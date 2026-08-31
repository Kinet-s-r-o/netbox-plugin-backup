"""Verify confirmed deletion of one revision from every storage and the database."""

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from netbox_config_backup.choices import (
    DestinationProtocolChoices,
    ReplicaStatusChoices,
    RunStatusChoices,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    RevisionReplica,
)
from netbox_config_backup.services.revision_deletion import (
    RevisionDeletionError,
    delete_config_revision_everywhere,
)
from netbox_config_backup.services.target_deletion import delete_backup_target
from netbox_config_backup.storage.local import LocalConfigStorage

prefix = f"ncb-revision-delete-{uuid4().hex[:8]}"
storage = LocalConfigStorage(
    settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"]
)
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
device = Device.objects.create(
    name=f"{prefix}-device",
    site=site,
    role=role,
    device_type=device_type,
)
target = BackupTarget.objects.create(device=device, driver_override="fake")
user = None
destination = BackupDestination.objects.create(
    name=f"{prefix}-nfs",
    protocol=DestinationProtocolChoices.NFS,
    auto_replicate=False,
    host="",
    port=None,
    base_path=prefix,
    mount_path="/mnt/config-backup-revision-delete-smoke",
    credential_profile=None,
    connect_timeout=None,
)


def create_revision(label, *, previous=None, protected=False):
    content = f"hostname {prefix}\nrevision {label}\n".encode()
    digest = sha256(content).hexdigest()
    revision = ConfigRevision.objects.create(
        target=target,
        normalized_hash=digest,
        normalizer_version="smoke-v1",
        driver_id="fake",
        content_changed=True,
        protected=protected,
        label=label,
        previous_revision=previous,
    )
    key = f"devices/{target.pk}/revisions/{revision.revision_uuid}/configuration.txt"
    storage.put(key, content)
    artifact = ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="configuration_dump",
        format="text",
        storage_key=key,
        size=len(content),
        raw_hash=digest,
        normalized_hash=digest,
        is_primary=True,
    )
    return revision, artifact, Path(storage.root, key)


try:
    first, _first_artifact, first_path = create_revision("first")
    deleted, deleted_artifact, deleted_path = create_revision(
        "delete-me",
        previous=first,
        protected=True,
    )
    latest, _latest_artifact, latest_path = create_revision("latest", previous=deleted)
    target.last_revision = latest
    target.save(update_fields=("last_revision", "last_updated"))
    run = BackupRun.objects.create(
        target=target,
        status=RunStatusChoices.SUCCESS_CHANGED,
        revision=deleted,
        changed=True,
        raw_changed=True,
    )
    replica = RevisionReplica.objects.create(
        revision=deleted,
        destination=destination,
        status=ReplicaStatusChoices.SUCCESS,
        remote_path=f"{prefix}/{device.name}/{deleted.revision_uuid}",
        remote_available=True,
    )
    deleted_id = deleted.pk
    deleted_artifact_id = deleted_artifact.pk
    replica_id = replica.pk

    try:
        delete_config_revision_everywhere(deleted.pk, storage=storage)
    except RevisionDeletionError as exc:
        assert "protected" in str(exc).lower(), exc
    else:
        raise AssertionError("A protected revision must not be deleted.")
    assert deleted_path.is_file()
    assert ConfigRevision.objects.filter(pk=deleted_id).exists()

    deleted.protected = False
    deleted.save(update_fields=("protected", "last_updated"))
    user = get_user_model().objects.create_superuser(
        username=f"{prefix}-admin",
        password=uuid4().hex,
    )
    client = Client()
    client.force_login(user)
    delete_url = reverse(
        "plugins:netbox_config_backup:configrevision_delete_everywhere",
        kwargs={"pk": deleted_id},
    )
    response = client.get(delete_url)
    assert response.status_code == 200
    assert b"Delete revision everywhere" in response.content
    assert destination.name.encode() in response.content

    response = client.post(delete_url, {})
    assert response.status_code == 400
    assert ConfigRevision.objects.filter(pk=deleted_id).exists()

    remote_result = SimpleNamespace(
        deleted_file_count=2,
        missing_file_count=0,
    )
    with patch(
        "netbox_config_backup.services.revision_deletion.delete_revision_replica",
        return_value=remote_result,
    ) as remote_delete:
        response = client.post(delete_url, {"confirm": "yes"})
    assert response.status_code == 302
    expected_location = (
        f"{reverse('plugins:netbox_config_backup:configrevision_list')}"
        f"?target_id={target.pk}"
    )
    response_messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.headers["Location"] == expected_location, (
        response.headers["Location"],
        response_messages,
    )
    remote_delete.assert_called_once()

    assert not ConfigRevision.objects.filter(pk=deleted_id).exists()
    assert not ConfigArtifact.objects.filter(pk=deleted_artifact_id).exists()
    assert not RevisionReplica.objects.filter(pk=replica_id).exists()
    assert not deleted_path.exists()
    assert first_path.is_file()
    assert latest_path.is_file()

    run.refresh_from_db()
    latest.refresh_from_db()
    target.refresh_from_db()
    assert run.revision_id is None
    assert latest.previous_revision_id == first.pk
    assert target.last_revision_id == latest.pk

    print(
        {
            "revision_deleted": True,
            "database_dependants_deleted": True,
            "local_file_deleted": True,
            "remote_delete_called": True,
            "protected_revision_blocked": True,
            "run_audit_preserved": True,
            "revision_chain_relinked": True,
        }
    )
finally:
    # The transport is mocked, so remove any surviving test replica metadata
    # before the target cleanup if an assertion interrupted the tested action.
    RevisionReplica.objects.filter(revision__target=target).delete()
    if BackupTarget.objects.filter(pk=target.pk).exists():
        target.refresh_from_db()
        delete_backup_target(target)
    if user is not None:
        get_user_model().objects.filter(pk=user.pk).delete()
    BackupDestination.objects.filter(pk=destination.pk).delete()
    Device.objects.filter(pk=device.pk).delete()
    DeviceType.objects.filter(pk=device_type.pk).delete()
    Manufacturer.objects.filter(pk=manufacturer.pk).delete()
    DeviceRole.objects.filter(pk=role.pk).delete()
    Site.objects.filter(pk=site.pk).delete()
