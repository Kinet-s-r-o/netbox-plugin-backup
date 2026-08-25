import hashlib
import os
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from netbox_config_backup.services.destination_ftp import VerifiedFtpDownloadResult
from netbox_config_backup.services.destination_types import DestinationError
from netbox_config_backup.services.ftp_recovery import (
    build_ftp_recovery_package,
    cleanup_expired_recovery_packages,
    recovery_package_is_expired,
    validate_recovery_package,
)


def fake_replica():
    device = SimpleNamespace(name="router-01")
    target = SimpleNamespace(device=device, device_id=7)
    revision = SimpleNamespace(
        pk=12,
        revision_uuid=UUID("11111111-2222-4333-8444-555555555555"),
        target=target,
    )
    destination = SimpleNamespace(pk=18, name="Internal FTP")
    return SimpleNamespace(pk=21, revision=revision, destination=destination)


def write_fake_verified_files(_replica, archive, *, archive_prefix, max_total_bytes):
    assert max_total_bytes >= 25
    archive.writestr(f"{archive_prefix}/configuration.txt", b"hostname router-01\n")
    archive.writestr(f"{archive_prefix}/_netbox_manifest.json", b"{}")
    return VerifiedFtpDownloadResult(
        file_count=2,
        verified_bytes=21,
        remote_path="/backup/devices/router-01/revisions/revision",
    )


class FtpRecoveryPackageTests(unittest.TestCase):
    def test_builds_atomic_package_with_manual_recovery_notice(self):
        token = uuid4()
        now = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "netbox_config_backup.services.ftp_recovery.write_verified_ftp_replica_to_archive",
                side_effect=write_fake_verified_files,
            ),
        ):
            result = build_ftp_recovery_package(
                fake_replica(),
                storage_root=root,
                package_token=token,
                ttl_minutes=60,
                max_total_bytes=1024,
                now=now,
            )
            path = validate_recovery_package(
                storage_root=root,
                package_token=token,
                expected_size=result.size,
                expected_sha256=result.sha256,
            )

            self.assertEqual(path.name, f"{token}.zip")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result.sha256)
            self.assertEqual(result.file_count, 2)
            self.assertEqual(result.expires_at, (now + timedelta(hours=1)).isoformat())
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                prefix = "router-01/11111111-2222-4333-8444-555555555555"
                self.assertIn(f"{prefix}/configuration.txt", names)
                readme = archive.read(f"{prefix}/RECOVERY_README.txt").decode()
                self.assertIn("manual recovery only", readme)
                self.assertIn("does not import, restore, or apply", readme)

    def test_validation_rejects_tampered_local_package(self):
        token = uuid4()
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "netbox_config_backup.services.ftp_recovery.write_verified_ftp_replica_to_archive",
                side_effect=write_fake_verified_files,
            ),
        ):
            result = build_ftp_recovery_package(
                fake_replica(),
                storage_root=root,
                package_token=token,
                ttl_minutes=60,
                max_total_bytes=1024,
            )
            package = Path(root) / ".recovery-packages" / f"{token}.zip"
            with package.open("ab") as handle:
                handle.write(b"tampered")

            with self.assertRaises(DestinationError) as raised:
                validate_recovery_package(
                    storage_root=root,
                    package_token=token,
                    expected_size=result.size,
                    expected_sha256=result.sha256,
                )
            self.assertEqual(raised.exception.error_code, "RECOVERY_PACKAGE_INVALID")

    def test_cleanup_removes_only_expired_generated_files(self):
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as root:
            package_root = Path(root) / ".recovery-packages"
            package_root.mkdir()
            expired = package_root / f"{uuid4()}.zip"
            current = package_root / f"{uuid4()}.zip"
            unrelated = package_root / "keep-me.zip"
            for path in (expired, current, unrelated):
                path.write_bytes(b"test")
            old = (now - timedelta(hours=2)).timestamp()
            os.utime(expired, (old, old))
            os.utime(current, (now.timestamp(), now.timestamp()))
            os.utime(unrelated, (old, old))

            summary = cleanup_expired_recovery_packages(
                storage_root=root,
                ttl_minutes=60,
                now=now,
            )

            self.assertEqual(summary, {"deleted": 1, "failed": 0})
            self.assertFalse(expired.exists())
            self.assertTrue(current.exists())
            self.assertTrue(unrelated.exists())

    def test_expiry_parser_fails_closed(self):
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        self.assertTrue(recovery_package_is_expired("invalid", now=now))
        self.assertTrue(
            recovery_package_is_expired((now - timedelta(seconds=1)).isoformat(), now=now)
        )
        self.assertFalse(
            recovery_package_is_expired((now + timedelta(seconds=1)).isoformat(), now=now)
        )


if __name__ == "__main__":
    unittest.main()
