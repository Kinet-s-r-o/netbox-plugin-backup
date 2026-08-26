"""Exercise FTP replication and remote retention against the configured server.

Run with ``manage.py shell`` inside the NetBox container. The script never
changes the scheduler or an existing target/destination. It creates one unique
target, two local revisions, and two FTP copies, expires only the older copy,
verifies the database tombstone and remote state, then removes its own data.
"""

from __future__ import annotations

import ftplib
import hashlib
import json
import posixpath
from datetime import timedelta
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.utils import timezone

from netbox_config_backup.choices import ReplicaStatusChoices
from netbox_config_backup.models import (
    BackupDestination,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    RemoteRetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.services.destination_ftp import (
    _connect,
    _read_remote,
    replicate_revision_ftp,
)
from netbox_config_backup.services.remote_retention_cleanup import (
    execute_remote_retention_cleanup,
)
from netbox_config_backup.services.replication import _mark_running
from netbox_config_backup.services.target_deletion import delete_backup_target
from netbox_config_backup.storage import build_config_storage


def _remote_directory_exists(destination, remote_path: str) -> bool:
    ftp = _connect(destination)
    try:
        try:
            ftp.cwd(remote_path)
        except ftplib.error_perm as exc:
            if str(exc).lstrip().startswith("550"):
                return False
            raise
        return True
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()


destination = (
    BackupDestination.objects.filter(enabled=True, protocol="ftp")
    .select_related("credential_profile")
    .order_by("pk")
    .first()
)
assert destination is not None, "Create and test an enabled FTP destination first."

marker = uuid4().hex[:10]
# Keep the generated path deliberately short. Some Windows FTP servers still
# enforce MAX_PATH on their physical root while the plugin adds an immutable
# temporary suffix during upload.
prefix = f"ncbrr-{marker}"
now = timezone.now()
storage = build_config_storage()
manufacturer = site = role = device_type = device = policy = target = None
old_replica = latest_replica = None


def _create_revision(label: str, *, created, protected: bool, previous=None):
    content = f"hostname {prefix}-{label}\n".encode()
    digest = hashlib.sha256(content).hexdigest()
    revision = ConfigRevision.objects.create(
        target=target,
        normalized_hash=digest,
        normalizer_version="1",
        driver_id="fake",
        content_changed=True,
        protected=protected,
        previous_revision=previous,
    )
    ConfigRevision.objects.filter(pk=revision.pk).update(created=created)
    revision.refresh_from_db()
    storage_key = f"remote-retention-smoke/{marker}/{revision.revision_uuid}/config.txt"
    storage.put(storage_key, content)
    ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="running_config",
        format="text",
        storage_key=storage_key,
        size=len(content),
        raw_hash=digest,
        normalized_hash=digest,
        is_primary=True,
    )
    revision = ConfigRevision.objects.prefetch_related("artifacts").get(pk=revision.pk)
    replica = RevisionReplica.objects.create(
        revision=revision,
        destination=destination,
        status=ReplicaStatusChoices.PENDING,
    )
    replica = _mark_running(replica.pk)
    assert replica.remote_path, "FTP path must be recorded before the first network write."
    transfer = replicate_revision_ftp(
        destination,
        revision,
        recorded_remote_path=replica.remote_path,
    )
    replica.status = ReplicaStatusChoices.SUCCESS
    replica.finished_at = now
    replica.next_retry_at = None
    replica.bytes_transferred = transfer.bytes_transferred
    replica.remote_path = transfer.remote_path
    replica.remote_available = True
    replica.save()
    return revision, replica, storage_key, content


