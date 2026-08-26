"""Exercise BackupRun -> ConfigRevision -> FTP inside a NetBox container.

Run with ``manage.py shell`` while the dedicated backup worker is running. The
script creates an isolated FakeDriver target, waits for the asynchronous FTP
replica, verifies the remote manifest and every artifact hash, and removes all
test data afterwards.
"""

from __future__ import annotations

import ftplib
import hashlib
import json
import posixpath
import time
from datetime import time as clock_time
from pathlib import PurePosixPath
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.utils import timezone

from netbox_config_backup.choices import ReplicaStatusChoices, RunStatusChoices
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    PlatformMapping,
    RetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.services.destination import DestinationError
from netbox_config_backup.services.destination_ftp import _connect, _read_remote
from netbox_config_backup.services.destination_paths import ftp_revision_destination_path
from netbox_config_backup.services.runtime import build_backup_pipeline
from netbox_config_backup.services.target_deletion import delete_backup_target

POLL_SECONDS = 1
REPLICA_TIMEOUT_SECONDS = 45


def _select_destination() -> BackupDestination:
    destinations = list(BackupDestination.objects.filter(enabled=True, auto_replicate=True))
    if len(destinations) != 1 or destinations[0].protocol != "ftp":
        raise AssertionError(
            "The FTP E2E smoke test requires exactly one enabled automatic "
            "destination and it must use FTP."
        )
    return destinations[0]


def _wait_for_replica(revision: ConfigRevision, destination: BackupDestination) -> RevisionReplica:
    deadline = time.monotonic() + REPLICA_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        replica = RevisionReplica.objects.filter(revision=revision, destination=destination).first()
        if replica and replica.status in {
            ReplicaStatusChoices.SUCCESS,
            ReplicaStatusChoices.FAILED,
        }:
            return replica
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"FTP replica did not finish within {REPLICA_TIMEOUT_SECONDS} seconds.")


