"""Exercise protected revisions and manual retention cleanup through NetBox/RQ."""

import hashlib
import time
from datetime import timedelta
from uuid import uuid4

from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    CredentialProfile,
    RetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.services.retention_cleanup import (
    RetentionCleanupError,
    execute_retention_cleanup,
)
from netbox_config_backup.services.target_deletion import delete_backup_target
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.local import LocalConfigStorage


def wait_for_job(job: Job, timeout: float = 60.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job.refresh_from_db()
        if job.status not in {"pending", "scheduled", "running"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"Job {job.pk} did not finish; current status={job.status}")


prefix = f"ncb-retention-cleanup-{uuid4().hex[:8]}"
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
retention = RetentionPolicy.objects.create(
    name=f"{prefix}-retention",
    keep_all_days=0,
    daily_days=0,
    weekly_weeks=0,
    monthly_months=0,
    minimum_changed_revisions=0,
    unchanged_run_days=1,
    changed_run_days=1,
    failed_run_days=1,
)
policy = BackupPolicy.objects.create(
    name=f"{prefix}-policy",
    schedule_type="daily",
    time_of_day="02:00",
    retry_backoff_minutes=[],
    retention_policy=retention,
)
target = BackupTarget.objects.create(
    device=device,
    driver_override="fake",
    policy_override=policy,
)
storage = LocalConfigStorage(settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"])
user_model = get_user_model()
admin = user_model.objects.create_superuser(username=f"{prefix}-admin")
limited = user_model.objects.create_user(username=f"{prefix}-limited")
client = Client()
client.force_login(admin)
cleanup_job = None
remote_credential = None
remote_destination = None
failed_replica = None


def create_revision(content: bytes, *, age_days: int, previous=None, protected=False):
    digest = hashlib.sha256(content).hexdigest()
    revision = ConfigRevision.objects.create(
        target=target,
        normalized_hash=digest,
        normalizer_version="1",
        driver_id="fake",
        previous_revision=previous,
        protected=protected,
    )
    created = timezone.now() - timedelta(days=age_days)
    ConfigRevision.objects.filter(pk=revision.pk).update(created=created)
    revision.refresh_from_db()
    key = f"retention-cleanup-smoke/{revision.revision_uuid}/running-config.txt"
    storage.put(key, content)
    artifact = ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="running_config",
        storage_key=key,
        size=len(content),
        raw_hash=digest,
        normalized_hash=digest,
        is_primary=True,
    )
    return revision, artifact


try:
    expired, expired_artifact = create_revision(b"hostname expired\n", age_days=90)
    extra_expired, extra_expired_artifact = create_revision(
        b"hostname extra-expired\n",
        age_days=80,
        previous=expired,
    )
    protected, protected_artifact = create_revision(
        b"hostname protected\n",
        age_days=60,
        previous=extra_expired,
        protected=True,
    )
    latest, latest_artifact = create_revision(
        b"hostname latest\n",
        age_days=0,
        previous=protected,
    )
    target.last_revision = latest
    target.save(update_fields=("last_revision", "last_updated"))

    # An exhausted failed repair can still point to an older complete copy or
    # an interrupted FTP upload. Local retention must remove only local bytes
    # and retain the revision/replica metadata needed for exact remote cleanup.
    remote_credential = CredentialProfile.objects.create(
        name=f"{prefix}-ftp-credentials",
        provider_id="environment",
        secret_reference="RETENTION_SMOKE_UNUSED",
        auth_type="password",
    )
    remote_destination = BackupDestination.objects.create(
        name=f"{prefix}-ftp",
        enabled=False,
        auto_replicate=False,
        protocol="ftp",
        allow_insecure_ftp=True,
        host="127.0.0.1",
        port=21,
        base_path="retention-smoke",
        credential_profile=remote_credential,
    )
    failed_replica = RevisionReplica.objects.create(
        revision=extra_expired,
        destination=remote_destination,
        status="failed",
        attempts=4,
        remote_path=(
            f"/retention-smoke/devices/{device.name}/backups/"
            f"{extra_expired.created.astimezone().strftime('%Y-%m-%d_%H-%M-%S')}"
            f"-r{extra_expired.pk}"
        ),
        remote_available=False,
        next_retry_at=None,
        error_code="DESTINATION_TEST_FAILURE",
    )

    expired_run = BackupRun.objects.create(
        target=target,
        status="success_changed",
        revision=expired,
        changed=True,
        finished_at=timezone.now() - timedelta(days=90),
    )
    BackupRun.objects.filter(pk=expired_run.pk).update(
        queued_at=timezone.now() - timedelta(days=90)
    )
    recent_run = BackupRun.objects.create(
        target=target,
        status="success_changed",
        revision=latest,
        changed=True,
        finished_at=timezone.now(),
    )

    protect_url = reverse(
        "plugins:netbox_config_backup:configrevision_set_protection",
        kwargs={"pk": expired.pk},
    )
    limited_client = Client()
    limited_client.force_login(limited)
    assert limited_client.post(protect_url, {"protected": "true"}).status_code == 403
    assert client.post(protect_url, {"protected": "invalid"}).status_code == 400
    response = client.post(protect_url, {"protected": "true"})
    assert response.status_code == 302
    expired.refresh_from_db()
    assert expired.protected
    response = client.post(protect_url, {"protected": "false"})
    assert response.status_code == 302
    expired.refresh_from_db()
    assert not expired.protected

    class FailingSecondStageStorage(LocalConfigStorage):
        def __init__(self, root):
            super().__init__(root)
            self.stage_calls = 0

        def stage_delete(self, key, namespace):
            self.stage_calls += 1
            if self.stage_calls == 2:
                raise StorageError("Simulated quarantine failure.")
            return super().stage_delete(key, namespace)

    try:
        execute_retention_cleanup(
            target.pk,
            storage=FailingSecondStageStorage(
                settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"]
            ),
        )
    except RetentionCleanupError:
        pass
    else:
        raise AssertionError("A staging failure must abort retention cleanup.")
    assert ConfigRevision.objects.filter(pk=expired.pk).exists()
    assert ConfigRevision.objects.filter(pk=extra_expired.pk).exists()
    assert storage.exists(expired_artifact.storage_key)
    assert storage.exists(extra_expired_artifact.storage_key)

    preview_url = reverse(
        "plugins:netbox_config_backup:backuptarget_retention_preview",
        kwargs={"pk": target.pk},
    )
    cleanup_url = reverse(
        "plugins:netbox_config_backup:backuptarget_retention_cleanup",
        kwargs={"pk": target.pk},
    )
    response = client.get(preview_url)
    assert response.status_code == 200
    assert b"Apply local retention" in response.content
    assert b"Local revisions expired" in response.content
    response = client.get(cleanup_url)
    assert response.status_code == 200
    assert b"Queue cleanup" in response.content

    assert limited_client.get(cleanup_url).status_code == 403

    response = client.post(cleanup_url, {"confirm": "on"})
    assert response.status_code == 302
    cleanup_job = wait_for_job(
        Job.objects.filter(
            name="Config backup retention cleanup",
            object_id=target.pk,
        ).latest("created")
    )
    assert cleanup_job.status == "completed", (cleanup_job.status, cleanup_job.data)

    assert not ConfigRevision.objects.filter(pk=expired.pk).exists()
    assert ConfigRevision.objects.filter(pk=extra_expired.pk).exists()
    assert RevisionReplica.objects.filter(pk=failed_replica.pk).exists()
    assert ConfigRevision.objects.filter(pk=protected.pk, protected=True).exists()
    assert ConfigRevision.objects.filter(pk=latest.pk).exists()
    assert not ConfigArtifact.objects.filter(pk=expired_artifact.pk).exists()
    extra_expired_artifact.refresh_from_db()
    assert not extra_expired_artifact.local_available
    assert ConfigArtifact.objects.filter(pk=protected_artifact.pk).exists()
    assert ConfigArtifact.objects.filter(pk=latest_artifact.pk).exists()
    assert not storage.exists(expired_artifact.storage_key)
    assert not storage.exists(extra_expired_artifact.storage_key)
    assert storage.exists(protected_artifact.storage_key)
    assert storage.exists(latest_artifact.storage_key)
    assert not BackupRun.objects.filter(pk=expired_run.pk).exists()
    assert BackupRun.objects.filter(pk=recent_run.pk).exists()
    latest.refresh_from_db()
    target.refresh_from_db()
    assert latest.previous_revision_id == protected.pk
    assert target.last_revision_id == latest.pk

    print(
        {
            "job_status": cleanup_job.status,
            "expired_revision_deleted": True,
            "expired_run_deleted": True,
            "artifact_deleted": True,
            "protected_revision_kept": True,
            "latest_revision_kept": True,
            "revision_chain_relinked": True,
            "failed_remote_pointer_preserved": True,
            "staging_failure_rolled_back": True,
            "permission_denied_without_delete_access": True,
        }
    )
finally:
    if failed_replica is not None:
        RevisionReplica.objects.filter(pk=failed_replica.pk).delete()
    if BackupTarget.objects.filter(pk=target.pk).exists():
        target.refresh_from_db()
        delete_backup_target(target)
    if cleanup_job is not None:
        cleanup_job.delete()
    admin.delete()
    limited.delete()
    policy.delete()
    retention.delete()
    if remote_destination is not None:
        remote_destination.delete()
    if remote_credential is not None:
        remote_credential.delete()
    device.delete()
    device_type.delete()
    manufacturer.delete()
    role.delete()
    site.delete()
