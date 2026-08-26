import ftplib
import hashlib
import io
import unittest
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from netbox_config_backup.services.destination_ftp import (
    VerifiedFtpDownloadResult,
    _build_manifest,
    _expected_revision_files,
    reconcile_ftp_destination,
    write_verified_ftp_replica_to_archive,
)
from netbox_config_backup.services.destination_types import DestinationError


class ArtifactManager:
    def __init__(self, artifacts):
        self.artifacts = tuple(artifacts)

    def all(self):
        return self.artifacts


class ReadOnlyFakeFTP:
    def __init__(self, files):
        self.files = dict(files)
        self.sock = object()
        self.closed = False

    def voidcmd(self, _command):
        return "200 OK"

    def size(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        return len(self.files[path])

    def retrbinary(self, command, callback):
        path = command.removeprefix("RETR ")
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        callback(self.files[path])

    def storbinary(self, *_args):
        raise AssertionError("Reconciliation must not upload files")

    def mkd(self, *_args):
        raise AssertionError("Reconciliation must not create directories")

    def delete(self, *_args):
        raise AssertionError("Reconciliation must not delete files")

    def rename(self, *_args):
        raise AssertionError("Reconciliation must not rename files")

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def audit_fixture():
    content = b"hostname router-01\n"
    artifact = SimpleNamespace(
        artifact_type="configuration_dump",
        format="text",
        storage_key="devices/1/configuration.txt",
        size=len(content),
        raw_hash=hashlib.sha256(content).hexdigest(),
        is_primary=True,
    )
    device = SimpleNamespace(name="router-01")
    target = SimpleNamespace(device=device, device_id=1)
    revision = SimpleNamespace(
        pk=22,
        revision_uuid=UUID("11111111-2222-3333-4444-555555555555"),
        target=target,
        driver_id="fake",
        created=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        artifacts=ArtifactManager((artifact,)),
    )
    path = "/backup/devices/router-01/revisions/11111111-2222-3333-4444-555555555555"
    destination = SimpleNamespace(pk=44, name="FTP test", base_path="backup", protocol="ftp")
    replica = SimpleNamespace(
        pk=33,
        revision=revision,
        remote_path=path,
        destination=destination,
        status="success",
    )
    manifest = _build_manifest(revision, (artifact,))
    files = {
        f"{path}/configuration.txt": content,
        f"{path}/_netbox_manifest.json": manifest,
    }
    return destination, replica, files


class FtpReconciliationTests(unittest.TestCase):
    def test_new_ftp_layout_uses_device_and_revision_time_in_artifact_name(self):
        _destination, replica, _files = audit_fixture()

        expected = _expected_revision_files(replica.revision, readable_names=True)

        self.assertEqual(expected[0].filename, "router-01_2026-08-24_10-00-00-000000Z.txt")
        self.assertEqual(expected[-1].filename, "_netbox_manifest.json")
        manifest = _build_manifest(
            replica.revision,
            replica.revision.artifacts.all(),
            readable_names=True,
        )
        self.assertIn(b'"schema":2', manifest)
        self.assertIn(expected[0].filename.encode(), manifest)

    def test_verifies_successful_replica_without_remote_writes(self):
        destination, replica, files = audit_fixture()
        ftp = ReadOnlyFakeFTP(files)

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = reconcile_ftp_destination(destination, replicas=(replica,))

        self.assertTrue(result["success"])
        self.assertEqual(result["checked_replicas"], 1)
        self.assertEqual(result["healthy_replicas"], 1)
        self.assertEqual(result["checked_files"], 2)
        self.assertEqual(result["verified_bytes"], sum(map(len, files.values())))
        self.assertEqual(result["issues"], [])
        self.assertTrue(ftp.closed)

    def test_reports_missing_file_but_completes_audit(self):
        destination, replica, files = audit_fixture()
        del files[next(path for path in files if path.endswith("configuration.txt"))]

        with patch(
            "netbox_config_backup.services.destination_ftp._connect",
            return_value=ReadOnlyFakeFTP(files),
        ):
            result = reconcile_ftp_destination(destination, replicas=(replica,))

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_replicas"], 1)
        self.assertEqual(result["missing_files"], 1)
        self.assertEqual(result["issues"][0]["problem"], "missing")

    def test_reports_hash_mismatch_separately_from_size(self):
        destination, replica, files = audit_fixture()
        artifact_path = next(path for path in files if path.endswith("configuration.txt"))
        files[artifact_path] = b"X" * len(files[artifact_path])

        with patch(
            "netbox_config_backup.services.destination_ftp._connect",
            return_value=ReadOnlyFakeFTP(files),
        ):
            result = reconcile_ftp_destination(destination, replicas=(replica,))

        self.assertEqual(result["hash_mismatches"], 1)
        self.assertEqual(result["size_mismatches"], 0)
        self.assertEqual(result["issues"][0]["problem"], "hash_mismatch")


class FtpVerifiedRecoveryDownloadTests(unittest.TestCase):
    def test_streams_exact_verified_files_into_archive_without_ftp_writes(self):
        _destination, replica, files = audit_fixture()
        ftp = ReadOnlyFakeFTP(files)
        buffer = io.BytesIO()

        with (
            patch(
                "netbox_config_backup.services.destination_ftp._connect",
                return_value=ftp,
            ),
            zipfile.ZipFile(buffer, "w") as archive,
        ):
            result = write_verified_ftp_replica_to_archive(
                replica,
                archive,
                archive_prefix="router-01/revision",
                max_total_bytes=1024 * 1024,
            )

        self.assertIsInstance(result, VerifiedFtpDownloadResult)
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.verified_bytes, sum(map(len, files.values())))
        self.assertTrue(ftp.closed)
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "router-01/revision/configuration.txt",
                    "router-01/revision/_netbox_manifest.json",
                },
            )
            self.assertEqual(
                archive.read("router-01/revision/configuration.txt"),
                b"hostname router-01\n",
            )

    def test_rejects_hash_mismatch_and_never_uses_ftp_write_commands(self):
        _destination, replica, files = audit_fixture()
        artifact_path = next(path for path in files if path.endswith("configuration.txt"))
        files[artifact_path] = b"X" * len(files[artifact_path])
        ftp = ReadOnlyFakeFTP(files)

        with (
            patch(
                "netbox_config_backup.services.destination_ftp._connect",
                return_value=ftp,
            ),
            zipfile.ZipFile(io.BytesIO(), "w") as archive,
            self.assertRaises(DestinationError) as raised,
        ):
            write_verified_ftp_replica_to_archive(
                replica,
                archive,
                archive_prefix="router-01/revision",
                max_total_bytes=1024 * 1024,
            )

        self.assertEqual(raised.exception.error_code, "RECOVERY_HASH_MISMATCH")
        self.assertTrue(ftp.closed)


if __name__ == "__main__":
    unittest.main()
