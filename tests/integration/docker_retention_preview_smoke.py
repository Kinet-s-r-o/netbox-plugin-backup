"""Verify the retention dry-run UI without changing backup history."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import BackupRun, BackupTarget, ConfigRevision

target = (
    BackupTarget.objects.select_related(
        "retention_override",
        "policy_override__retention_policy",
    )
    .order_by("pk")
    .first()
)
assert target is not None, "Create at least one backup device before running this smoke test."
assert target.retention_override_id or target.policy_override_id, (
    "The selected backup device needs an effective Local retention profile."
)

prefix = f"ncb-retention-{uuid4().hex[:8]}"
user_model = get_user_model()
admin = user_model.objects.create_superuser(username=f"{prefix}-admin")
limited = user_model.objects.create_user(username=f"{prefix}-limited")
url = reverse(
    "plugins:netbox_config_backup:backuptarget_retention_preview",
    kwargs={"pk": target.pk},
)
before_counts = (
    BackupRun.objects.filter(target=target).count(),
    ConfigRevision.objects.filter(target=target).count(),
)

try:
    limited_client = Client()
    limited_client.force_login(limited)
    assert limited_client.get(url).status_code == 403

    admin_client = Client()
    admin_client.force_login(admin)
    response = admin_client.get(url)
    assert response.status_code == 200
    assert b"Retention preview" in response.content
    assert b"Dry-run only" in response.content
    assert b"Keep" in response.content or b"No revisions yet" in response.content
    target_response = admin_client.get(target.get_absolute_url())
    assert target_response.status_code == 200
    assert b"Retention preview" in target_response.content
    assert before_counts == (
        BackupRun.objects.filter(target=target).count(),
        ConfigRevision.objects.filter(target=target).count(),
    )
finally:
    admin.delete()
    limited.delete()

print(
    {
        "target_id": target.pk,
        "status": response.status_code,
        "permission_denied_without_access": True,
        "history_unchanged": True,
        "run_count": before_counts[0],
        "revision_count": before_counts[1],
    }
)
