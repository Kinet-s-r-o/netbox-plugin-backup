from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .scheduling import calculate_next_run, target_timezone_name

TARGET_NEVER = "never"
TARGET_HEALTHY = "healthy"
TARGET_FAILED = "failed"
TARGET_STALE = "stale"
TARGET_DISABLED = "disabled"

RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_ERRORED = "errored"

FAILURE_RUN_STATUSES = (
    RUN_PARTIAL,
    RUN_FAILED,
    RUN_ERRORED,
)
ACTIVE_RUN_STATUSES = (
    RUN_QUEUED,
    RUN_RUNNING,
)


@dataclass(frozen=True, slots=True)
class TargetHealthEvaluation:
    status: str
    expected_success_by: datetime | None
    monitored: bool


@dataclass(frozen=True, slots=True)
class TargetHealthRefreshSummary:
    evaluated: int
    updated: int
    healthy: int
    stale: int
    failed: int
    never: int
    disabled: int


def evaluate_target_health(
    target,
    *,
    now: datetime,
    grace_minutes: int,
) -> TargetHealthEvaluation:
    if now.tzinfo is None:
        raise ValueError("Target health evaluation requires a timezone-aware datetime.")
    if grace_minutes < 0:
        raise ValueError("Target health grace cannot be negative.")

    if not target.enabled:
        return TargetHealthEvaluation(TARGET_DISABLED, None, False)

    if target.consecutive_failures > 0 or target.status == TARGET_FAILED:
        return TargetHealthEvaluation(TARGET_FAILED, None, True)

    policy = target.policy_override
    if not policy or not policy.enabled:
        status = TARGET_HEALTHY if target.last_success_at else TARGET_NEVER
        return TargetHealthEvaluation(status, None, False)

    reference = target.last_success_at or target.created
    if reference is None:
        return TargetHealthEvaluation(TARGET_NEVER, None, True)
    if reference.tzinfo is None:
        raise ValueError("Target health reference must be timezone-aware.")

    try:
        next_expected = calculate_next_run(
            policy,
            after=reference,
            now=reference,
            target_key=target.pk or target.device_id,
            timezone_name=target_timezone_name(target),
        )
    except ValueError:
        status = TARGET_HEALTHY if target.last_success_at else TARGET_NEVER
        return TargetHealthEvaluation(status, None, False)

    deadline = next_expected + timedelta(minutes=grace_minutes)
    if now >= deadline:
        return TargetHealthEvaluation(TARGET_STALE, deadline, True)
    status = TARGET_HEALTHY if target.last_success_at else TARGET_NEVER
    return TargetHealthEvaluation(status, deadline, True)


def refresh_target_health(
    *,
    now: datetime,
    grace_minutes: int,
) -> TargetHealthRefreshSummary:
    from netbox_config_backup.models import BackupTarget

    targets = list(
        BackupTarget.objects.select_related(
            "policy_override",
            "device__site",
        )
    )
    changed = []
    newly_stale_ids = []
    counts = {
        TARGET_HEALTHY: 0,
        TARGET_STALE: 0,
        TARGET_FAILED: 0,
        TARGET_NEVER: 0,
        TARGET_DISABLED: 0,
    }
    for target in targets:
        evaluation = evaluate_target_health(
            target,
            now=now,
            grace_minutes=grace_minutes,
        )
        counts[evaluation.status] += 1
        if target.status != evaluation.status:
            if evaluation.status == TARGET_STALE:
                newly_stale_ids.append(target.pk)
            target.status = evaluation.status
            target.last_updated = now
            changed.append(target)

    if changed:
        BackupTarget.objects.bulk_update(changed, ("status", "last_updated"))
    if newly_stale_ids:
        from netbox_config_backup.events import TARGET_STALE as TARGET_STALE_EVENT
        from netbox_config_backup.events import queue_target_event

        for target_id in newly_stale_ids:
            queue_target_event(TARGET_STALE_EVENT, target_id)

    return TargetHealthRefreshSummary(
        evaluated=len(targets),
        updated=len(changed),
        healthy=counts[TARGET_HEALTHY],
        stale=counts[TARGET_STALE],
        failed=counts[TARGET_FAILED],
        never=counts[TARGET_NEVER],
        disabled=counts[TARGET_DISABLED],
    )


def is_run_stuck(run, *, now: datetime, timeout_minutes: int) -> bool:
    if now.tzinfo is None:
        raise ValueError("Stuck run evaluation requires a timezone-aware datetime.")
    if timeout_minutes <= 0:
        raise ValueError("Stuck run timeout must be positive.")
    if run.status not in ACTIVE_RUN_STATUSES:
        return False
    reference = run.queued_at
    if run.status == RUN_RUNNING and run.started_at is not None:
        reference = run.started_at
    return reference <= now - timedelta(minutes=timeout_minutes)


def stuck_run_query(*, now: datetime, timeout_minutes: int) -> Any:
    from django.db.models import Q

    cutoff = now - timedelta(minutes=timeout_minutes)
    return Q(status=RUN_QUEUED, queued_at__lte=cutoff) | Q(
        status=RUN_RUNNING,
    ) & (Q(started_at__lte=cutoff) | Q(started_at__isnull=True, queued_at__lte=cutoff))


def stuck_run_queryset(
    queryset: Any,
    *,
    now: datetime,
    timeout_minutes: int,
) -> Any:
    return queryset.filter(stuck_run_query(now=now, timeout_minutes=timeout_minutes))


def failure_run_queryset(queryset: Any) -> Any:
    return queryset.filter(status__in=FAILURE_RUN_STATUSES)