def _verify_remote_revision(
    destination: BackupDestination,
    revision: ConfigRevision,
    replica: RevisionReplica,
) -> tuple[int, int, str]:
    artifacts = list(revision.artifacts.order_by("artifact_type"))
    expected_path = ftp_revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        created_at=revision.created,
    )
    assert replica.remote_path == expected_path, (replica.remote_path, expected_path)

    ftp = _connect(destination)
    try:
        manifest_bytes = _read_remote(
            ftp,
            posixpath.join(expected_path, "_netbox_manifest.json"),
            1024 * 1024,
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        assert manifest["schema"] == 2
        assert manifest["revision_uuid"] == str(revision.revision_uuid)
        assert manifest["device_id"] == revision.target.device_id
        assert manifest["device_name"] == revision.target.device.name
        assert manifest["device_directory"] == PurePosixPath(expected_path).parts[-3]
        assert manifest["driver_id"] == revision.driver_id

        manifest_artifacts = {item["artifact_type"]: item for item in manifest["artifacts"]}
        assert set(manifest_artifacts) == {item.artifact_type for item in artifacts}

        verified_bytes = 0
        for artifact in artifacts:
            item = manifest_artifacts[artifact.artifact_type]
            assert item["artifact_type"] == artifact.artifact_type
            assert item["format"] == artifact.format
            assert item["size"] == artifact.size
            assert item["sha256"] == artifact.raw_hash
            assert item["primary"] == artifact.is_primary
            expected_filename = item["filename"]
            assert expected_filename.startswith(f"{revision.target.device.name}_")
            content = _read_remote(
                ftp,
                posixpath.join(expected_path, expected_filename),
                artifact.size,
            )
            assert len(content) == artifact.size
            assert hashlib.sha256(content).hexdigest() == artifact.raw_hash
            verified_bytes += len(content)
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()

    return len(artifacts), verified_bytes, expected_path


def _remove_remote_revision(destination: BackupDestination, revision_path: str | None) -> None:
    if not revision_path:
        return
    try:
        ftp = _connect(destination)
    except DestinationError:
        return
    try:
        try:
            entries = ftp.nlst(revision_path)
        except ftplib.all_errors:
            entries = []
        prefix = revision_path.rstrip("/") + "/"
        for entry in entries:
            if entry.startswith("/"):
                absolute_entry = entry
            elif "/" in entry:
                absolute_entry = "/" + entry.lstrip("/")
            else:
                absolute_entry = posixpath.join(revision_path, entry)
            if (
                absolute_entry.startswith(prefix)
                and posixpath.dirname(absolute_entry) == revision_path
            ):
                try:
                    ftp.delete(absolute_entry)
                except ftplib.all_errors:
                    continue

        cleanup_paths = (
            revision_path,
            posixpath.dirname(revision_path),
            posixpath.dirname(posixpath.dirname(revision_path)),
        )
        for path in cleanup_paths:
            try:
                ftp.rmd(path)
            except ftplib.all_errors:
                continue
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()


destination = _select_destination()
prefix = f"ncb-ftp-e2e-{uuid4().hex[:8]}"
target = None
revision_path = None
site = manufacturer = device_type = role = platform = device = None
retention = policy = mapping = None

try:
    site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
    manufacturer = Manufacturer.objects.create(
        name=f"{prefix}-manufacturer", slug=f"{prefix}-manufacturer"
    )
    device_type = DeviceType.objects.create(
        manufacturer=manufacturer,
        model=f"{prefix}-device-type",
        slug=f"{prefix}-device-type",
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
    retention = RetentionPolicy.objects.create(name=f"{prefix}-retention")
    policy = BackupPolicy.objects.create(
        name=f"{prefix}-policy",
        schedule_type="daily",
        time_of_day=clock_time(3, 0),
        store_mode="changed_only",
        retention_policy=retention,
    )
    mapping = PlatformMapping.objects.create(
        platform=platform,
        driver_id="fake",
        driver_options={
            "config": (
                f"hostname {prefix}-device\n"
                "interface Loopback0\n"
                " description FTP end-to-end smoke test\n"
            )
        },
    )
    target = BackupTarget.objects.create(device=device, policy_override=policy)

    run = BackupRun.objects.create(target=target)
    result = build_backup_pipeline().execute(run.pk)
    assert result.status == RunStatusChoices.SUCCESS_CHANGED, result
    assert result.revision_id is not None

    revision = ConfigRevision.objects.get(pk=result.revision_id)
    assert revision.target_id == target.pk
    assert ConfigArtifact.objects.filter(revision=revision).exists()
    revision_path = ftp_revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        created_at=revision.created,
    )

    replica = _wait_for_replica(revision, destination)
    assert replica.status == ReplicaStatusChoices.SUCCESS, {
        "status": replica.status,
        "error_code": replica.error_code,
        "error_message": replica.error_message,
    }

    artifact_count, verified_bytes, revision_path = _verify_remote_revision(
        destination, revision, replica
    )
    initial_attempts = replica.attempts

    healthy_run = BackupRun.objects.create(target=target)
    healthy_result = build_backup_pipeline().execute(healthy_run.pk)
    assert healthy_result.status == RunStatusChoices.SUCCESS_UNCHANGED, healthy_result
    replica.refresh_from_db()
    assert replica.attempts == initial_attempts

    # Simulate an FTP server which was emptied while NetBox still has a
    # successful replica record. An unchanged backup must detect the missing
    # remote copy and heal it without creating another ConfigRevision.
    _remove_remote_revision(destination, revision_path)
    repair_run = BackupRun.objects.create(target=target)
    repair_result = build_backup_pipeline().execute(repair_run.pk)
    assert repair_result.status == RunStatusChoices.SUCCESS_UNCHANGED, repair_result
    assert ConfigRevision.objects.filter(target=target).count() == 1

    repaired_replica = _wait_for_replica(revision, destination)
    assert repaired_replica.status == ReplicaStatusChoices.SUCCESS, {
        "status": repaired_replica.status,
        "error_code": repaired_replica.error_code,
        "error_message": repaired_replica.error_message,
    }
    assert repaired_replica.attempts == initial_attempts + 1
    artifact_count, verified_bytes, revision_path = _verify_remote_revision(
        destination, revision, repaired_replica
    )
    print(
        json.dumps(
            {
                "marker": "FTP_E2E_SMOKE_OK",
                "destination": destination.name,
                "run_status": result.status,
                "healthy_unchanged_run_status": healthy_result.status,
                "repair_run_status": repair_result.status,
                "missing_remote_copy_repaired": True,
                "revision_uuid": str(revision.revision_uuid),
                "replica_status": repaired_replica.status,
                "artifact_count": artifact_count,
                "verified_bytes": verified_bytes,
                "remote_path": revision_path,
            },
            sort_keys=True,
        )
    )
finally:
    try:
        _remove_remote_revision(destination, revision_path)
    finally:
        if target and BackupTarget.objects.filter(pk=target.pk).exists():
            BackupRun.objects.filter(
                target=target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).update(
                status=RunStatusChoices.FAILED,
                finished_at=timezone.now(),
                error_code="E2E_TEST_CLEANUP",
                error_message="The isolated E2E test was interrupted and cleaned up.",
            )
            delete_backup_target(target)
        if mapping:
            PlatformMapping.objects.filter(pk=mapping.pk).delete()
        if device:
            Device.objects.filter(pk=device.pk).delete()
        if platform:
            Platform.objects.filter(pk=platform.pk).delete()
        if policy:
            BackupPolicy.objects.filter(pk=policy.pk).delete()
        if retention:
            RetentionPolicy.objects.filter(pk=retention.pk).delete()
        if device_type:
            DeviceType.objects.filter(pk=device_type.pk).delete()
        if manufacturer:
            Manufacturer.objects.filter(pk=manufacturer.pk).delete()
        if role:
            DeviceRole.objects.filter(pk=role.pk).delete()
        if site:
            Site.objects.filter(pk=site.pk).delete()
