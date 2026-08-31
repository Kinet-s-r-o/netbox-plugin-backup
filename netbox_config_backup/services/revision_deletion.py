from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.db import transaction

from netbox_config_backup.choices import (
    REPLICATED_DESTINATION_PROTOCOLS,
    ReplicaStatusChoices,
    RunStatusChoices,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    RevisionReplica,
)
from netbox_config_backup.storage.base import ConfigStorage, StorageError
from netbox_config_backup.storage.factory import build_config_storage

from .destination import delete_revision_replica
from .destination_types import DestinationError
from .revision_chain import relink_kept_revisions

logger = logging.getLogger("netbox_config_backup.storage")


class RevisionDeletionError(RuntimeError):
    """A revision could not be safely removed; the message is safe for the UI."""


@dataclass(frozen=True, slots=True)
class RevisionDeletionSummary:
    revision_id: int
    artifact_count: int
    artifact_bytes: int
    local_file_count: int
    missing_local_file_count: int
    replica_count: int
    remote_file_count: int
    missing_remote_file_count: int
    detached_run_count: int
    quarantine_purge_failures: int = 0


def _default_storage() -> ConfigStorage:
    return build_config_storage(settings.PLUGINS_CONFIG["netbox_config_backup"])


def _restore_staged(storage: ConfigStorage, staged: list[tuple[str, str]]) -> bool:
    failed = False
    for original_key, staged_key in reversed(staged):
        try:
            storage.restore_staged_delete(original_key, staged_key)
        except StorageError:
            failed = True
    return failed


def _validate_replicas(replicas: list[RevisionReplica]) -> None:
    for replica in replicas:
        has_recorded_copy = bool(replica.remote_available or replica.remote_path)
        if replica.status in {
            ReplicaStatusChoices.QUEUED,
            ReplicaStatusChoices.RUNNING,
        } or (
            replica.status == ReplicaStatusChoices.FAILED
            and replica.next_retry_at is not None
        ):
            raise RevisionDeletionError(
                "The revision has an active or scheduled remote transfer and cannot be deleted."
            )
        if not has_recorded_copy or replica.remote_deleted_at is not None:
            continue
        if replica.destination.protocol not in REPLICATED_DESTINATION_PROTOCOLS:
            raise RevisionDeletionError(
                "The revision has an external copy which this version cannot remove safely."
            )
        if not replica.destination.enabled:
            raise RevisionDeletionError(
                "A remote storage containing this revision is disabled. Enable it before "
                "deleting the revision so its copy can be removed safely."
            )
        if not replica.remote_path:
            raise RevisionDeletionError(
                "A remote copy has inconsistent path metadata. The revision was not deleted."
            )


