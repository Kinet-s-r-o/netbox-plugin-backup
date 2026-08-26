"""Verify opt-in automatic retention dispatching and safety skips."""

import hashlib
import time
from datetime import timedelta
from uuid import uuid4

from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from netbox_config_backup.jobs import (
    BACKUP_QUEUE,
    ScheduledRetentionDispatcherJob,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    OperationalSettings,
    RetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.services.destination_paths import ftp_revision_destination_path
from netbox_config_backup.services.retention import (
    RevisionCandidate,
    RunCandidate,
    build_retention_plan,
    effective_retention_policy,
    settings_from_policy,
)
from netbox_config_backup.services.retention_dispatcher import CLEANUP_JOB_NAME
from netbox_config_backup.services.target_deletion import delete_backup_target
from netbox_config_backup.storage.local import LocalConfigStorage


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))


class Runner:
    def __init__(self):
        self.logger = RecordingLogger()


def wait_for_job(job: Job, timeout: float = 60.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job.refresh_from_db()
        if job.status not in {"pending", "scheduled", "running"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"Job {job.pk} did not finish; current status={job.status}")


def current_plan(target, now):
    target = BackupTarget.objects.select_related(
        "retention_override",
        "policy_override__retention_policy",
    ).get(pk=target.pk)
    policy = effective_retention_policy(target)
    if policy is None:
        return None
    revisions = list(target.revisions.all())
    runs = list(target.runs.all())
    return build_retention_plan(
        settings_from_policy(policy),
        revisions=(
            RevisionCandidate(
                revision.pk,
                revision.created,
                revision.protected,
                revision.content_changed,
            )
            for revision in revisions
        ),
        runs=(
            RunCandidate(
                run.pk,
                run.finished_at or run.queued_at,
                run.status,
            )
            for run in runs
        ),
        now=now,
    )


now = timezone.now()
existing_targets = list(BackupTarget.objects.all())
unsafe_existing_targets = []
for existing_target in existing_targets:
    plan = current_plan(existing_target, now)
    if plan is not None and (plan.revisions_to_delete or plan.runs_to_delete):
        unsafe_existing_targets.append(
            {
                "target_id": existing_target.pk,
                "device": str(existing_target.device),
                "revisions_to_delete": plan.revisions_to_delete,
                "runs_to_delete": plan.runs_to_delete,
            }
        )
assert not unsafe_existing_targets, (
    "Refusing enabled scheduler smoke test because existing targets have expired data: "
    f"{unsafe_existing_targets}"
)

prefix = f"ncb-retention-scheduler-{uuid4().hex[:8]}"
admin = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
client = Client()
client.force_login(admin)
limited_user = get_user_model().objects.create_user(username=f"{prefix}-limited")
limited_client = Client()
limited_client.force_login(limited_user)
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
retention = RetentionPolicy.objects.create(
    name=f"{prefix}-retention",
    keep_all_days=0,
    daily_days=0,
    weekly_weeks=0,
    monthly_months=0,
    minimum_changed_revisions=0,
    unchanged_run_days=0,
    changed_run_days=0,
    failed_run_days=0,
)
policy = BackupPolicy.objects.create(
    name=f"{prefix}-policy",
    schedule_type="daily",
    time_of_day="02:00",
    retry_backoff_minutes=[],
    retention_policy=retention,
)
storage = LocalConfigStorage(settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"])
targets = []
devices = []
created_jobs = []
operational_settings = OperationalSettings.objects.get(singleton=True)
original_retention_enabled = operational_settings.retention_scheduler_enabled
original_remote_retention_enabled = operational_settings.remote_retention_scheduler_enabled
original_retention_batch_size = operational_settings.retention_scheduler_batch_size
OperationalSettings.objects.filter(pk=operational_settings.pk).update(
    retention_scheduler_enabled=False,
    remote_retention_scheduler_enabled=False,
    retention_scheduler_batch_size=25,
)
operational_settings.refresh_from_db()


def create_target(label):
    device = Device.objects.create(
        name=f"{prefix}-{label}",
        site=site,
        role=role,
        device_type=device_type,
    )
    devices.append(device)
    target = BackupTarget.objects.create(
        device=device,
        driver_override="fake",
        policy_override=policy,
    )
    targets.append(target)
    return target


def create_revision(target, label, *, age_days, previous=None):
    content = f"hostname {label}\n".encode()
    digest = hashlib.sha256(content).hexdigest()
    revision = ConfigRevision.objects.create(
        target=target,
        normalized_hash=digest,
        normalizer_version="1",
        driver_id="fake",
        previous_revision=previous,
    )
    ConfigRevision.objects.filter(pk=revision.pk).update(created=now - timedelta(days=age_days))
    revision.refresh_from_db()
    key = f"retention-scheduler-smoke/{revision.revision_uuid}/running-config.txt"
    storage.put(key, content)
    ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="running_config",
        storage_key=key,
        size=len(content),
        raw_hash=digest,
        normalized_hash=digest,
        is_primary=True,
    )
    return revision


def add_expired_history(target, label):
    old = create_revision(target, f"{label}-old", age_days=30)
    latest = create_revision(target, f"{label}-latest", age_days=0, previous=old)
    target.last_revision = latest
    target.save(update_fields=("last_revision", "last_updated"))
    return old, latest


try:
    automatic_target = create_target("automatic")
    automatic_old, automatic_latest = add_expired_history(
        automatic_target,
        "automatic",
    )
    ftp_destination = BackupDestination.objects.filter(
        enabled=True,
        protocol="ftp",
    ).first()
    assert ftp_destination is not None, "Retention smoke requires one enabled FTP destination"
    exhausted_replica = RevisionReplica.objects.create(
        revision=automatic_old,
        destination=ftp_destination,
        status="failed",
        attempts=ftp_destination.max_retries + 1,
        next_retry_at=None,
        remote_path=ftp_revision_destination_path(
            ftp_destination.base_path,
            device_name=automatic_target.device.name,
            device_id=automatic_target.device_id,
            created_at=automatic_old.created,
            revision_id=automatic_old.pk,
        ),
        remote_available=False,
    )

    active_target = create_target("active")
    add_expired_history(active_target, "active")
    active_run = BackupRun.objects.create(target=active_target, status="queued")

    duplicate_target = create_target("duplicate")
    add_expired_history(duplicate_target, "duplicate")
    duplicate_job = Job.objects.create(
        object_type=ContentType.objects.get_for_model(BackupTarget),
        object_id=duplicate_target.pk,
        name=CLEANUP_JOB_NAME,
        status="pending",
        data={},
        job_id=uuid4(),
        queue_name=BACKUP_QUEUE,
    )
    created_jobs.append(duplicate_job)

    clean_target = create_target("clean")
    clean_latest = create_revision(clean_target, "clean-latest", age_days=0)
    clean_target.last_revision = clean_latest
    clean_target.save(update_fields=("last_revision", "last_updated"))

    assert operational_settings.retention_scheduler_enabled is False
    assert operational_settings.remote_retention_scheduler_enabled is False
    settings_response = client.get(reverse("plugins:netbox_config_backup:advanced_settings"))
    assert settings_response.status_code == 200
    assert b"Expired backup data" in settings_response.content
    assert b"Enable local cleanup" in settings_response.content
    assert b"Enable FTP cleanup" in settings_response.content
    assert b"Runs every 24 hours" in settings_response.content
    assert b'name="retention_scheduler_batch_size"' in settings_response.content
    assert b'type="hidden" name="retention_scheduler_batch_size"' in settings_response.content
    assert b'value="25"' in settings_response.content

    limited_response = limited_client.post(
        reverse("plugins:netbox_config_backup:advanced_settings"),
        {
            "settings_action": "retention",
            "retention_scheduler_enabled": "on",
            "retention_scheduler_batch_size": "25",
            "confirm_enable": "on",
        },
    )
    assert limited_response.status_code == 403
    operational_settings.refresh_from_db()
    assert operational_settings.retention_scheduler_enabled is False

    unconfirmed_response = client.post(
        reverse("plugins:netbox_config_backup:advanced_settings"),
        {
            "settings_action": "retention",
            "retention_scheduler_enabled": "on",
            "retention_scheduler_batch_size": "25",
        },
    )
    assert unconfirmed_response.status_code == 400
    assert (
        b"Confirm the automatic deletion warning before enabling retention."
        in unconfirmed_response.content
    )
    operational_settings.refresh_from_db()
    assert operational_settings.retention_scheduler_enabled is False
    assert operational_settings.remote_retention_scheduler_enabled is False

    remote_unconfirmed_response = client.post(
        reverse("plugins:netbox_config_backup:advanced_settings"),
        {
            "settings_action": "retention",
            "remote_retention_scheduler_enabled": "on",
            "retention_scheduler_batch_size": "25",
        },
    )
    assert remote_unconfirmed_response.status_code == 400
    assert (
        b"Confirm the permanent FTP deletion warning before enabling remote retention."
        in remote_unconfirmed_response.content
    )
    operational_settings.refresh_from_db()
    assert operational_settings.retention_scheduler_enabled is False
    assert operational_settings.remote_retention_scheduler_enabled is False

    before_jobs = Job.objects.filter(name=CLEANUP_JOB_NAME).count()
    disabled_result = ScheduledRetentionDispatcherJob.run(Runner())
    assert disabled_result == {"enabled": False, "queued": 0}
    assert Job.objects.filter(name=CLEANUP_JOB_NAME).count() == before_jobs
    assert ConfigRevision.objects.filter(pk=automatic_old.pk).exists()

    enabled_response = client.post(
        reverse("plugins:netbox_config_backup:advanced_settings"),
        {
            "settings_action": "retention",
            "retention_scheduler_enabled": "on",
            "retention_scheduler_batch_size": "25",
            "confirm_enable": "on",
        },
    )
    assert enabled_response.status_code == 302
    operational_settings.refresh_from_db()
    assert operational_settings.retention_scheduler_enabled is True
    assert operational_settings.remote_retention_scheduler_enabled is False

    enabled_settings_response = client.get(
        reverse("plugins:netbox_config_backup:advanced_settings")
    )
    assert b'id="id_retention_scheduler_enabled" checked' in enabled_settings_response.content
    enabled_result = ScheduledRetentionDispatcherJob.run(Runner())

    assert enabled_result["enabled"] is True
    assert enabled_result["queued"] == 1, enabled_result
    assert enabled_result["local"]["skipped_active_backup"] >= 1, enabled_result
    assert enabled_result["local"]["skipped_active_cleanup"] >= 1, enabled_result

    cleanup_job = wait_for_job(
        Job.objects.filter(
            name=CLEANUP_JOB_NAME,
            object_id=automatic_target.pk,
        ).latest("created")
    )
    created_jobs.append(cleanup_job)
    assert cleanup_job.status == "completed", (cleanup_job.status, cleanup_job.data)
    assert ConfigRevision.objects.filter(pk=automatic_old.pk).exists()
    assert RevisionReplica.objects.filter(pk=exhausted_replica.pk).exists()
    assert not ConfigArtifact.objects.get(revision=automatic_old).local_available
    assert ConfigRevision.objects.filter(pk=automatic_latest.pk).exists()
    assert ConfigRevision.objects.filter(target=active_target).count() == 2
    assert ConfigRevision.objects.filter(target=duplicate_target).count() == 2
    assert ConfigRevision.objects.filter(target=clean_target).count() == 1

    disabled_response = client.post(
        reverse("plugins:netbox_config_backup:advanced_settings"),
        {
            "settings_action": "retention",
            "retention_scheduler_batch_size": "25",
        },
    )
    assert disabled_response.status_code == 302
    operational_settings.refresh_from_db()
    assert operational_settings.retention_scheduler_enabled is False
    assert operational_settings.remote_retention_scheduler_enabled is False

    print(
        {
            "default_disabled": True,
            "remote_default_disabled": True,
            "remote_requires_separate_confirmation": True,
            "enabled_queued": enabled_result["queued"],
            "active_backup_skipped": True,
            "active_cleanup_skipped": True,
            "clean_target_skipped": True,
            "cleanup_job_status": cleanup_job.status,
            "failed_remote_pointer_preserved": True,
            "existing_targets_untouched": len(existing_targets),
        }
    )
finally:
    OperationalSettings.objects.filter(pk=operational_settings.pk).update(
        retention_scheduler_enabled=original_retention_enabled,
        remote_retention_scheduler_enabled=original_remote_retention_enabled,
        retention_scheduler_batch_size=original_retention_batch_size,
    )
    if "active_run" in locals() and BackupRun.objects.filter(pk=active_run.pk).exists():
        BackupRun.objects.filter(pk=active_run.pk).update(
            status="skipped",
            finished_at=timezone.now(),
        )
    for job in created_jobs:
        Job.objects.filter(pk=job.pk).delete()
    for target in reversed(targets):
        if BackupTarget.objects.filter(pk=target.pk).exists():
            target.refresh_from_db()
            delete_backup_target(target)
    limited_user.delete()
    admin.delete()
    policy.delete()
    retention.delete()
    for device in reversed(devices):
        device.delete()
    device_type.delete()
    manufacturer.delete()
    role.delete()
    site.delete()
