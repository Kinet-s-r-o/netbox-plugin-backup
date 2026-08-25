from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.choices import JobStatusChoices
from core.models import Job
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.choices import (
    RunSourceChoices,
    RunStatusChoices,
    TargetStatusChoices,
)
from netbox_config_backup.models import BackupRun, BackupTarget

from .django_repository import DjangoBackupRepository
from .health import stuck_run_query
from .queueing import enqueue_backup_run
from .scheduling import (
    apply_target_schedule,
    calculate_failure_next_run,
    calculate_next_run,
    is_retry_scheduled,
    target_timezone_name,
)

ACTIVE_RUN_STATUSES = (RunStatusChoices.QUEUED, RunStatusChoices.RUNNING)


@dataclass(slots=True)
class DispatchSummary:
    initialized: int = 0
    due: int = 0
    queued: int = 0
    skipped_active: int = 0
    conflicts: int = 0


def initialize_target_schedules(*, now: datetime) -> int:
    inactive = BackupTarget.objects.filter(
        Q(enabled=False) | Q(policy_override__isnull=True) | Q(policy_override__enabled=False)
    ).exclude(next_run_at=None)
    inactive.update(next_run_at=None)

    targets = list(
        BackupTarget.objects.filter(
            enabled=True,
            policy_override__enabled=True,
            next_run_at__isnull=True,
        ).select_related("policy_override", "device__site")
    )
    for target in targets:
        apply_target_schedule(target, now=now)
    return len(targets)


def dispatch_due_targets(*, now: datetime | None = None, limit: int = 100) -> DispatchSummary:
    now = now or timezone.now()
    summary = DispatchSummary(initialized=initialize_target_schedules(now=now))
    candidate_ids = list(
        BackupTarget.objects.filter(
            enabled=True,
            policy_override__enabled=True,
            next_run_at__lte=now,
        )
        .order_by("next_run_at")
        .values_list("pk", flat=True)[:limit]
    )
    summary.due = len(candidate_ids)

    for target_id in candidate_ids:
        try:
            with transaction.atomic():
                target = (
                    BackupTarget.objects.select_for_update(of=("self",))
                    .select_related("policy_override", "device__site")
                    .get(pk=target_id)
                )
                if (
                    not target.enabled
                    or not target.policy_override
                    or not target.policy_override.enabled
                    or not target.next_run_at
                    or target.next_run_at > now
                ):
                    continue
                if BackupRun.objects.filter(target=target, status__in=ACTIVE_RUN_STATUSES).exists():
                    summary.skipped_active += 1
                    continue

                scheduled_for = target.next_run_at
                retry = target.status == TargetStatusChoices.FAILED and is_retry_scheduled(
                    target.policy_override, target.consecutive_failures
                )
                source = RunSourceChoices.RETRY if retry else RunSourceChoices.SCHEDULED
                dedupe_key = f"{source}:{scheduled_for.isoformat()}"
                target.next_run_at = calculate_next_run(
                    target.policy_override,
                    after=scheduled_for,
                    now=now,
                    target_key=target.pk,
                    timezone_name=target_timezone_name(target),
                )
                target.save(update_fields=("next_run_at", "last_updated"))
                enqueue_backup_run(
                    target,
                    source=source,
                    scheduled_for=scheduled_for,
                    dedupe_key=dedupe_key,
                )
                summary.queued += 1
        except IntegrityError:
            summary.conflicts += 1
    return summary


def reconcile_stale_runs(*, now: datetime, stale_after_minutes: int) -> int:
    candidates = BackupRun.objects.filter(
        stuck_run_query(now=now, timeout_minutes=stale_after_minutes)
    ).select_related("target__policy_override", "target__device__site")
    reconciled = 0
    repository = DjangoBackupRepository()
    for run in candidates:
        job = Job.objects.filter(job_id=run.job_id).only("status").first() if run.job_id else None
        if job and job.status in JobStatusChoices.ENQUEUED_STATE_CHOICES:
            continue
        repository.mark_failed(
            run.pk,
            status=RunStatusChoices.ERRORED,
            error_code="STALE_RUN",
            error_message="Backup job ended without completing its run record.",
            finished_at=now,
        )
        reconciled += 1
    return reconciled


def failure_next_run(target: BackupTarget, *, failed_at: datetime, failures: int):
    policy = target.policy_override
    return calculate_failure_next_run(
        policy,
        failed_at=failed_at,
        consecutive_failures=failures,
        target_key=target.pk,
        timezone_name=target_timezone_name(target),
    )
