"""Verify the Device Backup tab plus raw and password-protected downloads."""

import base64
import io
import os
from uuid import uuid4

import pyzipper
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import (
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    DownloadEncryptionSecret,
    OperationalSettings,
)

prefix = f"ncb-device-tab-{uuid4().hex[:8]}"
master_key_environment = {
    "NETBOX_CONFIG_BACKUP_MASTER_KEY": base64.urlsafe_b64encode(bytes(range(32)))
    .decode()
    .rstrip("="),
    "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "download-smoke",
    "NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS": "{}",
}
original_master_key_environment = {
    name: os.environ.get(name) for name in master_key_environment
}
os.environ.update(master_key_environment)
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

    settings_url = reverse("plugins:netbox_config_backup:advanced_settings")
    protected_password = "smoke-test protected ZIP password"
    missing_password = client.post(
        settings_url,
        {
            "settings_action": "download_encryption",
            "download_zip_encryption_enabled": "on",
        },
    )
    assert missing_password.status_code == 400
    assert b"Set a ZIP password before enabling protected downloads" in missing_password.content

    enable = client.post(
        settings_url,
        {
            "settings_action": "download_encryption",
            "download_zip_encryption_enabled": "on",
            "download_zip_password": protected_password,
            "download_zip_password_confirm": protected_password,
        },
    )
    assert enable.status_code == 302
    operational_settings = OperationalSettings.objects.get(singleton=True)
    assert operational_settings.download_zip_encryption_enabled is True
    stored_secret = DownloadEncryptionSecret.objects.get(singleton=True)
    assert protected_password.encode() not in bytes(stored_secret.ciphertext)

    settings_page = client.get(settings_url)
    assert settings_page.status_code == 200
    assert protected_password.encode() not in settings_page.content
    assert b"A password is configured" in settings_page.content

    protected_download = client.get(download_url)
    assert protected_download.status_code == 200
    protected_payload = b"".join(protected_download.streaming_content)
    assert protected_download["Content-Type"] == "application/zip"
    assert protected_download["Content-Disposition"].endswith('.zip"')
    with pyzipper.AESZipFile(io.BytesIO(protected_payload)) as archive:
        assert len(archive.namelist()) == 1
        archive.setpassword(protected_password.encode())
        assert archive.read(archive.namelist()[0]) == payload

    retain = client.post(
        settings_url,
        {
            "settings_action": "download_encryption",
            "download_zip_encryption_enabled": "on",
        },
    )
    assert retain.status_code == 302
    assert DownloadEncryptionSecret.objects.get(singleton=True).pk == stored_secret.pk

    rotation_check = io.StringIO()
    call_command("config_backup_rotate_master_key", stdout=rotation_check)
    assert "pending_rotation=0" in rotation_check.getvalue()
    assert protected_password not in rotation_check.getvalue()

    DownloadEncryptionSecret.objects.filter(singleton=True).delete()
    unavailable = client.get(download_url)
    assert unavailable.status_code == 503
    assert payload not in unavailable.content

    print(
        {
            "device_tab": True,
            "revision_preview": True,
            "primary_backup_download": True,
            "aes_256_zip_download": True,
            "missing_secret_fails_closed": True,
            "master_key_rotation_verifies_download_secret": True,
            "download_bytes": len(payload),
        }
    )
finally:
    OperationalSettings.objects.filter(singleton=True).update(
        download_zip_encryption_enabled=False
    )
    DownloadEncryptionSecret.objects.filter(singleton=True).delete()
    for name, value in original_master_key_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    user.delete()
