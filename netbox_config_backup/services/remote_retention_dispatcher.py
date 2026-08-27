from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.choices import JobStatusChoices
from core.models import Job
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.choices import (
    REPLICATED_DESTINATION_PROTOCOLS,
    ReplicaStatusChoices,
    RunStatusChoices,
)
from netbox_config_backup.models import BackupDestination, BackupRun, BackupTarget, RevisionReplica

from .retention import (
    RevisionCandidate,
    build_retention_plan,
    effective_remote_retention_policy,
    settings_from_remote_policy,
)

ACTIVE_BACKUP_STATUSES = (RunStatusChoices.QUEUED, RunStatusChoices.RUNNING)
REMOTE_CLEANUP_JOB_NAMES = (
    "Config backup remote retention cleanup",
    # Preserve upgrade-time deduplication while an older named job is active.
    "Config backup FTP retention cleanup",
)


@dataclass(slots=True)
class RemoteRetentionDispatchSummary:
    considered: int = 0
    expired: int = 0
    queued: int = 0
    skipped_active_backup: int = 0
    skipped_active_cleanup: int = 0
    conflicts: int = 0


def dispatch_expired_remote_targets(
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> RemoteRetentionDispatchSummary:
    """Queue cleanup for targets expired on at least one remote storage."""

    if limit <= 0:
        raise ValueError("Remote retention dispatcher limit must be positive.")
    now = now or timezone.now()
    summary = RemoteRetentionDispatchSummary()
    content_type = ContentType.objects.get_for_model(BackupTarget)
    candidate_ids = (
        BackupTarget.objects.filter(
            Q(remote_retention_policy__isnull=False)
            | Q(
                revisions__replicas__destination__protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
                revisions__replicas__destination__enabled=True,
                revisions__replicas__destination__remote_retention_policy__isnull=False,
            )
        )
        .distinct()
        .order_by("pk")
        .values_list("pk", flat=True)
    )

    for target_id in candidate_ids.iterator():
        if summary.queued >= limit:
            break
        summary.considered += 1
        try:
            with transaction.atomic():
                target = (
                    BackupTarget.objects.select_for_update(of=("self",))
                    .select_related("remote_retention_policy")
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
                    name__in=REMOTE_CLEANUP_JOB_NAMES,
                    status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
                ).exists():
                    summary.skipped_active_cleanup += 1
                    continue

                destinations = (
                    BackupDestination.objects.filter(
                        protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
                        enabled=True,
                        replicas__revision__target=target,
                        replicas__remote_deleted_at__isnull=True,
                    )
                    .select_related("remote_retention_policy")
                    .distinct()
                )
                target_expired = False
                for destination in destinations:
                    policy = effective_remote_retention_policy(target, destination)
                    if policy is None:
                        continue
                    candidate_revision_ids = (
                        RevisionReplica.objects.filter(
                            revision__target=target,
                            destination=destination,
                            remote_deleted_at__isnull=True,
                        )
                        .filter(
                            Q(status=ReplicaStatusChoices.SUCCESS, remote_available=True)
                            | Q(
                                status=ReplicaStatusChoices.FAILED,
                                remote_path__gt="",
                                next_retry_at__isnull=True,
                            )
                        )
                        .values_list("revision_id", flat=True)
                    )
                    revisions = list(
                        target.revisions.filter(pk__in=candidate_revision_ids).order_by(
                            "-created", "-pk"
                        )
                    )
                    plan = build_retention_plan(
                        settings_from_remote_policy(policy),
                        revisions=(
                            RevisionCandidate(
                                object_id=revision.pk,
                                created=revision.created,
                                protected=revision.protected,
                                content_changed=revision.content_changed,
                            )
                            for revision in revisions
                        ),
                        runs=(),
                        now=now,
                    )
                    if plan.revisions_to_delete:
                        target_expired = True
                        break
                if not target_expired:
                    continue
                summary.expired += 1

                from netbox_config_backup.jobs import BACKUP_QUEUE, RemoteRetentionCleanupJob

                RemoteRetentionCleanupJob.enqueue(
                    target_id=target.pk,
                    instance=target,
                    queue_name=BACKUP_QUEUE,
                )
                summary.queued += 1
        except IntegrityError:
            summary.conflicts += 1
    return summary
