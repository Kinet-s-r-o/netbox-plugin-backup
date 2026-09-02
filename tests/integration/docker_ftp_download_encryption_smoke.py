"""Verify single-layer protected FTP ZIP downloads without real FTP/device access.

Only the FTP transfer is simulated. Package creation/hash validation, HTTP
authorization, encryption, and download auditing use production code. All database
changes roll back; generated packages live only in a temporary directory.
"""

import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

import pyzipper
from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import Client, override_settings
from django.urls import reverse

from netbox_config_backup.models import (
    BackupDestination,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    CredentialProfile,
    RevisionReplica,
)
from netbox_config_backup.services.destination_ftp import VerifiedFtpDownloadResult
from netbox_config_backup.services.download_encryption import DownloadEncryptionError
from netbox_config_backup.services.ftp_recovery import build_ftp_recovery_package

prefix = f"ncb-zip-download-{uuid4().hex[:8]}"
password = "synthetic-only ZIP password"
with transaction.atomic(), TemporaryDirectory(prefix=f"{prefix}-") as storage_root:
    site = Site.objects.create(name=prefix, slug=prefix)
    manufacturer = Manufacturer.objects.create(name=prefix, slug=prefix)
    device_type = DeviceType.objects.create(manufacturer=manufacturer, model=prefix, slug=prefix)
    role = DeviceRole.objects.create(name=prefix, slug=prefix)
    device = Device.objects.create(name=prefix, site=site, role=role, device_type=device_type)
    target = BackupTarget.objects.create(device=device, enabled=True, driver_override="fake")
    revision = ConfigRevision.objects.create(
        target=target, normalized_hash="a" * 64, normalizer_version="test", driver_id="fake"
    )
    payload = b"hostname synthetic-router\n"
    ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="running_config",
        format="text",
        storage_key="unused/synthetic.cfg",
        size=len(payload),
        raw_hash=hashlib.sha256(payload).hexdigest(),
        is_primary=True,
    )
    credentials = CredentialProfile.objects.create(
        name=prefix, provider_id="environment", secret_reference="SYNTHETIC_UNUSED"
    )
    destination = BackupDestination.objects.create(
        name=prefix, protocol="ftp", allow_insecure_ftp=True, host="invalid.example",
        port=21, credential_profile=credentials, auto_replicate=False,
    )
    replica = RevisionReplica.objects.create(
        revision=revision, destination=destination, status="success", remote_available=True
    )
    admin = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
    restricted = get_user_model().objects.create_user(username=f"{prefix}-restricted")
    client = Client(HTTP_HOST="localhost")
    client.force_login(admin)

    files = {
        "configuration.txt": payload,
        "_netbox_manifest.json": json.dumps(
            {"sha256": hashlib.sha256(payload).hexdigest()}
        ).encode(),
        "native.tgz": b"unchanged native device archive",
    }

    def transfer(_replica, archive, *, archive_prefix, max_total_bytes):
        for name, content in files.items():
            archive.writestr(f"{archive_prefix}/{name}", content)
        return VerifiedFtpDownloadResult(
            file_count=len(files), verified_bytes=sum(map(len, files.values())), remote_path="test"
        )

    plugin_config = dict(settings.PLUGINS_CONFIG)
    plugin_config["netbox_config_backup"] = {
        **plugin_config["netbox_config_backup"], "storage_root": storage_root
    }
    with override_settings(PLUGINS_CONFIG=plugin_config), patch(
        "netbox_config_backup.services.ftp_recovery.write_verified_ftp_replica_to_archive",
        side_effect=transfer,
    ) as ftp_transfer:
        result = build_ftp_recovery_package(
            replica, storage_root=storage_root, package_token=uuid4(),
            ttl_minutes=60, max_total_bytes=1024 * 1024,
        )
        package_path = Path(storage_root) / ".recovery-packages" / f"{result.token}.zip"
        original = package_path.read_bytes()
        job = Job.objects.create(
            object_type=ContentType.objects.get_for_model(BackupTarget),
            object_id=target.pk,
            name="Prepare verified FTP recovery package",
            status="completed",
            data={"ftp_recovery_package": result.as_dict()},
            job_id=uuid4(),
            queue_name="netbox_config_backup.backup",
        )
        url = reverse(
            "plugins:netbox_config_backup:configrevision_ftp_recovery_download",
            kwargs={"pk": revision.pk, "job_id": job.job_id},
        )

        with patch("netbox_config_backup.views.resolve_download_zip_password", return_value=None):
            response = client.get(url)
            assert response.status_code == 200
            assert b"".join(response.streaming_content) == original

        with patch(
            "netbox_config_backup.views.resolve_download_zip_password", return_value=password
        ):
            response = client.get(url)
            assert response.status_code == 200
            assert response["Content-Type"] == "application/zip"
            assert "_protected.zip" in response["Content-Disposition"]
            assert response["Cache-Control"] == "private, no-store"
            protected = b"".join(response.streaming_content)
            assert int(response["Content-Length"]) == len(protected)
        with ZipFile(io.BytesIO(original)) as plain, pyzipper.AESZipFile(
            io.BytesIO(protected)
        ) as encrypted:
            assert encrypted.namelist() == plain.namelist()
            assert len(encrypted.namelist()) == len(files) + 1
            assert not any(name.endswith(".zip") for name in encrypted.namelist())
            for info in encrypted.infolist():
                assert info.flag_bits & 1 and info.wz_aes_strength == 3
                assert encrypted.read(info, pwd=password.encode()) == plain.read(info.filename)
        assert package_path.read_bytes() == original
        job.refresh_from_db()
        assert job.data["ftp_recovery_package"]["download_count"] == 2

        with patch(
            "netbox_config_backup.views.resolve_download_zip_password",
            side_effect=DownloadEncryptionError("Protected downloads are temporarily unavailable."),
        ):
            assert client.get(url).status_code == 503
        limited = Client(HTTP_HOST="localhost")
        limited.force_login(restricted)
        assert limited.get(url).status_code == 403

        # Invalid stored bytes must still fail integrity verification before encryption.
        package_path.write_bytes(original + b"tampered")
        with patch("netbox_config_backup.views.encrypt_zip_package_stream") as encrypt:
            assert client.get(url).status_code == 404
            encrypt.assert_not_called()

        # A malformed archive with a matching package hash must fail closed too.
        package_path.write_bytes(b"invalid ZIP")
        job.data["ftp_recovery_package"].update(
            size=11, sha256=hashlib.sha256(b"invalid ZIP").hexdigest()
        )
        job.save(update_fields=("data",))
        with patch(
            "netbox_config_backup.views.resolve_download_zip_password", return_value=password
        ):
            assert client.get(url).status_code == 503
        job.refresh_from_db()
        assert job.data["ftp_recovery_package"]["download_count"] == 2
        ftp_transfer.assert_called_once()
    transaction.set_rollback(True)

print("FTP_DOWNLOAD_ENCRYPTION_SMOKE_OK: single ZIP, AES-256, unchanged files, RBAC, fail-closed")
