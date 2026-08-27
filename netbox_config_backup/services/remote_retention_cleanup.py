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
    RemoteRetentionPolicy,
    RevisionReplica,
)

from .destination_ftp import delete_revision_replica_ftp
from .destination_types import DestinationError
from .retention import (
    RevisionCandidate,
    build_retention_plan,
    effective_remote_retention_policy_id,
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
            if BackupRun.objects.filter(
                target=target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists():
                raise RemoteRetentionCleanupError(
                    "FTP retention cannot run while the target has an active backup."
                )

            # Serialize retention with enqueue/retry workers. Without row locks,
            # a retry can upload while retention is deleting the same path and
            # then clear the tombstone in _mark_success().
            locked_replicas = list(
                RevisionReplica.objects.select_for_update()
                .filter(
                    revision__target=target,
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
            destination_ids = {replica.destination_id for replica in locked_replicas}
            locked_destinations = (
                BackupDestination.objects.select_for_update(of=("self",))
                .select_related("remote_retention_policy")
                .filter(pk__in=destination_ids, protocol="ftp")
                .order_by("pk")
                .in_bulk()
            )
            for replica in locked_replicas:
                replica.destination = locked_destinations[replica.destination_id]

            # A retention profile controls irreversible FTP deletion. Lock
            # every effective row before calculating a plan so an administrator
            # cannot change its windows or cap halfway through cleanup.
            policy_ids_by_destination = {
                destination_id: effective_remote_retention_policy_id(target, destination)
                for destination_id, destination in locked_destinations.items()
            }
            locked_policies = (
                RemoteRetentionPolicy.objects.select_for_update()
                .filter(
                    pk__in={
                        policy_id
                        for policy_id in policy_ids_by_destination.values()
                        if policy_id is not None
                    }
                )
                .order_by("pk")
                .in_bulk()
            )
            replicas_by_destination: dict[int, dict[int, list[RevisionReplica]]] = {}
            for replica in locked_replicas:
                replicas_by_destination.setdefault(replica.destination_id, {}).setdefault(
                    replica.revision_id, []
                ).append(replica)

            revisions = list(
                ConfigRevision.objects.select_for_update()
                .filter(
                    target=target,
                    pk__in={replica.revision_id for replica in locked_replicas},
                )
                .prefetch_related("artifacts", "replicas")
                .order_by("-created", "-pk")
            )
            revision_by_id = {revision.pk: revision for revision in revisions}

            replica_count = 0
            cancelled_replica_count = 0
            deleted_file_count = 0
            missing_file_count = 0
            deleted_bytes = 0
            removed_directory_count = 0
            deferred_revision_count = 0
            completed_revision_ids: set[int] = set()
            configured_policy_count = 0

            for destination_id, destination in locked_destinations.items():
                if not destination.enabled:
                    continue
                policy_id = policy_ids_by_destination[destination_id]
                if policy_id is None:
                    continue
                policy = locked_policies.get(policy_id)
                if policy is None:
                    raise RemoteRetentionCleanupError(
                        "The effective FTP retention profile no longer exists."
                    )
                configured_policy_count += 1
                replicas_by_revision = replicas_by_destination.get(destination_id, {})
                candidate_revisions = [
                    revision_by_id[revision_id]
                    for revision_id, replicas in replicas_by_revision.items()
                    if revision_id in revision_by_id
                    and any(
                        (
                            replica.status == ReplicaStatusChoices.SUCCESS
                            and replica.remote_available
                        )
                        or (
                            replica.status == ReplicaStatusChoices.FAILED
                            and replica.remote_path
                            and replica.next_retry_at is None
                        )
                        for replica in replicas
                    )
                ]
                plan = build_retention_plan(
                    settings_from_remote_policy(policy),
                    revisions=(
                        RevisionCandidate(
                            object_id=revision.pk,
                            created=revision.created,
                            protected=revision.protected,
                            content_changed=revision.content_changed,
                        )
                        for revision in candidate_revisions
                    ),
                    runs=(),
                    now=generated_at,
                )
                expired_ids = {
                    decision.object_id for decision in plan.revision_decisions if not decision.keep
                }

                for revision_id in expired_ids:
                    revision_replicas = replicas_by_revision.get(revision_id, [])
                    if any(
                        replica.status
                        in (ReplicaStatusChoices.QUEUED, ReplicaStatusChoices.RUNNING)
                        or (
                            replica.status == ReplicaStatusChoices.FAILED
                            and replica.next_retry_at is not None
                        )
                        for replica in revision_replicas
                    ):
                        deferred_revision_count += 1
                        continue
                    replicas = [
                        replica
                        for replica in revision_replicas
                        if (
                            replica.status == ReplicaStatusChoices.SUCCESS
                            and replica.remote_available
                        )
                        or (
                            replica.status == ReplicaStatusChoices.FAILED
                            and replica.remote_path
                            and replica.next_retry_at is None
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
                    completed_revision_ids.add(revision_id)

            if configured_policy_count == 0:
                raise RemoteRetentionCleanupError(
                    "No enabled FTP storage has an effective retention profile. "
                    "FTP copies are kept indefinitely."
                )

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
                revision_count=len(completed_revision_ids),
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
