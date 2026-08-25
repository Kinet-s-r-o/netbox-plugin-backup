from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.choices import JobStatusChoices
from core.models import Job
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.models import BackupDestination

from .ftp_audit_scheduling import (
    calculate_destination_next_ftp_audit,
    calculate_next_ftp_audit,
)

AUDIT_JOB_NAME = "Config backup FTP integrity audit"


@dataclass(slots=True)
class FtpAuditDispatchSummary:
    initialized: int = 0
    due: int = 0
    queued: int = 0
    skipped_active: int = 0
    conflicts: int = 0


def initialize_ftp_audit_schedules(*, now: datetime) -> int:
    inactive = BackupDestination.objects.filter(
        Q(enabled=False) | Q(integrity_audit_enabled=False) | ~Q(protocol="ftp")
    ).exclude(next_integrity_audit_at=None)
    inactive.update(next_integrity_audit_at=None)

    destinations = list(
        BackupDestination.objects.filter(
            enabled=True,
            protocol="ftp",
            integrity_audit_enabled=True,
            next_integrity_audit_at__isnull=True,
        )
    )
    for destination in destinations:
        destination.next_integrity_audit_at = calculate_destination_next_ftp_audit(
            destination,
            now=now,
            timezone_name=settings.TIME_ZONE,
        )
        destination.save(update_fields=("next_integrity_audit_at", "last_updated"))
    return len(destinations)


def dispatch_due_ftp_audits(
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> FtpAuditDispatchSummary:
    if limit <= 0:
        raise ValueError("FTP audit dispatcher limit must be positive.")
    now = now or timezone.now()
    summary = FtpAuditDispatchSummary(initialized=initialize_ftp_audit_schedules(now=now))
    content_type = ContentType.objects.get_for_model(BackupDestination)
    candidate_ids = list(
        BackupDestination.objects.filter(
            enabled=True,
            protocol="ftp",
            integrity_audit_enabled=True,
            next_integrity_audit_at__lte=now,
        )
        .order_by("next_integrity_audit_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    summary.due = len(candidate_ids)

    for destination_id in candidate_ids:
        try:
            with transaction.atomic():
                destination = BackupDestination.objects.select_for_update(of=("self",)).get(
                    pk=destination_id
                )
                if (
                    not destination.enabled
                    or destination.protocol != "ftp"
                    or not destination.integrity_audit_enabled
                    or not destination.next_integrity_audit_at
                    or destination.next_integrity_audit_at > now
                ):
                    continue

                scheduled_for = destination.next_integrity_audit_at
                destination.next_integrity_audit_at = calculate_next_ftp_audit(
                    destination,
                    after=scheduled_for,
                    now=now,
                    timezone_name=settings.TIME_ZONE,
                )
                destination.save(update_fields=("next_integrity_audit_at", "last_updated"))

                active = Job.objects.filter(
                    object_type=content_type,
                    object_id=destination.pk,
                    name=AUDIT_JOB_NAME,
                    status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
                ).exists()
                if active:
                    summary.skipped_active += 1
                    continue

                from netbox_config_backup.jobs import (
                    BACKUP_QUEUE,
                    DestinationReconciliationJob,
                )

                DestinationReconciliationJob.enqueue(
                    destination_id=destination.pk,
                    scheduled_for=scheduled_for.isoformat(),
                    instance=destination,
                    queue_name=BACKUP_QUEUE,
                )
                summary.queued += 1
        except IntegrityError:
            summary.conflicts += 1
    return summary
