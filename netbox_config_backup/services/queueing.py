from __future__ import annotations

from datetime import datetime

from django.db import transaction

from netbox_config_backup.models import BackupRun, BackupTarget


@transaction.atomic
def enqueue_backup_run(
    target: BackupTarget,
    *,
    source: str,
    user=None,
    scheduled_for: datetime | None = None,
    dedupe_key: str | None = None,
) -> BackupRun:
    from netbox_config_backup.jobs import BACKUP_QUEUE, BackupRunJob

    run = BackupRun.objects.create(
        target=target,
        source=source,
        scheduled_for=scheduled_for,
        dedupe_key=dedupe_key,
        triggered_by=user,
    )
    job = BackupRunJob.enqueue(
        run_id=run.pk,
        user=user,
        queue_name=BACKUP_QUEUE,
    )
    run.job_id = job.job_id
    run.save(update_fields=("job_id", "last_updated"))
    return run
