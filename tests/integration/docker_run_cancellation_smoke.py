"""Verify worker-aware manual queueing and safe cancellation of a queued run."""

from uuid import uuid4

import django_rq
from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from rq.job import JobStatus

from netbox_config_backup.jobs import BACKUP_QUEUE, BackupRunJob
from netbox_config_backup.models import BackupRun, BackupTarget

prefix = f"ncb-cancel-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
device = Device.objects.create(
    name=f"{prefix}-device",
    site=site,
    role=role,
    device_type=device_type,
)
target = BackupTarget.objects.create(device=device, enabled=True, driver_override="fake")
user = get_user_model().objects.create_superuser(
    username=f"{prefix}-admin",
    password=uuid4().hex,
)
client = Client()
client.force_login(user)

try:
    # The CI shell has no dedicated worker. A manual request must fail fast
    # without creating a BackupRun which would remain queued forever.
    manual_url = reverse(
        "plugins:netbox_config_backup:backuptarget_run",
        kwargs={"pk": target.pk},
    )
    response = client.post(manual_url)
    assert response.status_code == 302
    assert not BackupRun.objects.filter(target=target).exists()

    # Create the same queued state directly so the UI cancellation path can be
    # exercised without a worker consuming it.
    run = BackupRun.objects.create(target=target)
    job = BackupRunJob.enqueue(
        run_id=run.pk,
        user=user,
        queue_name=BACKUP_QUEUE,
    )
    run.job_id = job.job_id
    run.save(update_fields=("job_id", "last_updated"))
    queue = django_rq.get_queue(BACKUP_QUEUE)
    assert queue.fetch_job(str(job.job_id)) is not None

    detail = client.get(run.get_absolute_url())
    assert detail.status_code == 200
    assert b"Cancel queued backup" in detail.content

    cancel_url = reverse(
        "plugins:netbox_config_backup:backuprun_cancel",
        kwargs={"pk": run.pk},
    )
    response = client.post(cancel_url)
    assert response.status_code == 302
    assert response.headers["Location"] == run.get_absolute_url()

    run.refresh_from_db()
    target.refresh_from_db()
    assert run.status == "skipped"
    assert run.error_code == "CANCELLED"
    assert run.finished_at is not None
    assert target.status == "never"
    assert not Job.objects.filter(job_id=job.job_id).exists()
    # NetBox removes its Core Job row and cancels the corresponding RQ job.
    # Depending on the installed RQ version, the cancelled Redis record can be
    # retained briefly for observability, but it is no longer executable.
    redis_job = queue.fetch_job(str(job.job_id))
    assert redis_job is None or redis_job.get_status(refresh=True) == JobStatus.CANCELED

    detail = client.get(run.get_absolute_url())
    assert detail.status_code == 200
    assert b"Cancel queued backup" not in detail.content

    print(
        {
            "worker_guard": True,
            "queued_run_cancelled": True,
            "rq_job_removed": True,
            "target_health_unchanged": True,
        }
    )
finally:
    for cleanup_job in Job.objects.filter(
        job_id__in=BackupRun.objects.filter(target=target).values("job_id")
    ):
        cleanup_job.delete()
    BackupRun.objects.filter(target=target).delete()
    target.delete()
    device.delete()
    role.delete()
    device_type.delete()
    manufacturer.delete()
    site.delete()
    user.delete()
