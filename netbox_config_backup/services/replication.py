from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.choices import DestinationProtocolChoices, ReplicaStatusChoices
from netbox_config_backup.models import (
    BackupDestination,
    ConfigRevision,
    RevisionReplica,
)

from .destination import DestinationError, replicate_revision
from .destination_ftp import reconcile_ftp_destination
from .destination_paths import ftp_revision_destination_path


@dataclass(slots=True)
class ReplicationDispatchSummary:
    considered: int = 0
    queued: int = 0
    skipped: int = 0


def create_revision_replicas(revision_id: int) -> int:
    """Create and queue replicas only after the primary revision transaction commits."""
    destination_ids = BackupDestination.objects.filter(
        enabled=True, auto_replicate=True
    ).values_list("pk", flat=True)
    created_ids: list[int] = []
    for destination_id in destination_ids:
        replica, created = RevisionReplica.objects.get_or_create(
            revision_id=revision_id,
            destination_id=destination_id,
        )
        if created:
            created_ids.append(replica.pk)
    for replica_id in created_ids:
        enqueue_revision_replica(replica_id)
    return len(created_ids)


def ensure_revision_replicas(revision_id: int) -> int:
    """Ensure the latest unchanged revision still exists at automatic destinations.

    A successful database record is not sufficient evidence after an FTP server
    was emptied or restored. Existing successful FTP replicas are therefore
    verified read-only and queued again only when a file is missing, unreadable,
    or no longer matches its recorded size/hash.
    """

    destinations = BackupDestination.objects.filter(enabled=True, auto_replicate=True)
    queued = 0
    for destination in destinations:
        replica, created = RevisionReplica.objects.get_or_create(
            revision_id=revision_id,
            destination=destination,
        )
        if created:
            if enqueue_revision_replica(replica.pk) is not None:
                queued += 1
            continue

        if replica.status in {
            ReplicaStatusChoices.QUEUED,
            ReplicaStatusChoices.RUNNING,
        }:
            continue
        if replica.remote_deleted_at is not None:
            # An FTP retention tombstone is intentional. An unchanged backup
            # must not silently recreate the expired remote copy.
            continue

        needs_repair = replica.status != ReplicaStatusChoices.SUCCESS
        if not needs_repair and destination.protocol == DestinationProtocolChoices.FTP:
            try:
                verification = reconcile_ftp_destination(
                    destination,
                    replicas=(replica,),
                )
                needs_repair = not bool(verification["success"])
            except DestinationError:
                # Queue the regular replication workflow so an unavailable FTP
                # destination is recorded and retried through the existing path.
                needs_repair = True

        if needs_repair and enqueue_revision_replica(replica.pk, force=True) is not None:
            queued += 1
    return queued


@transaction.atomic
def enqueue_revision_replica(replica_id: int, *, user=None, force: bool = False):
    from netbox_config_backup.jobs import BACKUP_QUEUE, DestinationReplicationJob

    replica = (
        RevisionReplica.objects.select_for_update().select_related("destination").get(pk=replica_id)
    )
    if not replica.destination.enabled:
        return None
    if replica.remote_deleted_at is not None:
        return None
    if replica.status in {
        ReplicaStatusChoices.QUEUED,
        ReplicaStatusChoices.RUNNING,
    }:
        return None
    if not force and replica.status == ReplicaStatusChoices.SUCCESS:
        return None
    replica.status = ReplicaStatusChoices.QUEUED
    replica.queued_at = timezone.now()
    replica.started_at = None
    replica.finished_at = None
    replica.next_retry_at = None
    replica.error_code = ""
    replica.error_message = ""
    replica.remote_available = False
    replica.save()
    job = DestinationReplicationJob.enqueue(
        replica_id=replica.pk,
        instance=replica.destination,
        user=user,
        queue_name=BACKUP_QUEUE,
    )
    replica.job_id = job.job_id
    replica.save(update_fields=("job_id", "last_updated"))
    return job


def execute_revision_replica(replica_id: int):
    replica = _mark_running(replica_id)

    try:
        if replica.destination.protocol == DestinationProtocolChoices.FTP and replica.remote_path:
            # Repair the immutable copy at its recorded path. Rebuilding a path
            # from the current device name after a NetBox rename would orphan
            # the historical directory and lose the only database pointer to it.
            result = replicate_revision(
                replica.destination,
                replica.revision,
                recorded_remote_path=replica.remote_path,
            )
        else:
            result = replicate_revision(replica.destination, replica.revision)
    except DestinationError as exc:
        _mark_failed(replica.pk, exc)
        raise
    except Exception as exc:
        safe_error = DestinationError(
            "INTERNAL_ERROR", "External replication stopped because of an internal error."
        )
        _mark_failed(replica.pk, safe_error)
        raise safe_error from exc
    _mark_success(replica.pk, result.remote_path, result.bytes_transferred)
    return result


