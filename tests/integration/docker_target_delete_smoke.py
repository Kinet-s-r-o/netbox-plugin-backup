"""Verify complete and safe deletion of a backup target with history."""

from pathlib import Path
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import (
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    StoredCredential,
)
from netbox_config_backup.services.quick_setup import create_quick_setup
from netbox_config_backup.services.runtime import build_backup_pipeline
from netbox_config_backup.services.target_deletion import (
    TargetDeletionError,
    delete_backup_target,
)
from netbox_config_backup.storage.base import StorageError

prefix = f"ncb-delete-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
platform = Platform.objects.create(name=f"{prefix}-platform", slug=f"{prefix}-platform")
device = Device.objects.create(
    name=f"{prefix}-device",
    site=site,
    role=role,
    device_type=device_type,
    platform=platform,
)

setup = create_quick_setup(
    device=device,
    driver_id="fake",
    port=22,
    verify_host_key=False,
    username="delete-test-user",
    password=f"delete-{uuid4().hex}",
    schedule="daily",
    retention_days=30,
)
target = setup.target
target_id = target.pk
connection_id = setup.connection_profile.pk
credential_id = setup.credential_profile.pk

run = BackupRun.objects.create(target=target)
result = build_backup_pipeline().execute(run.pk)
assert result.status == "success_changed", result
artifact = ConfigArtifact.objects.get(revision__target=target)
artifact_path = Path(
    settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"],
    artifact.storage_key,
)
assert artifact_path.is_file()


class FailingStorage:
    def stage_delete(self, key, namespace):
        raise StorageError()


try:
    delete_backup_target(target, storage=FailingStorage())
except TargetDeletionError:
    pass
else:
    raise AssertionError("Storage failure should abort target deletion.")
assert BackupTarget.objects.filter(pk=target_id).exists()
assert artifact_path.is_file()

user = get_user_model().objects.create_superuser(
    username=f"{prefix}-admin",
    password=uuid4().hex,
)
client = Client()
client.force_login(user)
plugin_root_url = reverse("plugins:netbox_config_backup:root")
overview_url = reverse("plugins:netbox_config_backup:home")
assert overview_url.endswith("/overview/")
root_response = client.get(plugin_root_url)
assert root_response.status_code == 302
assert root_response.headers["Location"] == overview_url
delete_url = reverse(
    "plugins:netbox_config_backup:backuptarget_delete",
    kwargs={"pk": target_id},
)
response = client.get(delete_url)
assert response.status_code == 200
assert b"backup run" in response.content.lower()
assert b"config revision" in response.content.lower()
assert b"config artifact" in response.content.lower()

response = client.post(
    delete_url,
    {
        "confirm": "on",
    },
)
assert response.status_code == 302
assert response.headers["Location"] == reverse(
    "plugins:netbox_config_backup:backuptarget_list"
)
assert not BackupTarget.objects.filter(pk=target_id).exists()
assert not BackupRun.objects.filter(target_id=target_id).exists()
assert not ConfigRevision.objects.filter(target_id=target_id).exists()
assert not ConfigArtifact.objects.filter(revision__target_id=target_id).exists()
assert not ConnectionProfile.objects.filter(pk=connection_id).exists()
assert not CredentialProfile.objects.filter(pk=credential_id).exists()
assert not StoredCredential.objects.filter(profile_id=credential_id).exists()
assert Device.objects.filter(pk=device.pk).exists()
assert not artifact_path.exists()

print(
    {
        "target_deleted": True,
        "redirected_to_device_list": True,
        "history_deleted": True,
        "artifact_file_deleted": True,
        "quick_profiles_deleted": True,
        "device_preserved": True,
        "storage_failure_was_atomic": True,
        "overview_uses_distinct_url": True,
    }
)
