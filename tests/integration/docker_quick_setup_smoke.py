"""Exercise the one-page quick setup workflow in a real NetBox environment."""

import atexit
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from users.models import ObjectPermission

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.models import (
    BackupPolicy,
    BackupTarget,
    ConnectionProfile,
    CredentialProfile,
    PlatformMapping,
    RemoteRetentionPolicy,
    RetentionPolicy,
    SftpReceiverProfile,
    StoredCredential,
)
from netbox_config_backup.services.quick_setup import create_quick_setup
from netbox_config_backup.services.ui_language import SESSION_KEY

# Every database mutation in this smoke test lives inside one outer
# transaction. The atexit rollback runs after both a successful script and an
# unhandled assertion, so no test users, encrypted credentials, permissions,
# devices, or plugin profiles persist in the NetBox database.
_smoke_transaction = transaction.atomic()
_smoke_transaction.__enter__()


def _rollback_smoke_transaction():
    connection = transaction.get_connection()
    if connection.in_atomic_block:
        transaction.set_rollback(True)
        _smoke_transaction.__exit__(None, None, None)


atexit.register(_rollback_smoke_transaction)


def make_device(prefix: str, *, platform: Platform) -> Device:
    site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
    manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
    device_type = DeviceType.objects.create(
        manufacturer=manufacturer,
        model=f"{prefix}-type",
        slug=f"{prefix}-type",
    )
    role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
    return Device.objects.create(
        name=f"{prefix}-device",
        site=site,
        role=role,
        device_type=device_type,
        platform=platform,
    )


prefix = f"ncb-quick-{uuid4().hex[:8]}"
platform = Platform.objects.create(name=f"{prefix}-platform", slug=f"{prefix}-platform")
PlatformMapping.objects.create(platform=platform, driver_id="fake", enabled=True)
device = make_device(prefix, platform=platform)
plaintext = f"quick-{uuid4().hex}"

user = get_user_model().objects.create_superuser(
    username=f"{prefix}-admin",
    password=uuid4().hex,
)
client = Client()
client.force_login(user)
session = client.session
session[SESSION_KEY] = "en"
session.save()

add_url = reverse("plugins:netbox_config_backup:backuptarget_add")
available_devices_url = reverse("plugins-api:netbox_config_backup-api:available-device-list")
response = client.get(add_url)
assert response.status_code == 200
assert b"Add device" in response.content
assert b"Save &amp; test connection" in response.content
assert b"Credential profile" in response.content
assert b"Connection profile" in response.content
assert b'id="new-credential-fields"' in response.content
assert b'id="existing-credential-note"' in response.content
assert b"Advanced settings" in response.content
assert b"Usually not required" in response.content
assert b"Technical defaults are applied automatically" in response.content
assert b'name="remote_retention_days"' in response.content
assert b"Use remote storage profile" in response.content
assert b"Allow the device to create and send a backup file" in response.content
assert b'<details class="card advanced-settings mt-4">' in response.content

