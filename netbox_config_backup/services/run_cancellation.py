from __future__ import annotations

from dataclasses import dataclass

from core.choices import JobStatusChoices
from core.models import Job
from django.db import transaction
from django.utils import timezone

from netbox_config_backup.choices import RunStatusChoices
from netbox_config_backup.models import BackupRun


class BackupRunCancellationError(RuntimeError):
    """A backup run cannot be cancelled safely; the message is safe for the UI."""


@dataclass(frozen=True, slots=True)
class BackupRunCancellationResult:
    run_id: int
    job_removed: bool


@transaction.atomic
def cancel_queued_backup_run(run_id: int) -> BackupRunCancellationResult:
    """Cancel an RQ job and release a backup target without deleting its audit row."""
    run = BackupRun.objects.select_for_update().get(pk=run_id)
    if run.status != RunStatusChoices.QUEUED:
        raise BackupRunCancellationError(
            "Only a backup run which is still queued can be cancelled."
        )

    job = (
        Job.objects.select_for_update().filter(job_id=run.job_id).first()
        if run.job_id
        else None
    )
    if job and job.status == JobStatusChoices.STATUS_RUNNING:
        raise BackupRunCancellationError(
            "The worker has already started this backup; wait for it to finish or become stale."
        )

    # NetBox's Job.delete() also cancels/removes the corresponding RQ job from
    # Redis. Do this before releasing the active-run database constraint.
    if job:
        try:
            job.delete()
        except Exception as exc:
            raise BackupRunCancellationError(
                "The queued Redis job could not be cancelled. Nothing was changed."
            ) from exc

    run.status = RunStatusChoices.SKIPPED
    run.finished_at = timezone.now()
    run.error_code = "CANCELLED"
    run.error_message = "Queued backup was cancelled before execution."
    run.save(
        update_fields=(
            "status",
            "finished_at",
            "error_code",
            "error_message",
            "last_updated",
        )
    )
    return BackupRunCancellationResult(run_id=run.pk, job_removed=job is not None)