def delete_config_revision_everywhere(
    revision_id: int,
    *,
    storage: ConfigStorage | None = None,
) -> RevisionDeletionSummary:
    """Delete one revision's local/remote data and database metadata safely.

    BackupRun rows are retained as audit history. Their nullable revision link is
    cleared by the model relationship when the revision is deleted.
    """

    target_storage = storage or _default_storage()
    namespace = f"revision-delete-{revision_id}-{uuid4().hex}"
    staged: list[tuple[str, str]] = []

    try:
        with transaction.atomic():
            revision_stub = ConfigRevision.objects.only("target_id").get(pk=revision_id)
            target = BackupTarget.objects.select_for_update().get(
                pk=revision_stub.target_id
            )
            revision = (
                ConfigRevision.objects.select_for_update()
                .select_related("target__device")
                .get(pk=revision_id, target=target)
            )
            if revision.protected:
                raise RevisionDeletionError(
                    "The revision is protected. Unprotect it before deleting it."
                )
            if BackupRun.objects.filter(
                target=target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists():
                raise RevisionDeletionError(
                    "The target has an active backup run. Wait for it to finish before "
                    "deleting the revision."
                )

            artifacts = list(
                ConfigArtifact.objects.select_for_update()
                .filter(revision=revision)
                .order_by("pk")
            )
            replicas = list(
                RevisionReplica.objects.select_for_update()
                .filter(revision=revision)
                .select_related("destination", "revision__target__device")
                .prefetch_related("revision__artifacts")
                .order_by("pk")
            )
            destinations = (
                BackupDestination.objects.select_for_update()
                .filter(pk__in={replica.destination_id for replica in replicas})
                .in_bulk()
            )
            for replica in replicas:
                replica.destination = destinations[replica.destination_id]
            _validate_replicas(replicas)

            missing_local_file_count = 0
            for artifact in artifacts:
                staged_key = target_storage.stage_delete(
                    artifact.storage_key,
                    namespace,
                )
                if staged_key is None:
                    missing_local_file_count += 1
                else:
                    staged.append((artifact.storage_key, staged_key))

            remote_file_count = 0
            missing_remote_file_count = 0
            deleted_replica_count = 0
            for replica in replicas:
                if replica.remote_deleted_at is not None or not (
                    replica.remote_available or replica.remote_path
                ):
                    continue
                try:
                    result = delete_revision_replica(replica)
                except DestinationError as exc:
                    raise RevisionDeletionError(
                        f"A remote copy could not be removed safely ({exc.error_code}). "
                        "The revision was not deleted."
                    ) from exc
                deleted_replica_count += 1
                remote_file_count += result.deleted_file_count
                missing_remote_file_count += result.missing_file_count

            revisions = list(
                ConfigRevision.objects.select_for_update()
                .filter(target=target)
                .order_by("created", "pk")
            )
            relink_kept_revisions(target, revisions, {revision.pk})
            detached_run_count = BackupRun.objects.filter(revision=revision).count()
            deleted_revision_id = revision.pk
            revision.delete()

            summary = RevisionDeletionSummary(
                revision_id=deleted_revision_id,
                artifact_count=len(artifacts),
                artifact_bytes=sum(artifact.size for artifact in artifacts),
                local_file_count=len(staged),
                missing_local_file_count=missing_local_file_count,
                replica_count=deleted_replica_count,
                remote_file_count=remote_file_count,
                missing_remote_file_count=missing_remote_file_count,
                detached_run_count=detached_run_count,
            )
    except Exception as exc:
        restore_failed = _restore_staged(target_storage, staged)
        if restore_failed:
            raise RevisionDeletionError(
                "Revision deletion failed and quarantined local files could not be fully restored."
            ) from exc
        if isinstance(exc, RevisionDeletionError):
            raise
        if isinstance(exc, ConfigRevision.DoesNotExist):
            raise RevisionDeletionError("The revision no longer exists.") from exc
        if isinstance(exc, StorageError):
            raise RevisionDeletionError(
                "Stored configuration files could not be quarantined. Nothing was deleted."
            ) from exc
        logger.exception(
            "Revision %s deletion failed before the database transaction committed.",
            revision_id,
        )
        raise RevisionDeletionError("Revision deletion failed before commit.") from exc

    purge_failures = 0
    for _original_key, staged_key in staged:
        try:
            target_storage.purge_staged_delete(staged_key)
        except StorageError:
            purge_failures += 1
            logger.warning(
                "A revision-deletion quarantine object could not be purged for revision %s.",
                revision_id,
            )
    if purge_failures:
        summary = RevisionDeletionSummary(
            revision_id=summary.revision_id,
            artifact_count=summary.artifact_count,
            artifact_bytes=summary.artifact_bytes,
            local_file_count=summary.local_file_count,
            missing_local_file_count=summary.missing_local_file_count,
            replica_count=summary.replica_count,
            remote_file_count=summary.remote_file_count,
            missing_remote_file_count=summary.missing_remote_file_count,
            detached_run_count=summary.detached_run_count,
            quarantine_purge_failures=purge_failures,
        )
    return summary
