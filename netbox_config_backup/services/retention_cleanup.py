from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from netbox_config_backup.choices import (
    DestinationProtocolChoices,
    ReplicaStatusChoices,
    RunStatusChoices,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    RetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.factory import build_config_storage

from .retention import (
    RevisionCandidate,
    RunCandidate,
    build_retention_plan,
    effective_local_retention_policy,
    has_recorded_remote_copy,
    settings_from_policy,
)


class RetentionCleanupError(RuntimeError):
    """A retention cleanup was aborted with a message safe for the job log."""


@dataclass(frozen=True, slots=True)
class RetentionCleanupSummary:
    target_id: int
    run_count: int
    revision_count: int
    artifact_count: int
    artifact_bytes: int
    missing_artifact_count: int
    quarantine_purge_failures: int
    deferred_revision_count: int = 0


def _default_storage():
    return build_config_storage(settings.PLUGINS_CONFIG["netbox_config_backup"])


def execute_retention_cleanup(
    target_id: int,
    *,
    storage=None,
    now=None,
) -> RetentionCleanupSummary:
    """Recompute and execute one target's retention plan with reversible file staging."""
    target_storage = storage or _default_storage()
    generated_at = now or timezone.now()
    namespace = f"retention-{target_id}-{uuid4().hex}"
    staged: list[tuple[str, str]] = []

    try:
        with transaction.atomic():
            target = (
                BackupTarget.objects.select_for_update(of=("self",))
                .select_related("policy_override", "remote_retention_policy")
                .get(pk=target_id)
            )
            if BackupRun.objects.filter(
                target=target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists():
                raise RetentionCleanupError(
                    "Retention cleanup cannot run while the target has an active backup."
                )

            local_storage = (
                BackupDestination.objects.select_for_update(of=("self",))
                .select_related("local_retention_policy")
                .get(
                    protocol=DestinationProtocolChoices.LOCAL,
                    is_default=True,
                )
            )
            effective_policy = effective_local_retention_policy(target, local_storage)
            if effective_policy is None:
                raise RetentionCleanupError(
                    "The backup target has no effective Local retention profile."
                )
            policy = RetentionPolicy.objects.select_for_update().get(pk=effective_policy.pk)

            revisions = list(
                ConfigRevision.objects.select_for_update()
                .filter(target=target)
                .prefetch_related("artifacts")
                .order_by("-created", "-pk")
            )
            locked_replicas = list(
                RevisionReplica.objects.select_for_update()
                .filter(revision__target=target)
                .select_related("destination")
                .order_by("pk")
            )
            replicas_by_revision: dict[int, list[RevisionReplica]] = {}
            for replica in locked_replicas:
                replicas_by_revision.setdefault(replica.revision_id, []).append(replica)
            runs = list(
                BackupRun.objects.select_for_update()
                .filter(target=target)
                .order_by("-queued_at", "-pk")
            )
            locally_available_revisions = [
                revision
                for revision in revisions
                if any(artifact.local_available for artifact in revision.artifacts.all())
            ]
            plan = build_retention_plan(
                settings_from_policy(policy),
                revisions=(
                    RevisionCandidate(
                        object_id=revision.pk,
                        created=revision.created,
                        protected=revision.protected,
                        content_changed=revision.content_changed,
                    )
                    for revision in locally_available_revisions
                ),
                runs=(
                    RunCandidate(
                        object_id=run.pk,
                        timestamp=run.finished_at or run.queued_at,
                        status=run.status,
                    )
                    for run in runs
                ),
                now=generated_at,
            )
            planned_revision_ids = {
                decision.object_id for decision in plan.revision_decisions if not decision.keep
            }
            run_ids = {decision.object_id for decision in plan.run_decisions if not decision.keep}

            # Never remove the upload source while a remote copy is active or has
            # a scheduled retry. An exhausted failure is no longer an active
            # replication obligation and must not retain local data forever.
            deferred_revision_ids = {
                revision.pk
                for revision in locally_available_revisions
                if revision.pk in planned_revision_ids
                and any(
                    replica.remote_deleted_at is None
                    and (
                        replica.status
                        in (ReplicaStatusChoices.QUEUED, ReplicaStatusChoices.RUNNING)
                        or (
                            replica.destination.enabled
                            and replica.status == ReplicaStatusChoices.PENDING
                        )
                        or (
                            replica.destination.enabled
                            and replica.status == ReplicaStatusChoices.FAILED
                            and replica.next_retry_at is not None
                        )
                    )
                    for replica in replicas_by_revision.get(revision.pk, ())
                )
            }
            revision_ids = planned_revision_ids - deferred_revision_ids
            artifacts = list(
                ConfigArtifact.objects.select_for_update().filter(
                    revision_id__in=revision_ids,
                    local_available=True,
                )
            )

            missing_artifact_count = 0
            for artifact in artifacts:
                staged_key = target_storage.stage_delete(artifact.storage_key, namespace)
                if staged_key is None:
                    missing_artifact_count += 1
                else:
                    staged.append((artifact.storage_key, staged_key))

            if artifacts:
                ConfigArtifact.objects.filter(
                    pk__in=(artifact.pk for artifact in artifacts)
                ).update(
                    local_available=False,
                    local_deleted_at=generated_at,
                    last_updated=generated_at,
                )

            remotely_preserved_revision_ids = {
                revision.pk
                for revision in revisions
                if revision.pk in revision_ids
                and has_recorded_remote_copy(replicas_by_revision.get(revision.pk, ()))
            }
            database_revision_ids = revision_ids - remotely_preserved_revision_ids
            locally_unavailable_ids = {
                revision.pk
                for revision in revisions
                if revision.pk in revision_ids
                or not any(artifact.local_available for artifact in revision.artifacts.all())
            }
            _relink_kept_revisions(target, revisions, locally_unavailable_ids)
            BackupRun.objects.filter(pk__in=run_ids).delete()
            ConfigRevision.objects.filter(pk__in=database_revision_ids).delete()

            summary = RetentionCleanupSummary(
                target_id=target.pk,
                run_count=len(run_ids),
                revision_count=len(revision_ids),
                artifact_count=len(artifacts),
                artifact_bytes=sum(artifact.size for artifact in artifacts),
                missing_artifact_count=missing_artifact_count,
                quarantine_purge_failures=0,
                deferred_revision_count=len(deferred_revision_ids),
            )
    except Exception as exc:
        restore_failed = _restore_staged(target_storage, staged)
        if restore_failed:
            raise RetentionCleanupError(
                "Retention cleanup failed and quarantined files could not be fully restored."
            ) from exc
        if isinstance(exc, RetentionCleanupError):
            raise
        if isinstance(exc, StorageError):
            raise RetentionCleanupError(
                "Retention cleanup could not stage stored configuration files. Nothing was deleted."
            ) from exc
        raise RetentionCleanupError(
            "Retention cleanup failed before the database transaction committed."
        ) from exc

    purge_failures = 0
    for _original_key, staged_key in staged:
        try:
            target_storage.purge_staged_delete(staged_key)
        except StorageError:
            purge_failures += 1
    if purge_failures:
        summary = RetentionCleanupSummary(
            target_id=summary.target_id,
            run_count=summary.run_count,
            revision_count=summary.revision_count,
            artifact_count=summary.artifact_count,
            artifact_bytes=summary.artifact_bytes,
            missing_artifact_count=summary.missing_artifact_count,
            quarantine_purge_failures=purge_failures,
            deferred_revision_count=summary.deferred_revision_count,
        )
    return summary


def _relink_kept_revisions(target, revisions, deleted_ids: set[int]) -> None:
    kept = sorted(
        (revision for revision in revisions if revision.pk not in deleted_ids),
        key=lambda revision: (revision.created, revision.pk),
    )
    previous = None
    for revision in kept:
        previous_id = previous.pk if previous else None
        if revision.previous_revision_id != previous_id:
            revision.previous_revision_id = previous_id
            revision.save(update_fields=("previous_revision", "last_updated"))
        previous = revision
    latest_id = kept[-1].pk if kept else None
    if target.last_revision_id != latest_id:
        target.last_revision_id = latest_id
        target.save(update_fields=("last_revision", "last_updated"))


def _restore_staged(storage, staged: list[tuple[str, str]]) -> bool:
    failed = False
    for original_key, staged_key in reversed(staged):
        try:
            storage.restore_staged_delete(original_key, staged_key)
        except StorageError:
            failed = True
    return failed
