"""Verify RevisionReplica -> temporary ZIP -> authorized HTTP download.

Run with ``manage.py shell`` while the dedicated backup worker is running.
The script uses an existing successful FTP replica, performs read-only remote
downloads, verifies the ZIP, and removes only its temporary local package and
test Job record afterwards. It never opens a device connection.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.jobs import BACKUP_QUEUE, FtpRecoveryPackageJob
from netbox_config_backup.models import BackupRun, ConfigArtifact, RevisionReplica
from netbox_config_backup.services.ftp_recovery import validate_recovery_package

TIMEOUT_SECONDS = 60


replica = (
    RevisionReplica.objects.filter(status="success", destination__protocol="ftp")
    .select_related("destination", "revision__target__device")
    .prefetch_related("revision__artifacts")
    .order_by("-finished_at")
    .first()
)
if replica is None:
    raise AssertionError("The FTP recovery smoke test needs one successful FTP replica.")

revision = replica.revision
user = get_user_model().objects.filter(is_active=True, is_superuser=True).first()
if user is None:
    raise AssertionError("The FTP recovery smoke test needs an active superuser.")

run_count_before = BackupRun.objects.count()
token = uuid4()
job = None
package_path = None

try:
    job = FtpRecoveryPackageJob.enqueue(
        replica_id=replica.pk,
        package_token=str(token),
        instance=revision.target,
        user=user,
        queue_name=BACKUP_QUEUE,
    )
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        job.refresh_from_db()
        if job.status in {"completed", "failed", "errored"}:
            break
        time.sleep(1)
    if job.status != "completed":
        raise AssertionError(
            {
                "status": job.status,
                "data": job.data,
                "job_id": str(job.job_id),
            }
        )

    result = job.data["ftp_recovery_package"]
    package_path = validate_recovery_package(
        storage_root=settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"],
        package_token=result["token"],
        expected_size=result["size"],
        expected_sha256=result["sha256"],
    )
    client = Client(HTTP_HOST="localhost")
    client.force_login(user)
    revision_response = client.get(revision.get_absolute_url())
    assert revision_response.status_code == 200
    assert b"Prepare verified ZIP" in revision_response.content
    result_response = client.get(
        reverse(
            "plugins:netbox_config_backup:configrevision_ftp_recovery_result",
            kwargs={"pk": revision.pk, "job_id": job.job_id},
        )
    )
    assert result_response.status_code == 200
    assert b"This task reads the existing FTP copy only" in result_response.content
    status_response = client.get(
        reverse(
            "plugins:netbox_config_backup:configrevision_ftp_recovery_status",
            kwargs={"pk": revision.pk, "job_id": job.job_id},
        )
    )
    assert status_response.status_code == 200
    assert status_response.json()["ready"] is True
    response = client.get(
        reverse(
            "plugins:netbox_config_backup:configrevision_ftp_recovery_download",
            kwargs={"pk": revision.pk, "job_id": job.job_id},
        )
    )
    assert response.status_code == 200, response.status_code
    zip_bytes = b"".join(response.streaming_content)
    assert len(zip_bytes) == result["size"]
    assert hashlib.sha256(zip_bytes).hexdigest() == result["sha256"]

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        manifest_name = next(
            name for name in archive.namelist() if name.endswith("/_netbox_manifest.json")
        )
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["revision_uuid"] == str(revision.revision_uuid)
        archive_prefix = manifest_name.rsplit("/", 1)[0]
        expected_artifacts = {
            artifact.artifact_type: artifact
            for artifact in ConfigArtifact.objects.filter(revision=revision)
        }
        assert set(expected_artifacts) == {item["artifact_type"] for item in manifest["artifacts"]}
        for item in manifest["artifacts"]:
            content = archive.read(f"{archive_prefix}/{item['filename']}")
            artifact = expected_artifacts[item["artifact_type"]]
            assert len(content) == artifact.size == item["size"]
            assert hashlib.sha256(content).hexdigest() == artifact.raw_hash == item["sha256"]
        readme = archive.read(f"{archive_prefix}/RECOVERY_README.txt").decode()
        assert "manual recovery only" in readme
        assert "does not import, restore, or apply" in readme

    job.refresh_from_db()
    audit = job.data["ftp_recovery_package"]
    assert audit["download_count"] == 1
    assert audit["downloads"][-1]["user_id"] == user.pk
    assert BackupRun.objects.count() == run_count_before
    print(
        json.dumps(
            {
                "marker": "FTP_RECOVERY_SMOKE_OK",
                "revision_uuid": str(revision.revision_uuid),
                "replica_id": replica.pk,
                "destination": replica.destination.name,
                "verified_ftp_files": result["file_count"],
                "verified_source_bytes": result["verified_bytes"],
                "zip_bytes": result["size"],
                "download_audit_count": audit["download_count"],
                "device_connections_created": 0,
            },
            sort_keys=True,
        )
    )
finally:
    if package_path is not None:
        package_path.unlink(missing_ok=True)
    if job is not None:
        job.delete()
