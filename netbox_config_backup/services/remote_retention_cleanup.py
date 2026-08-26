from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from netbox_config_backup.choices import ReplicaStatusChoices, RunStatusChoices
from netbox_config_backup.models import (
    BackupDestination,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    RevisionReplica,
)

from .destination_ftp import delete_revision_replica_ftp
from .destination_types import DestinationError
from .retention import (
    RevisionCandidate,
    build_retention_plan,
    settings_from_remote_policy,
)


class RemoteRetentionCleanupError(RuntimeError):
    """An FTP retention cleanup stopped safely."""


@dataclass(frozen=True, slots=True)
class RemoteRetentionCleanupSummary:
    target_id: int
    revision_count: int
    replica_count: int
    cancelled_replica_count: int
    deleted_file_count: int
    missing_file_count: int
    deleted_bytes: int
    removed_directory_count: int
    deferred_revision_count: int
    metadata_revision_count: int


def execute_remote_retention_cleanup(
    target_id: int,
    *,
    now=None,
) -> RemoteRetentionCleanupSummary:
    """Apply one target's FTP policy without touching local artifact bytes.

    The database transaction intentionally remains open while FTP objects are
    removed. FTP deletion cannot be rolled back, but the operation is strictly
    scoped and idempotent: if a later delete fails, the database remains
    unchanged and the next run safely accepts already absent files.
    """

    generated_at = now or timezone.now()
    try:
        with transaction.atomic():
            target = (
                BackupTarget.objects.select_for_update(of=("self",))
                .select_related("remote_retention_policy")
                .get(pk=target_id)
            )
            policy = target.remote_retention_policy
            if policy is None:
                raise RemoteRetentionCleanupError(
                    "The backup target has no FTP retention profile. FTP copies are kept indefinitely."
                )
            if BackupRun.objects.filter(
                target=target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists():
                raise RemoteRetentionCleanupError(
                    "FTP retention cannot run while the target has an active backup."
                )

            candidate_revision_ids = (
                RevisionReplica.objects.filter(
                    revision__target=target,
                    destination__protocol="ftp",
                    destination__enabled=True,
                    remote_deleted_at__isnull=True,
                )
                .filter(
                    Q(
                        status=ReplicaStatusChoices.SUCCESS,
                        remote_available=True,
                    )
                    | Q(
                        status=ReplicaStatusChoices.FAILED,
                        remote_path__gt="",
                        next_retry_at__isnull=True,
                    )
                )
                .values_list("revision_id", flat=True)
            )
            revisions = list(
                ConfigRevision.objects.select_for_update()
                .filter(target=target, pk__in=candidate_revision_ids)
                .prefetch_related("artifacts")
                .order_by("-created", "-pk")
            )
            # Serialize retention with enqueue/retry workers. Without row locks,
            # a retry can upload while retention is deleting the same path and
            # then clear the tombstone in _mark_success().
            locked_replicas = list(
                RevisionReplica.objects.select_for_update()
                .filter(
                    revision_id__in=(revision.pk for revision in revisions),
                    destination__protocol="ftp",
                    remote_deleted_at__isnull=True,
                )
                .select_related("destination", "revision__target__device")
                .prefetch_related("revision__artifacts")
                .order_by("pk")
            )
            # Lock destination configuration after replica rows (the same lock
            # order used by replication completion) and replace the related
            # object cache with the locked, current row. This makes disabling
            # a destination a real deletion kill switch without introducing a
            # replica/destination lock-order inversion.
            locked_destinations = (
                BackupDestination.objects.select_for_update()
                .filter(pk__in={replica.destination_id for replica in locked_replicas})
                .in_bulk()
            )
            for replica in locked_replicas:
                replica.destination = locked_destinations[replica.destination_id]
            replicas_by_revision: dict[int, list[RevisionReplica]] = {}
            for replica in locked_replicas:
                replicas_by_revision.setdefault(replica.revision_id, []).append(replica)
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
                now=generated_at,
            )
            expired_ids = {
                decision.object_id for decision in plan.revision_decisions if not decision.keep
            }

            revision_count = 0
            replica_count = 0
            cancelled_replica_count = 0
            deleted_file_count = 0
            missing_file_count = 0
            deleted_bytes = 0
            removed_directory_count = 0
            deferred_revision_count = 0
            completed_revision_ids: set[int] = set()

            for revision in revisions:
                if revision.pk not in expired_ids:
                    continue
                revision_replicas = replicas_by_revision.get(revision.pk, [])
                if any(
                    replica.destination.enabled
                    and (
                        replica.status
                        in (ReplicaStatusChoices.QUEUED, ReplicaStatusChoices.RUNNING)
                        or (
                            replica.status == ReplicaStatusChoices.FAILED
                            and replica.next_retry_at is not None
                        )
                    )
                    for replica in revision_replicas
                ):
                    deferred_revision_count += 1
                    continue
                replicas = [
                    replica
                    for replica in revision_replicas
                    if replica.destination.enabled
                    and (
                        (
                            replica.status == ReplicaStatusChoices.SUCCESS
                            and replica.remote_available
                        )
                        or (
                            replica.status == ReplicaStatusChoices.FAILED
                            and replica.remote_path
                            and replica.next_retry_at is None
                        )
                    )
                ]
                # The candidate may have changed while this transaction was
                # waiting for its row lock. Never create a tombstone unless a
                # confirmed available copy was locked and is still eligible.
                if not replicas:
                    continue

                for replica in replicas:
                    result = delete_revision_replica_ftp(replica)
                    replica_count += 1
                    deleted_file_count += result.deleted_file_count
                    missing_file_count += result.missing_file_count
                    deleted_bytes += result.deleted_bytes
                    removed_directory_count += int(result.directory_removed)

                    replica.remote_available = False
                    replica.remote_deleted_at = generated_at
                    replica.next_retry_at = None
                    replica.job_id = None
                    replica.save(
                        update_fields=(
                            "remote_available",
                            "remote_deleted_at",
                            "next_retry_at",
                            "job_id",
                            "last_updated",
                        )
                    )
                revision_count += 1
                completed_revision_ids.add(revision.pk)

            metadata_revision_ids = {
                revision.pk
                for revision in revisions
                if revision.pk in completed_revision_ids
                and not any(artifact.local_available for artifact in revision.artifacts.all())
                and not revision.replicas.filter(
                    Q(remote_available=True) | Q(remote_path__gt=""),
                    remote_deleted_at__isnull=True,
                ).exists()
                and not revision.replicas.filter(
                    Q(
                        status__in=(
                            ReplicaStatusChoices.PENDING,
                            ReplicaStatusChoices.QUEUED,
                            ReplicaStatusChoices.RUNNING,
                        )
                    )
                    | Q(
                        status=ReplicaStatusChoices.FAILED,
                        next_retry_at__isnull=False,
                    ),
                    remote_deleted_at__isnull=True,
                ).exists()
            }
            if metadata_revision_ids:
                ConfigRevision.objects.filter(pk__in=metadata_revision_ids).delete()

            return RemoteRetentionCleanupSummary(
                target_id=target.pk,
                revision_count=revision_count,
                replica_count=replica_count,
                cancelled_replica_count=cancelled_replica_count,
                deleted_file_count=deleted_file_count,
                missing_file_count=missing_file_count,
                deleted_bytes=deleted_bytes,
                removed_directory_count=removed_directory_count,
                deferred_revision_count=deferred_revision_count,
                metadata_revision_count=len(metadata_revision_ids),
            )
    except RemoteRetentionCleanupError:
        raise
    except DestinationError as exc:
        raise RemoteRetentionCleanupError(
            f"FTP retention stopped safely ({exc.error_code}). No database state was changed."
        ) from exc
    except BackupTarget.DoesNotExist as exc:
        raise RemoteRetentionCleanupError("The backup target no longer exists.") from exc
    except Exception as exc:
        raise RemoteRetentionCleanupError(
            "FTP retention failed before its database state could be committed."
        ) from exc
