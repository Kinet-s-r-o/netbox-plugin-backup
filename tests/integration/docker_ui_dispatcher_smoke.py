"""Exercise the authenticated UI and RQ dispatcher in an isolated NetBox stack."""

import json
import time
from uuid import uuid4

from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import BackupRun, BackupTarget, ConfigArtifact, ConfigRevision
from netbox_config_backup.services.examples import get_example_configuration


def wait_for_run(run: BackupRun, timeout: float = 30.0) -> BackupRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run.refresh_from_db()
        if run.status not in {"queued", "running"}:
            return run
        time.sleep(0.25)
    raise AssertionError(f"Backup run {run.pk} did not finish; current status={run.status}")


def wait_for_job(job: Job, timeout: float = 30.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job.refresh_from_db()
        if job.status not in {"pending", "scheduled", "running"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"Job {job.pk} did not finish; current status={job.status}")


prefix = f"ncb-ui-{uuid4().hex[:8]}"
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

user = get_user_model().objects.create_superuser(username=f"{prefix}-admin", password=uuid4().hex)
client = Client()
client.force_login(user)

target = BackupTarget.objects.create(device=device, enabled=True, driver_override="fake")

list_urls = (
    "home",
    "examples",
    "help",
    "advanced_settings",
    "backuptarget_list",
    "backuprun_list",
    "configrevision_list",
    "backuppolicy_list",
    "retentionpolicy_list",
    "remoteretentionpolicy_list",
    "platformmapping_list",
    "connectionprofile_list",
    "credentialprofile_list",
    "ssh_host_key_list",
)
for name in list_urls:
    response = client.get(reverse(f"plugins:netbox_config_backup:{name}"))
    assert response.status_code == 200, (name, response.status_code)

add_urls = (
    "backuptarget_add",
    "backuppolicy_add",
    "retentionpolicy_add",
    "remoteretentionpolicy_add",
    "platformmapping_add",
    "connectionprofile_add",
    "credentialprofile_add",
)
for name in add_urls:
    response = client.get(reverse(f"plugins:netbox_config_backup:{name}"))
    assert response.status_code == 200, (name, response.status_code)

response = client.get(reverse("plugins-api:netbox_config_backup-api:remoteretentionpolicy-list"))
assert response.status_code == 200, response.status_code

target_api_url = reverse(
    "plugins-api:netbox_config_backup-api:backuptarget-detail",
    kwargs={"pk": target.pk},
)
response = client.delete(target_api_url)
assert response.status_code == 405, response.status_code
assert BackupTarget.objects.filter(pk=target.pk).exists()

response = client.post(reverse("plugins:netbox_config_backup:examples"))
assert response.status_code == 302
assert get_example_configuration() is not None

run_count_before_test = BackupRun.objects.filter(target=target).count()
revision_count_before_test = ConfigRevision.objects.filter(target=target).count()
test_url = reverse(
    "plugins:netbox_config_backup:backuptarget_test_connection",
    kwargs={"pk": target.pk},
)
response = client.post(test_url)
assert response.status_code == 302, response.status_code
assert "/plugins/config-backup/targets/" in response["Location"]
assert "/connection-test/" in response["Location"]
connection_job = wait_for_job(
    Job.objects.filter(
        name="Config backup connection test",
        object_id=target.pk,
    ).latest("created")
)
assert connection_job.status == "completed", (connection_job.status, connection_job.data)
assert BackupRun.objects.filter(target=target).count() == run_count_before_test
assert ConfigRevision.objects.filter(target=target).count() == revision_count_before_test
result_url = reverse(
    "plugins:netbox_config_backup:backuptarget_connection_test_result",
    kwargs={"pk": target.pk, "job_id": connection_job.job_id},
)
status_url = reverse(
    "plugins:netbox_config_backup:backuptarget_connection_test_status",
    kwargs={"pk": target.pk, "job_id": connection_job.job_id},
)
response = client.get(result_url)
assert response.status_code == 200
assert b"Connection test succeeded" in response.content
assert b"Validated collection" in response.content
response = client.get(status_url)
assert response.status_code == 200
assert response.json()["terminal"] is True
assert response.json()["status"] == "completed"
assert response.json()["driver_id"] == "fake"
response = client.get(connection_job.get_absolute_url())
assert response.status_code == 200, response.status_code

run_url = reverse("plugins:netbox_config_backup:backuptarget_run", kwargs={"pk": target.pk})
response = client.post(run_url)
assert response.status_code == 302, response.status_code
first = wait_for_run(BackupRun.objects.filter(target=target).latest("created"))
assert first.status == "success_changed", (first.status, first.error_code, first.error_message)
assert first.revision_id is not None

revision_api_url = reverse(
    "plugins-api:netbox_config_backup-api:configrevision-detail",
    kwargs={"pk": first.revision_id},
)
run_api_url = reverse(
    "plugins-api:netbox_config_backup-api:backuprun-detail",
    kwargs={"pk": first.pk},
)
assert client.delete(revision_api_url).status_code == 405
assert client.delete(run_api_url).status_code == 405

response = client.get(first.get_absolute_url())
assert response.status_code == 200
response = client.get(first.revision.get_absolute_url())
assert response.status_code == 200

response = client.post(run_url)
assert response.status_code == 302, response.status_code
second = wait_for_run(BackupRun.objects.filter(target=target).latest("created"))
assert second.pk != first.pk
assert second.status == "success_unchanged", (
    second.status,
    second.error_code,
    second.error_message,
)
assert ConfigRevision.objects.filter(target=target).count() == 1
assert ConfigArtifact.objects.filter(revision__target=target).count() == 1

print(
    json.dumps(
        {
            "artifact_count": ConfigArtifact.objects.filter(revision__target=target).count(),
            "connection_test_job": str(connection_job.job_id),
            "connection_test_status": connection_job.status,
            "first_run": first.status,
            "form_pages": len(add_urls),
            "menu_pages": len(list_urls),
            "queue_job_ids": [str(first.job_id), str(second.job_id)],
            "revision_count": ConfigRevision.objects.filter(target=target).count(),
            "second_run": second.status,
            "target": str(target),
        },
        sort_keys=True,
    )
)
