from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.db import transaction

from netbox_config_backup.choices import RunStatusChoices
from netbox_config_backup.models import (
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
)
from netbox_config_backup.storage.base import ConfigStorage, StorageError
from netbox_config_backup.storage.factory import build_config_storage

logger = logging.getLogger("netbox_config_backup.storage")


class TargetDeletionError(RuntimeError):
    """A target could not be safely removed; the message is safe for the UI."""


@dataclass(frozen=True, slots=True)
class TargetDeletionSummary:
    run_count: int
    revision_count: int
    artifact_count: int
    quick_connection: bool
    quick_credential: bool


def summarize_target_deletion(target: BackupTarget) -> TargetDeletionSummary:
    return TargetDeletionSummary(
        run_count=BackupRun.objects.filter(target=target).count(),
        revision_count=ConfigRevision.objects.filter(target=target).count(),
        artifact_count=ConfigArtifact.objects.filter(revision__target=target).count(),
        quick_connection=bool(
            target.connection_override and target.connection_override.name.startswith("[Quick]")
        ),
        quick_credential=bool(
            target.credential_override and target.credential_override.name.startswith("[Quick]")
        ),
    )


def _default_storage() -> ConfigStorage:
    return build_config_storage(settings.PLUGINS_CONFIG["netbox_config_backup"])


def delete_backup_target(
    target: BackupTarget,
    *,
    storage: ConfigStorage | None = None,
) -> TargetDeletionSummary:
    """Delete a target, its audit history, stored artifacts, and unshared Quick profiles."""
    target_storage = storage or _default_storage()
    namespace = f"target-delete-{target.pk}-{uuid4().hex}"
    staged: list[tuple[str, str]] = []
    try:
        with transaction.atomic():
            locked_target = BackupTarget.objects.select_for_update().get(pk=target.pk)
            if BackupRun.objects.filter(
                target=locked_target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists():
                raise TargetDeletionError(
                    "The target has an active backup run and cannot be deleted."
                )

            summary = summarize_target_deletion(locked_target)
            artifact_keys = list(
                ConfigArtifact.objects.select_for_update()
                .filter(revision__target=locked_target)
                .values_list("storage_key", flat=True)
            )
            for key in artifact_keys:
                staged_key = target_storage.stage_delete(key, namespace)
                if staged_key is not None:
                    staged.append((key, staged_key))

            connection_id = locked_target.connection_override_id
            credential_id = locked_target.credential_override_id

            BackupRun.objects.filter(target=locked_target).delete()
            ConfigRevision.objects.filter(target=locked_target).delete()
            locked_target.delete()

            if connection_id:
                connection = ConnectionProfile.objects.filter(pk=connection_id).first()
                if (
                    connection
                    and connection.name.startswith("[Quick]")
                    and not connection.target_overrides.exists()
                    and not connection.platform_mappings.exists()
                ):
                    connection.delete()

            if credential_id:
                credential = CredentialProfile.objects.filter(pk=credential_id).first()
                if (
                    credential
                    and credential.name.startswith("[Quick]")
                    and not credential.target_overrides.exists()
                    and not credential.platform_mappings.exists()
                ):
                    credential.delete()
    except Exception as exc:
        restore_failed = False
        for original_key, staged_key in reversed(staged):
            try:
                target_storage.restore_staged_delete(original_key, staged_key)
            except StorageError:
                restore_failed = True
        if restore_failed:
            raise TargetDeletionError(
                "Target deletion failed and quarantined files could not be fully restored."
            ) from exc
        if isinstance(exc, TargetDeletionError):
            raise
        if isinstance(exc, StorageError):
            raise TargetDeletionError(
                "Stored configuration files could not be quarantined. Nothing was deleted."
            ) from exc
        raise TargetDeletionError("Target deletion failed before commit.") from exc

    for _original_key, staged_key in staged:
        try:
            target_storage.purge_staged_delete(staged_key)
        except StorageError:
            logger.warning("A target-deletion quarantine object could not be purged.")

    return summary
