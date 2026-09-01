"""Verify the native NetBox Device Backup tab, preview, and raw download."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import BackupTarget, ConfigArtifact, ConfigRevision

prefix = f"ncb-device-tab-{uuid4().hex[:8]}"
target = BackupTarget.objects.filter(device__name__startswith="ncb-smoke-").first()
assert target is not None, "The main Docker smoke test must create a backup target first."
revision = ConfigRevision.objects.filter(target=target).order_by("-created").first()
assert revision is not None
artifact = ConfigArtifact.objects.get(revision=revision, is_primary=True)
user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")

try:
    client = Client()
    client.force_login(user)
    tab_url = reverse("dcim:device_config_backup", kwargs={"pk": target.device_id})
    preview_url = reverse(
        "plugins:netbox_config_backup:configrevision_content",
        kwargs={"pk": revision.pk},
    )
    download_url = reverse(
        "plugins:netbox_config_backup:configrevision_artifact_download",
        kwargs={"pk": revision.pk, "artifact_pk": artifact.pk},
    )

    device_detail = client.get(target.device.get_absolute_url())
    assert device_detail.status_code == 200
    assert tab_url.encode() in device_detail.content

    tab = client.get(tab_url)
    assert tab.status_code == 200
    assert b"Configuration backups" in tab.content
    assert revision.get_absolute_url().encode() in tab.content
    assert preview_url.encode() in tab.content
    assert download_url.encode() in tab.content

    preview = client.get(preview_url)
    assert preview.status_code == 200
    assert b"hostname ncb-smoke-device" in preview.content

    download = client.get(download_url)
    assert download.status_code == 200
    payload = b"".join(download.streaming_content)
    assert payload.startswith(b"hostname ncb-smoke-device")
    assert "attachment" in download["Content-Disposition"]
    assert target.device.name in download["Content-Disposition"]

    print(
        {
            "device_tab": True,
            "revision_preview": True,
            "primary_backup_download": True,
            "download_bytes": len(payload),
        }
    )
finally:
    user.delete()