try:
    manufacturer = Manufacturer.objects.create(name=prefix, slug=prefix)
    site = Site.objects.create(name=prefix, slug=prefix)
    role = DeviceRole.objects.create(name=prefix, slug=prefix)
    device_type = DeviceType.objects.create(
        manufacturer=manufacturer,
        model=prefix,
        slug=prefix,
    )
    device = Device.objects.create(
        name=prefix,
        site=site,
        role=role,
        device_type=device_type,
    )
    policy = RemoteRetentionPolicy.objects.create(
        name=prefix,
        keep_all_days=0,
        daily_days=0,
        weekly_weeks=0,
        monthly_months=0,
        minimum_changed_revisions=0,
        max_copies_per_target=100,
    )
    target = BackupTarget.objects.create(
        device=device,
        driver_override="fake",
        remote_retention_policy=policy,
    )

    old_revision, old_replica, old_key, old_content = _create_revision(
        "old",
        created=now - timedelta(days=90),
        protected=False,
    )
    latest_revision, latest_replica, latest_key, latest_content = _create_revision(
        "latest-protected",
        created=now,
        protected=True,
        previous=old_revision,
    )
    target.last_revision = latest_revision
    target.save(update_fields=("last_revision", "last_updated"))

    assert _remote_directory_exists(destination, old_replica.remote_path)
    assert _remote_directory_exists(destination, latest_replica.remote_path)
    assert storage.get(old_key) == old_content
    assert storage.get(latest_key) == latest_content

    # Simulate an exhausted repair which retained the immutable path but no
    # longer claims that a complete remote copy is available. Retention must
    # still remove this exact possible remnant instead of orphaning it.
    old_replica.status = ReplicaStatusChoices.FAILED
    old_replica.remote_available = False
    old_replica.error_code = "SMOKE_FAILED_REPAIR"
    old_replica.error_message = "Intentional remote-retention smoke state."
    old_replica.next_retry_at = None
    old_replica.save()

    summary = execute_remote_retention_cleanup(target.pk, now=now)
    old_replica.refresh_from_db()
    latest_replica.refresh_from_db()

    assert summary.revision_count == 1, summary
    assert summary.replica_count == 1, summary
    assert old_replica.status == ReplicaStatusChoices.FAILED
    assert old_replica.remote_available is False
    assert old_replica.remote_deleted_at is not None
    assert latest_replica.status == ReplicaStatusChoices.SUCCESS
    assert latest_replica.remote_available is True
    assert latest_replica.remote_deleted_at is None
    assert not _remote_directory_exists(destination, old_replica.remote_path)

    ftp = _connect(destination)
    try:
        manifest = _read_remote(
            ftp,
            posixpath.join(latest_replica.remote_path, "_netbox_manifest.json"),
            1024 * 1024,
        )
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()
    assert json.loads(manifest.decode("utf-8"))["revision_uuid"] == str(
        latest_revision.revision_uuid
    )
    assert storage.get(old_key) == old_content
    assert storage.get(latest_key) == latest_content
    assert ConfigRevision.objects.filter(pk=old_revision.pk).exists()
    assert ConfigRevision.objects.filter(pk=latest_revision.pk, protected=True).exists()

    print(
        json.dumps(
            {
                "marker": "FTP_REMOTE_RETENTION_SMOKE_OK",
                "destination": destination.name,
                "deleted_revision_uuid": str(old_revision.revision_uuid),
                "retained_revision_uuid": str(latest_revision.revision_uuid),
                "tombstone_recorded": True,
                "failed_repair_path_cleaned": True,
                "latest_protected_remote_intact": True,
                "local_artifacts_intact": True,
            },
            sort_keys=True,
        )
    )
finally:
    if target and BackupTarget.objects.filter(pk=target.pk).exists():
        target.refresh_from_db()
        delete_backup_target(target)
    if policy:
        RemoteRetentionPolicy.objects.filter(pk=policy.pk).delete()
    if device:
        Device.objects.filter(pk=device.pk).delete()
    if device_type:
        DeviceType.objects.filter(pk=device_type.pk).delete()
    if manufacturer:
        Manufacturer.objects.filter(pk=manufacturer.pk).delete()
    if role:
        DeviceRole.objects.filter(pk=role.pk).delete()
    if site:
        Site.objects.filter(pk=site.pk).delete()
