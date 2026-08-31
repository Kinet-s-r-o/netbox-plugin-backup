"""Verify target freshness, stuck-run separation, dashboard, and UI filters."""

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

from netbox_config_backup.models import (
    BackupPolicy,
    BackupRun,
    BackupTarget,
    RetentionPolicy,
)
from netbox_config_backup.services.dispatcher import reconcile_stale_runs
from netbox_config_backup.services.health import refresh_target_health
from netbox_config_backup.services.target_deletion import delete_backup_target

prefix = f"ncb-health-{uuid4().hex[:8]}"
now = timezone.now()
existing_statuses = dict(BackupTarget.objects.values_list("pk", "status"))
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site", time_zone="UTC")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
retention = RetentionPolicy.objects.create(name=f"{prefix}-retention")
policy = BackupPolicy.objects.create(
    name=f"{prefix}-policy",
    schedule_type="interval",
    interval_minutes=60,
    time_of_day=None,
    retry_backoff_minutes=[],
    retention_policy=retention,
)
user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
client = Client()
client.force_login(user)
devices = []
targets = []
jobs = []
failed_run = None
stuck_run = None


def create_target(label, **values):
    device = Device.objects.create(
        name=f"{prefix}-{label}",
        site=site,
        role=role,
        device_type=device_type,
    )
    devices.append(device)
    target = BackupTarget.objects.create(
        device=device,
        policy_override=policy,
        driver_override="fake",
        **values,
    )
    targets.append(target)
    return target


try:
    healthy_target = create_target(
        "healthy",
        status="healthy",
        last_success_at=now - timedelta(minutes=30),
    )
    stale_target = create_target(
        "stale",
        status="healthy",
        last_success_at=now - timedelta(hours=3),
    )
    failed_target = create_target(
        "failed",
        status="failed",
        consecutive_failures=1,
        last_success_at=now - timedelta(minutes=20),
    )
    failed_run = BackupRun.objects.create(
        target=failed_target,
        status="failed",
        queued_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=9),
        finished_at=now - timedelta(minutes=8),
        error_code="HEALTH_SMOKE_FAILURE",
        error_message="Safe dashboard failure detail.",
    )

    stuck_target = create_target("stuck", status="never")
    core_job = Job.objects.create(
        object_type=ContentType.objects.get_for_model(BackupTarget),
        object_id=stuck_target.pk,
        name="Config backup",
        status="pending",
        data={},
        job_id=uuid4(),
        queue_name="netbox_config_backup.backup",
    )
    jobs.append(core_job)
    stuck_run = BackupRun.objects.create(
        target=stuck_target,
        status="queued",
        queued_at=now
        - timedelta(
            minutes=settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_run_minutes"] + 1
        ),
        job_id=core_job.job_id,
    )

    summary = refresh_target_health(
        now=now,
        grace_minutes=settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_target_grace_minutes"],
    )
    healthy_target.refresh_from_db()
    stale_target.refresh_from_db()
    failed_target.refresh_from_db()
    stuck_target.refresh_from_db()
    assert healthy_target.status == "healthy"
    assert stale_target.status == "stale"
    assert failed_target.status == "failed"
    assert stuck_target.status == "never"
    assert summary.stale >= 1
    assert summary.failed >= 1

    home = client.get(reverse("plugins:netbox_config_backup:home"))
    assert home.status_code == 200
    assert b"Recent failures" in home.content
    assert b"HEALTH_SMOKE_FAILURE" in home.content
    assert b"Safe dashboard failure detail." in home.content
    assert failed_run.get_absolute_url().encode() in home.content
    assert stale_target.device.name.encode() not in home.content

    stale_list = client.get(
        reverse("plugins:netbox_config_backup:backuptarget_list"),
        {"status": "stale"},
    )
    assert stale_list.status_code == 200
    assert stale_target.device.name.encode() in stale_list.content
    assert healthy_target.device.name.encode() not in stale_list.content

    failed_list = client.get(
        reverse("plugins:netbox_config_backup:backuprun_list"),
        {"failed": "true"},
    )
    assert failed_list.status_code == 200
    assert failed_target.device.name.encode() in failed_list.content
    assert stuck_target.device.name.encode() not in failed_list.content

    error_list = client.get(
        reverse("plugins:netbox_config_backup:backuprun_list"),
        {"error_code": "HEALTH_SMOKE"},
    )
    assert error_list.status_code == 200
    assert failed_target.device.name.encode() in error_list.content

    stuck_list = client.get(
        reverse("plugins:netbox_config_backup:backuprun_list"),
        {"stuck": "true"},
    )
    assert stuck_list.status_code == 200
    assert stuck_target.device.name.encode() in stuck_list.content
    assert failed_target.device.name.encode() not in stuck_list.content

    stale_detail = client.get(stale_target.get_absolute_url())
    assert stale_detail.status_code == 200
    assert b"This target is stale." in stale_detail.content
    assert b"Expected success by" in stale_detail.content

    failed_detail = client.get(failed_target.get_absolute_url())
    assert failed_detail.status_code == 200
    assert b"The latest backup attempt failed." in failed_detail.content
    assert failed_run.get_absolute_url().encode() in failed_detail.content

    stuck_target_detail = client.get(stuck_target.get_absolute_url())
    assert stuck_target_detail.status_code == 200
    assert b"A backup run appears to be stuck." in stuck_target_detail.content
    assert stuck_run.get_absolute_url().encode() in stuck_target_detail.content

    stuck_run_detail = client.get(stuck_run.get_absolute_url())
    assert stuck_run_detail.status_code == 200
    assert b"This backup run appears to be stuck." in stuck_run_detail.content
    assert b"not a stale target classification" in stuck_run_detail.content
    stuck_detected = stuck_run.is_stuck

    # A pending Core Job without a corresponding Redis job is orphaned. The
    # dispatcher must cancel it and release the target instead of trusting the
    # database status forever.
    assert (
        reconcile_stale_runs(
            now=now,
            stale_after_minutes=settings.PLUGINS_CONFIG["netbox_config_backup"][
                "stale_run_minutes"
            ],
        )
        == 1
    )
    assert not Job.objects.filter(pk=core_job.pk).exists()
    stuck_run.refresh_from_db()
    stale_target.refresh_from_db()
    assert stuck_run.status == "errored"
    assert stuck_run.error_code == "STALE_RUN"
    assert stale_target.status == "stale"

    print(
        {
            "healthy": healthy_target.status,
            "stale": stale_target.status,
            "failed": failed_target.status,
            "stuck_detected": stuck_detected,
            "dashboard_failure_visible": True,
            "target_filter": True,
            "failure_filter": True,
            "stuck_filter": True,
            "stuck_reconciled": stuck_run.error_code,
        }
    )
finally:
    BackupRun.objects.filter(target__in=targets).exclude(pk=getattr(failed_run, "pk", None)).update(
        status="errored", finished_at=timezone.now()
    )
    for job in jobs:
        Job.objects.filter(pk=job.pk).delete()
    for target in reversed(targets):
        if BackupTarget.objects.filter(pk=target.pk).exists():
            target.refresh_from_db()
            delete_backup_target(target)
    for target_id, status in existing_statuses.items():
        BackupTarget.objects.filter(pk=target_id).update(status=status)
    user.delete()
    policy.delete()
    retention.delete()
    for device in reversed(devices):
        device.delete()
    device_type.delete()
    manufacturer.delete()
    role.delete()
    site.delete()
