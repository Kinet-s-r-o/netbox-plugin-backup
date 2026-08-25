"""Exercise database-backed SSH trust and the background discovery job."""

import json
import time

from netbox_config_backup.jobs import BACKUP_QUEUE, SSHHostKeyScanJob
from netbox_config_backup.models import SSHHostKey
from netbox_config_backup.services.django_repository import DjangoBackupRepository

trusted = SSHHostKey.objects.filter(status="trusted").select_related("target").first()
assert trusted is not None, "Approve at least one SSH host key before running this smoke test."

context = DjangoBackupRepository().get_target_execution_context(trusted.target_id)
assert trusted.known_hosts_line in context.connection.trusted_host_keys

job = SSHHostKeyScanJob.enqueue(
    target_ids=[trusted.target_id],
    queue_name=BACKUP_QUEUE,
)
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    job.refresh_from_db()
    if job.status not in {"pending", "scheduled", "running"}:
        break
    time.sleep(0.25)

assert job.status == "completed", (job.status, job.error)
trusted.refresh_from_db()
assert trusted.status == "trusted"
assert trusted.last_seen_at >= trusted.first_seen_at

print(
    json.dumps(
        {
            "job": str(job.job_id),
            "status": job.status,
            "target": trusted.target_id,
            "trusted_lines": len(context.connection.trusted_host_keys),
        },
        sort_keys=True,
    )
)
