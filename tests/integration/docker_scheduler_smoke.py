"""Exercise scheduled dispatch, failure backoff, and a successful retry."""

import json
import time
from datetime import timedelta
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.utils import timezone

from netbox_config_backup.models import (
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    PlatformMapping,
    RetentionPolicy,
)
from netbox_config_backup.services.dispatcher import dispatch_due_targets


def wait_for_run(run: BackupRun, timeout: float = 30.0) -> BackupRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run.refresh_from_db()
        if run.status not in {"queued", "running"}:
            return run
        time.sleep(0.25)
    raise AssertionError(f"Backup run {run.pk} did not finish; current status={run.status}")


BackupTarget.objects.filter(device__name__startswith="ncb-scheduler-").update(
    enabled=False, next_run_at=None
)
prefix = f"ncb-scheduler-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site", time_zone="UTC")
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
retention = RetentionPolicy.objects.create(name=f"{prefix}-retention")
policy = BackupPolicy.objects.create(
    name=f"{prefix}-policy",
    schedule_type="interval",
    interval_minutes=60,
    time_of_day=None,
    max_retries=2,
    retry_backoff_minutes=[0],
    retention_policy=retention,
)
mapping = PlatformMapping.objects.create(
    platform=platform,
    driver_id="fake",
    driver_options={"failure_code": "SIMULATED_FAILURE"},
)
now = timezone.now()
target = BackupTarget.objects.create(
    device=device,
    policy_override=policy,
    next_run_at=now - timedelta(seconds=1),
)

first_dispatch = dispatch_due_targets(now=now)
assert first_dispatch.queued == 1, first_dispatch
first = wait_for_run(BackupRun.objects.filter(target=target).latest("created"))
assert first.source == "scheduled"
assert first.status == "failed", (first.status, first.error_code, first.error_message)
target.refresh_from_db()
assert target.consecutive_failures == 1
assert target.next_run_at is not None

mapping.driver_options = {
    "config": f"hostname {device.name}\ninterface Loopback0\n description retry-success\n"
}
mapping.save(update_fields=("driver_options", "last_updated"))

second_dispatch = dispatch_due_targets(now=timezone.now() + timedelta(seconds=1))
assert second_dispatch.queued == 1, second_dispatch
second = wait_for_run(BackupRun.objects.filter(target=target).latest("created"))
assert second.pk != first.pk
assert second.source == "retry"
assert second.status == "success_changed", (second.status, second.error_code, second.error_message)
target.refresh_from_db()
assert target.consecutive_failures == 0
assert target.status == "healthy"
assert target.next_run_at > timezone.now()
assert ConfigRevision.objects.filter(target=target).count() == 1

print(
    json.dumps(
        {
            "first_dispatch_queued": first_dispatch.queued,
            "first_run": first.status,
            "first_source": first.source,
            "next_run_at": target.next_run_at.isoformat(),
            "retry_dispatch_queued": second_dispatch.queued,
            "retry_run": second.status,
            "retry_source": second.source,
            "revision_count": ConfigRevision.objects.filter(target=target).count(),
        },
        sort_keys=True,
    )
)
