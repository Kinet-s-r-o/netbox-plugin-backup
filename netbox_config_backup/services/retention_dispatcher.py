from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.choices import JobStatusChoices
from core.models import Job
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.choices import DestinationProtocolChoices, RunStatusChoices
from netbox_config_backup.models import BackupDestination, BackupRun, BackupTarget

from .retention import (
    RevisionCandidate,
    RunCandidate,
    build_retention_plan,
    effective_local_retention_policy,
    effective_retention_policy,
    settings_from_policy,
)

ACTIVE_BACKUP_STATUSES = (RunStatusChoices.QUEUED, RunStatusChoices.RUNNING)
CLEANUP_JOB_NAME = "Config backup retention cleanup"


@dataclass(slots=True)
class RetentionDispatchSummary:
    considered: int = 0
    expired: int = 0
    queued: int = 0
    skipped_active_backup: int = 0
    skipped_active_cleanup: int = 0
    conflicts: int = 0


def dispatch_expired_targets(
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> RetentionDispatchSummary:
    """Queue retention cleanup only for targets with currently expired history."""
    if limit <= 0:
        raise ValueError("Retention dispatcher limit must be positive.")
    now = now or timezone.now()
    summary = RetentionDispatchSummary()
    content_type = ContentType.objects.get_for_model(BackupTarget)
    local_storage = (
        BackupDestination.objects.filter(
            protocol=DestinationProtocolChoices.LOCAL,
            is_default=True,
        )
        .select_related("local_retention_policy")
        .first()
    )
    candidate_targets = BackupTarget.objects.all()
    if local_storage is None or local_storage.local_retention_policy_id is None:
        candidate_targets = candidate_targets.filter(
            Q(retention_override__isnull=False) | Q(policy_override__retention_policy__isnull=False)
        )
    candidate_ids = candidate_targets.order_by("pk").values_list("pk", flat=True)

    for target_id in candidate_ids.iterator():
        if summary.queued >= limit:
            break
        summary.considered += 1
        try:
            with transaction.atomic():
                target = (
                    BackupTarget.objects.select_for_update(of=("self",))
                    .select_related(
                        "retention_override",
                        "policy_override__retention_policy",
                    )
                    .get(pk=target_id)
                )
                if BackupRun.objects.filter(
                    target=target,
                    status__in=ACTIVE_BACKUP_STATUSES,
                ).exists():
                    summary.skipped_active_backup += 1
                    continue
                if Job.objects.filter(
                    object_type=content_type,
                    object_id=target.pk,
                    name=CLEANUP_JOB_NAME,
                    status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
                ).exists():
                    summary.skipped_active_cleanup += 1
                    continue

                policy = (
                    effective_local_retention_policy(target, local_storage)
                    if local_storage is not None
                    else effective_retention_policy(target)
                )
                if policy is None:
                    continue
                revisions = list(
                    target.revisions.filter(artifacts__local_available=True)
                    .distinct()
                    .order_by("-created", "-pk")
                )
                runs = list(target.runs.all().order_by("-queued_at", "-pk"))
                plan = build_retention_plan(
                    settings_from_policy(policy),
                    revisions=(
                        RevisionCandidate(
                            object_id=revision.pk,
                            created=revision.created,
                            protected=revision.protected,
                            content_changed=revision.content_changed,
                        )
                        for revision in revisions
                    ),
                    runs=(
                        RunCandidate(
                            object_id=run.pk,
                            timestamp=run.finished_at or run.queued_at,
                            status=run.status,
                        )
                        for run in runs
                    ),
                    now=now,
                )
                if not plan.revisions_to_delete and not plan.runs_to_delete:
                    continue
                summary.expired += 1

                from netbox_config_backup.jobs import BACKUP_QUEUE, RetentionCleanupJob

                RetentionCleanupJob.enqueue(
                    target_id=target.pk,
                    instance=target,
                    queue_name=BACKUP_QUEUE,
                )
                summary.queued += 1
        except IntegrityError:
            summary.conflicts += 1
    return summary
