"""Exercise the automatic FTP integrity-audit scheduler against a live destination."""

import time
from datetime import timedelta

from core.models import Job
from django.utils import timezone

from netbox_config_backup.models import BackupDestination
from netbox_config_backup.services.ftp_audit_dispatcher import (
    AUDIT_JOB_NAME,
    dispatch_due_ftp_audits,
)


def wait_for_job(job: Job, timeout: float = 90.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job.refresh_from_db()
        if job.status not in {"pending", "scheduled", "running"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"Job {job.pk} did not finish; current status={job.status}")


destination = (
    BackupDestination.objects.filter(
        enabled=True,
        protocol="ftp",
        replicas__status="success",
    )
    .distinct()
    .order_by("pk")
    .first()
)
assert destination is not None, "An enabled FTP destination with a successful replica is required."

original = {
    "integrity_audit_enabled": destination.integrity_audit_enabled,
    "integrity_audit_frequency": destination.integrity_audit_frequency,
    "integrity_audit_time": destination.integrity_audit_time,
    "integrity_audit_weekday": destination.integrity_audit_weekday,
    "next_integrity_audit_at": destination.next_integrity_audit_at,
}

try:
    now = timezone.now()
    BackupDestination.objects.filter(pk=destination.pk).update(
        integrity_audit_enabled=True,
        integrity_audit_frequency="daily",
        integrity_audit_time="04:00",
        next_integrity_audit_at=None,
    )

    initialized = dispatch_due_ftp_audits(now=now)
    destination.refresh_from_db()
    assert initialized.initialized == 1, initialized
    assert initialized.queued == 0, initialized
    assert destination.next_integrity_audit_at > now

    due_at = now - timedelta(minutes=1)
    BackupDestination.objects.filter(pk=destination.pk).update(next_integrity_audit_at=due_at)
    before = Job.objects.filter(name=AUDIT_JOB_NAME, object_id=destination.pk).count()

    first = dispatch_due_ftp_audits(now=now)
    assert first.due == 1 and first.queued == 1, first
    destination.refresh_from_db()
    assert destination.next_integrity_audit_at > now

    second = dispatch_due_ftp_audits(now=now)
    assert second.due == 0 and second.queued == 0, second
    assert Job.objects.filter(name=AUDIT_JOB_NAME, object_id=destination.pk).count() == before + 1

    job = wait_for_job(
        Job.objects.filter(name=AUDIT_JOB_NAME, object_id=destination.pk).latest("created")
    )
    assert job.status == "completed", (job.status, job.data)
    destination.refresh_from_db()
    assert destination.last_integrity_audit_status == "healthy", (
        destination.last_integrity_audit_status,
        job.data,
    )
    assert destination.last_integrity_audit_problem_count == 0

    print(
        {
            "destination": destination.name,
            "initialized": initialized.initialized,
            "queued": first.queued,
            "duplicate_jobs": 0,
            "job_status": job.status,
            "audit_status": destination.last_integrity_audit_status,
            "checked_files": job.data["destination_reconciliation"]["checked_files"],
        }
    )
finally:
    BackupDestination.objects.filter(pk=destination.pk).update(**original)