@transaction.atomic
def _mark_running(replica_id: int) -> RevisionReplica:
    replica = (
        RevisionReplica.objects.select_for_update()
        .select_related(
            "destination__credential_profile",
            "revision__target__device",
        )
        .prefetch_related("revision__artifacts")
        .get(pk=replica_id)
    )
    if not replica.destination.enabled:
        raise DestinationError(
            "DESTINATION_DISABLED",
            "The external destination is disabled and no network connection was attempted.",
        )
    if replica.remote_deleted_at is not None:
        raise DestinationError(
            "REPLICA_EXPIRED",
            "The FTP revision copy has expired and cannot be uploaded again.",
        )
    if replica.status not in {
        ReplicaStatusChoices.PENDING,
        ReplicaStatusChoices.QUEUED,
        ReplicaStatusChoices.FAILED,
    }:
        raise DestinationError(
            "REPLICA_STATE_CONFLICT", "The revision replica is not ready to run."
        )
    if replica.destination.protocol == DestinationProtocolChoices.FTP and not replica.remote_path:
        # Record the deterministic path before any network write. If the
        # worker fails after creating a partial directory, retention and target
        # deletion still have an exact immutable location to reconcile.
        replica.remote_path = ftp_revision_destination_path(
            replica.destination.base_path,
            device_name=replica.revision.target.device.name,
            device_id=replica.revision.target.device_id,
            created_at=replica.revision.created,
            revision_id=replica.revision.pk,
        )
    replica.status = ReplicaStatusChoices.RUNNING
    replica.started_at = timezone.now()
    replica.finished_at = None
    replica.attempts += 1
    replica.error_code = ""
    replica.error_message = ""
    replica.remote_available = False
    replica.save()
    return replica


@transaction.atomic
def _mark_success(replica_id: int, remote_path: str, bytes_transferred: int) -> None:
    now = timezone.now()
    replica = (
        RevisionReplica.objects.select_for_update().select_related("destination").get(pk=replica_id)
    )
    replica.status = ReplicaStatusChoices.SUCCESS
    replica.finished_at = now
    replica.next_retry_at = None
    replica.remote_path = remote_path
    replica.bytes_transferred = bytes_transferred
    replica.error_code = ""
    replica.error_message = ""
    replica.remote_available = True
    replica.remote_deleted_at = None
    replica.save()
    destination = replica.destination
    was_failed = bool(destination.last_error_code)
    destination.last_success_at = now
    destination.last_error_code = ""
    destination.last_error_message = ""
    destination.save(
        update_fields=(
            "last_success_at",
            "last_error_code",
            "last_error_message",
            "last_updated",
        )
    )
    if was_failed:
        from netbox_config_backup.events import REPLICA_RECOVERED, queue_replica_event

        queue_replica_event(REPLICA_RECOVERED, replica.pk)


@transaction.atomic
def _mark_failed(replica_id: int, exc: DestinationError) -> None:
    now = timezone.now()
    replica = (
        RevisionReplica.objects.select_for_update().select_related("destination").get(pk=replica_id)
    )
    replica.status = ReplicaStatusChoices.FAILED
    replica.finished_at = now
    replica.error_code = exc.error_code[:64]
    replica.error_message = exc.safe_message[:500]
    replica.remote_available = False
    if replica.attempts <= replica.destination.max_retries:
        replica.next_retry_at = now + timedelta(minutes=replica.destination.retry_delay_minutes)
    else:
        replica.next_retry_at = None
    replica.save()
    destination = replica.destination
    first_failure = not destination.last_error_code
    destination.last_error_code = replica.error_code
    destination.last_error_message = replica.error_message
    destination.save(update_fields=("last_error_code", "last_error_message", "last_updated"))
    if first_failure:
        from netbox_config_backup.events import REPLICA_FAILED, queue_replica_event

        queue_replica_event(REPLICA_FAILED, replica.pk)


def dispatch_due_replicas(*, limit: int = 100) -> ReplicationDispatchSummary:
    now = timezone.now()
    candidate_ids = list(
        RevisionReplica.objects.filter(
            destination__enabled=True,
            remote_deleted_at__isnull=True,
        )
        .filter(
            Q(status=ReplicaStatusChoices.PENDING)
            | Q(status=ReplicaStatusChoices.FAILED, next_retry_at__lte=now)
        )
        .order_by("queued_at")
        .values_list("pk", flat=True)[:limit]
    )
    summary = ReplicationDispatchSummary(considered=len(candidate_ids))
    for replica_id in candidate_ids:
        job = enqueue_revision_replica(replica_id)
        if job is None:
            summary.skipped += 1
        else:
            summary.queued += 1
    return summary


def reconcile_stale_replicas(*, stale_after_minutes: int = 120) -> int:
    from core.choices import JobStatusChoices
    from core.models import Job

    cutoff = timezone.now() - timedelta(minutes=stale_after_minutes)
    candidates = RevisionReplica.objects.filter(
        Q(status=ReplicaStatusChoices.RUNNING, started_at__lte=cutoff)
        | Q(status=ReplicaStatusChoices.QUEUED, queued_at__lte=cutoff),
        remote_deleted_at__isnull=True,
    ).only("pk", "job_id")
    reconciled = 0
    for replica in candidates:
        job = Job.objects.filter(job_id=replica.job_id).only("status").first()
        if job and job.status in JobStatusChoices.ENQUEUED_STATE_CHOICES:
            continue
        _mark_failed(
            replica.pk,
            DestinationError(
                "STALE_REPLICA",
                "The FTP replication job ended without completing its replica record.",
            ),
        )
        reconciled += 1
    return reconciled


def backfill_destination(destination: BackupDestination, *, user=None, limit: int = 1000) -> int:
    revision_ids = (
        ConfigRevision.objects.filter(artifacts__local_available=True)
        .exclude(replicas__destination=destination)
        .distinct()
        .values_list("pk", flat=True)[:limit]
    )
    replica_ids: list[int] = []
    for revision_id in revision_ids:
        replica, created = RevisionReplica.objects.get_or_create(
            revision_id=revision_id,
            destination=destination,
        )
        if created:
            replica_ids.append(replica.pk)
    for replica_id in replica_ids:
        enqueue_revision_replica(replica_id, user=user)
    return len(replica_ids)