response = client.post(
    add_url,
    {
        "device": device.pk,
        "driver_id": "",
        "protocol": "auto",
        "port": 2222,
        "host_key_policy": "strict",
        "username": "quick-user",
        "password": plaintext,
        "password_confirm": plaintext,
        "schedule": "6h",
        "retention_days": 90,
        "remote_retention_days": 365,
        "_create": "",
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors

target = BackupTarget.objects.select_related(
    "connection_override",
    "credential_override",
    "policy_override__retention_policy",
    "remote_retention_policy",
).get(device=device)

response = client.get(available_devices_url, {"q": device.name, "brief": "true"})
assert response.status_code == 200
assert all(item["id"] != device.pk for item in response.json()["results"])

available_device = make_device(f"{prefix}-available", platform=platform)
response = client.get(
    available_devices_url,
    {"q": available_device.name, "brief": "true"},
)
assert response.status_code == 200
assert any(item["id"] == available_device.pk for item in response.json()["results"])
assert target.driver_override == "fake"
assert target.connection_override.port == 2222
assert target.connection_override.verify_host_key is True
assert target.connection_override.auto_trust_first_host_key is False
assert target.policy_override.schedule_type == "interval"
assert target.policy_override.interval_minutes == 360
assert target.policy_override.retention_policy.daily_days == 90
assert target.remote_retention_policy is not None
assert target.remote_retention_policy.daily_days == 365
assert target.next_run_at is not None

stored = StoredCredential.objects.get(profile=target.credential_override)
assert plaintext.encode() not in bytes(stored.ciphertext)
material = secret_provider_registry.get("encrypted_database").resolve(
    target.credential_override.secret_reference
)
assert material.username == "quick-user"
assert material.password == plaintext

reused_device = make_device(f"{prefix}-reused", platform=platform)
connection_count = ConnectionProfile.objects.count()
credential_count = CredentialProfile.objects.count()
response = client.post(
    add_url,
    {
        "device": reused_device.pk,
        "driver_id": "",
        "connection_profile": target.connection_override_id,
        "credential_profile": target.credential_override_id,
        "protocol": "auto",
        "schedule": "daily",
        "retention_days": 30,
        "remote_retention_days": "",
        "_create": "",
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors
reused_target = BackupTarget.objects.get(device=reused_device)
assert reused_target.connection_override_id == target.connection_override_id
assert reused_target.credential_override_id == target.credential_override_id
assert reused_target.remote_retention_policy_id is None
assert ConnectionProfile.objects.count() == connection_count
assert CredentialProfile.objects.count() == credential_count

mapping = PlatformMapping.objects.get(platform=platform)
mapping.connection_profile = target.connection_override
mapping.credential_profile = target.credential_override
mapping.save(update_fields=("connection_profile", "credential_profile"))
automatic_device = make_device(f"{prefix}-automatic", platform=platform)
response = client.post(
    add_url,
    {
        "device": automatic_device.pk,
        "driver_id": "",
        "protocol": "auto",
        "schedule": "daily",
        "retention_days": 30,
        "remote_retention_days": "",
        "_create": "",
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors
automatic_target = BackupTarget.objects.get(device=automatic_device)
assert automatic_target.connection_override_id == target.connection_override_id
assert automatic_target.credential_override_id == target.credential_override_id
assert automatic_target.remote_retention_policy_id is None

# Quick Setup always creates a Local retention profile. A user without runtime
# and destructive-retention authority must not be able to assign it indirectly.
limited_user = get_user_model().objects.create_user(
    username=f"{prefix}-limited-admin",
    password=uuid4().hex,
)
limited_add_permission = ObjectPermission.objects.create(
    name=f"[Quick setup smoke] {prefix} add without FTP retention",
    actions=["add"],
    constraints=None,
)
limited_add_permission.object_types.set(
    ContentType.objects.get_for_models(
        BackupTarget,
        ConnectionProfile,
        CredentialProfile,
        StoredCredential,
        BackupPolicy,
        RetentionPolicy,
    ).values()
)
limited_user.object_permissions.add(limited_add_permission)
limited_client = Client()
limited_client.force_login(limited_user)
assert limited_client.get(add_url).status_code == 403

limited_device = make_device(f"{prefix}-limited", platform=platform)
limited_payload = {
    "device": limited_device.pk,
    "driver_id": "",
    "connection_profile": target.connection_override_id,
    "credential_profile": target.credential_override_id,
    "protocol": "auto",
    "schedule": "daily",
    "retention_days": 30,
    "remote_retention_days": "",
    "_create": "",
}
response = limited_client.post(add_url, limited_payload)
assert response.status_code == 403, response.status_code
assert not BackupTarget.objects.filter(device=limited_device).exists()

denied_remote_device = make_device(f"{prefix}-limited-ftp", platform=platform)
response = limited_client.post(
    add_url,
    {
        **limited_payload,
        "device": denied_remote_device.pk,
        "remote_retention_days": 90,
    },
)
assert response.status_code == 403, response.status_code
assert not BackupTarget.objects.filter(device=denied_remote_device).exists()

ceragon_platform = Platform.objects.create(
    name=f"{prefix}-ceragon-platform",
    slug=f"{prefix}-ceragon-platform",
)
receiver = SftpReceiverProfile.objects.create(
    name=f"{prefix}-receiver",
    credential_profile=target.credential_override,
    advertised_host="192.0.2.20",
)
PlatformMapping.objects.create(
    platform=ceragon_platform,
    driver_id="ceragon_ip50",
    connection_profile=target.connection_override,
    credential_profile=target.credential_override,
    receiver_profile=receiver,
    driver_options={
        "allow_device_export": True,
        "restore_point": "restore-point-2",
    },
    enabled=True,
)
ceragon_device = make_device(f"{prefix}-ceragon", platform=ceragon_platform)
response = client.post(
    add_url,
    {
        "device": ceragon_device.pk,
        "driver_id": "",
        "protocol": "auto",
        "schedule": "daily",
        "retention_days": 30,
        "_create": "",
    },
)
assert response.status_code == 200
assert b"Confirm the device-side backup export before saving." in response.content
assert not BackupTarget.objects.filter(device=ceragon_device).exists()

response = client.get(reverse("plugins:netbox_config_backup:advanced_settings"))
assert response.status_code == 200
assert b"Platform mappings" in response.content
assert b"Add device" not in response.content
assert b"Credential profiles" in response.content

response = client.post(
    add_url,
    {
        "device": device.pk,
        "driver_id": "fake",
        "protocol": "auto",
        "port": 22,
        "username": "duplicate",
        "password": plaintext,
        "password_confirm": plaintext,
        "schedule": "daily",
        "retention_days": 30,
    },
)
assert response.status_code == 200
assert BackupTarget.objects.filter(device=device).count() == 1
assert plaintext.encode() not in response.content

rollback_prefix = f"{prefix}-rollback"
rollback_device = make_device(rollback_prefix, platform=platform)
BackupTarget.objects.create(device=rollback_device, driver_override="fake")
connection_count = ConnectionProfile.objects.count()
credential_count = CredentialProfile.objects.count()
remote_retention_count = RemoteRetentionPolicy.objects.count()
try:
    create_quick_setup(
        device=rollback_device,
        driver_id="fake",
        port=22,
        verify_host_key=False,
        username="rollback-user",
        password=plaintext,
        schedule="12h",
        retention_days=365,
        remote_retention_days=730,
    )
except IntegrityError:
    pass
else:
    raise AssertionError("Duplicate target creation should fail.")
assert ConnectionProfile.objects.count() == connection_count
assert CredentialProfile.objects.count() == credential_count
assert RemoteRetentionPolicy.objects.count() == remote_retention_count

print(
    {
        "target": target.pk,
        "automatic_driver": target.driver_override,
        "encrypted_credential": True,
        "existing_profiles_reused": True,
        "mapped_profiles_applied": True,
        "native_export_requires_explicit_confirmation": True,
        "schedule_minutes": target.policy_override.interval_minutes,
        "retention_days": target.policy_override.retention_policy.daily_days,
        "remote_retention_days": target.remote_retention_policy.daily_days,
        "remote_retention_uses_storage_when_blank": True,
        "remote_retention_permission_is_conditional": True,
        "duplicate_validation": True,
        "atomic_rollback": True,
    }
)
