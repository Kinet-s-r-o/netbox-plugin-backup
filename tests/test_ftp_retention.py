import ftplib
import hashlib
import posixpath
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from netbox_config_backup.services.destination_ftp import (
    _expected_revision_files,
    _mkdirs,
    _store,
    delete_revision_replica_ftp,
    replicate_revision_ftp,
)
from netbox_config_backup.services.destination_paths import (
    ftp_revision_destination_path,
    revision_destination_path,
)
from netbox_config_backup.services.destination_types import DestinationError


class ArtifactManager:
    def __init__(self, artifacts):
        self.artifacts = tuple(artifacts)

    def all(self):
        return self.artifacts


class MutableFakeFTP:
    def __init__(
        self,
        files,
        directories,
        *,
        full_paths=False,
        missing_directory_error="550 directory not found",
    ):
        self.files = dict(files)
        self.directories = set(directories)
        self.full_paths = full_paths
        self.missing_directory_error = missing_directory_error
        self.current_directory = "/"
        self.delete_calls = []
        self.rmd_calls = []
        self.sock = object()
        self.closed = False

    def cwd(self, path):
        if path == "/":
            self.current_directory = path
            return "250 OK"
        if path not in self.directories:
            if isinstance(self.missing_directory_error, BaseException):
                raise self.missing_directory_error
            raise ftplib.error_perm(self.missing_directory_error)
        self.current_directory = path
        return "250 OK"

    def mkd(self, path):
        self.directories.add(path)
        return path

    def nlst(self):
        prefix = self.current_directory.rstrip("/") + "/"
        names = {
            path.removeprefix(prefix)
            for path in self.files
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        }
        names.update(
            path.removeprefix(prefix)
            for path in self.directories
            if path.startswith(prefix)
            and path != self.current_directory
            and "/" not in path.removeprefix(prefix)
        )
        names = sorted(names)
        if self.full_paths:
            return [prefix + name for name in names]
        return names

    def delete(self, path):
        self.delete_calls.append(path)
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        del self.files[path]
        return "250 deleted"

    def rmd(self, path):
        self.rmd_calls.append(path)
        prefix = path.rstrip("/") + "/"
        if any(filename.startswith(prefix) for filename in self.files):
            raise ftplib.error_perm("550 directory not empty")
        if path not in self.directories:
            raise ftplib.error_perm("550 directory not found")
        self.directories.remove(path)
        return "250 removed"

    def quit(self):
        self.closed = True
        self.sock = None

    def close(self):
        self.closed = True
        self.sock = None


class DeniedDeleteFakeFTP(MutableFakeFTP):
    def __init__(self, *args, denied_filename, **kwargs):
        super().__init__(*args, **kwargs)
        self.denied_filename = denied_filename

    def delete(self, path):
        self.delete_calls.append(path)
        if path.endswith("/" + self.denied_filename):
            raise ftplib.error_perm("550 permission denied")
        return super().delete(path)


class DelayedCwdAfterMkdirFakeFTP(MutableFakeFTP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_cwd_failures = {}

    def mkd(self, path):
        result = super().mkd(path)
        self.pending_cwd_failures[path] = 2
        return result

    def cwd(self, path):
        if self.pending_cwd_failures.get(path, 0) > 0:
            self.pending_cwd_failures[path] -= 1
            raise ftplib.error_temp("450 Requested file action not taken")
        return super().cwd(path)


def replica_fixture(
    *,
    legacy=False,
    historical_readable=False,
    historical_nested=False,
    revision_uuid=None,
    revision_pk=22,
):
    primary_content = b"hostname router-01\n"
    secondary_content = b"native backup"
    artifacts = (
        SimpleNamespace(
            artifact_type="configuration_dump",
            format="text",
            storage_key="devices/1/configuration.txt",
            size=len(primary_content),
            raw_hash=hashlib.sha256(primary_content).hexdigest(),
            is_primary=True,
        ),
        SimpleNamespace(
            artifact_type="native_backup",
            format="archive",
            storage_key="devices/1/native-backup.tgz",
            size=len(secondary_content),
            raw_hash=hashlib.sha256(secondary_content).hexdigest(),
            is_primary=False,
        ),
    )
    device = SimpleNamespace(name="router-01")
    target = SimpleNamespace(device=device, device_id=187)
    revision = SimpleNamespace(
        pk=revision_pk,
        revision_uuid=revision_uuid or UUID("11111111-2222-3333-4444-555555555555"),
        target=target,
        driver_id="fake",
        created=datetime(2026, 8, 26, 8, 11, 6, tzinfo=UTC),
        artifacts=ArtifactManager(artifacts),
    )
    destination = SimpleNamespace(
        pk=44,
        name="FTP test",
        base_path="netbox-config-backup",
        protocol="ftp",
        enabled=True,
        max_artifact_size=1024 * 1024,
    )
    if legacy:
        remote_path = revision_destination_path(
            destination.base_path,
            device_name=device.name,
            device_id=target.device_id,
            revision_uuid=revision.revision_uuid,
        )
    else:
        remote_path = ftp_revision_destination_path(
            destination.base_path,
            device_name=device.name,
            device_id=target.device_id,
            created_at=revision.created,
            revision_id=(None if historical_readable or historical_nested else revision.pk),
            revision_uuid=revision.revision_uuid if historical_nested else None,
        )
    replica = SimpleNamespace(
        pk=33,
        destination=destination,
        revision=revision,
        remote_path=remote_path,
        status="success",
        remote_available=True,
        remote_deleted_at=None,
    )
    expected = _expected_revision_files(revision, readable_names=not legacy)
    files = {f"{remote_path}/{item.filename}": b"x" * item.size for item in expected}
    return replica, expected, files


class FtpRetentionDeleteTests(unittest.TestCase):
    def test_temporary_upload_rejection_has_a_precise_safe_error(self):
        ftp = SimpleNamespace(
            storbinary=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ftplib.error_temp("450 Requested file action not taken")
            )
        )

        with self.assertRaises(DestinationError) as raised:
            _store(ftp, "/backup/config.txt", b"configuration")

        self.assertEqual(raised.exception.error_code, "DESTINATION_UPLOAD_TEMPORARY")

    def test_mkdirs_accepts_ambiguous_450_only_after_parent_proves_absence(self):
        ftp = MutableFakeFTP(
            {},
            {"/netbox-config-backup"},
            missing_directory_error=ftplib.error_temp("450 Requested file action not taken"),
        )

        _mkdirs(ftp, "/netbox-config-backup/devices/router-01")

        self.assertIn("/netbox-config-backup/devices", ftp.directories)
        self.assertIn("/netbox-config-backup/devices/router-01", ftp.directories)

    def test_mkdirs_retries_transient_cwd_after_server_creates_directory(self):
        ftp = DelayedCwdAfterMkdirFakeFTP(
            {},
            {"/netbox-config-backup"},
            missing_directory_error=ftplib.error_temp("450 Requested file action not taken"),
        )

        with patch("netbox_config_backup.services.destination_ftp.time.sleep") as sleep:
            _mkdirs(ftp, "/netbox-config-backup/devices/router-01")

        self.assertIn("/netbox-config-backup/devices/router-01", ftp.directories)
        self.assertGreaterEqual(sleep.call_count, 2)

    def test_deletes_only_expected_files_manifest_last_and_exact_directory(self):
        replica, expected, files = replica_fixture()
        ftp = MutableFakeFTP(files, {replica.remote_path}, full_paths=True)

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.expected_file_count, len(expected))
        self.assertEqual(result.deleted_file_count, len(expected))
        self.assertEqual(result.missing_file_count, 0)
        self.assertEqual(result.deleted_bytes, sum(item.size for item in expected))
        self.assertTrue(result.directory_removed)
        self.assertFalse(result.already_absent)
        self.assertEqual(ftp.files, {})
        self.assertEqual(ftp.rmd_calls, [replica.remote_path])
        self.assertTrue(ftp.delete_calls[-1].endswith("/_netbox_manifest.json"))
        self.assertTrue(ftp.closed)

    def test_missing_expected_file_is_idempotent(self):
        replica, expected, files = replica_fixture()
        missing = expected[0]
        del files[f"{replica.remote_path}/{missing.filename}"]
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.deleted_file_count, len(expected) - 1)
        self.assertEqual(result.missing_file_count, 1)
        self.assertTrue(result.directory_removed)

    def test_interrupted_deterministic_part_file_is_removed_safely(self):
        replica, expected, files = replica_fixture()
        interrupted = f"{replica.remote_path}/{expected[0].filename}.part"
        files[interrupted] = b"partial"
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertTrue(result.directory_removed)
        self.assertNotIn(interrupted, ftp.files)

    def test_already_missing_revision_directory_is_success(self):
        replica, expected, _files = replica_fixture()
        ftp = MutableFakeFTP({}, set())

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertTrue(result.already_absent)
        self.assertFalse(result.directory_removed)
        self.assertEqual(result.missing_file_count, len(expected))
        self.assertEqual(ftp.delete_calls, [])
        self.assertEqual(ftp.rmd_calls, [])

    def test_generic_550_is_missing_only_when_parent_listing_proves_absence(self):
        replica, expected, _files = replica_fixture()
        parent = posixpath.dirname(replica.remote_path)
        ftp = MutableFakeFTP(
            {},
            {parent},
            missing_directory_error="550 Requested action not taken",
        )

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertTrue(result.already_absent)
        self.assertEqual(result.missing_file_count, len(expected))

    def test_generic_550_is_missing_when_nearest_existing_ancestor_proves_absence(self):
        replica, expected, _files = replica_fixture()
        timestamp_parent = posixpath.dirname(replica.remote_path)
        backups_parent = posixpath.dirname(timestamp_parent)
        ftp = MutableFakeFTP(
            {},
            {backups_parent},
            missing_directory_error="550 Requested action not taken",
        )

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertTrue(result.already_absent)
        self.assertEqual(result.missing_file_count, len(expected))

    def test_unknown_file_aborts_before_any_delete(self):
        replica, _expected, files = replica_fixture()
        unknown_path = f"{replica.remote_path}/operator-note.txt"
        files[unknown_path] = b"do not delete"
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with (
            patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp),
            self.assertRaises(DestinationError) as raised,
        ):
            delete_revision_replica_ftp(replica)

        self.assertEqual(raised.exception.error_code, "DESTINATION_DELETE_CONFLICT")
        self.assertEqual(ftp.delete_calls, [])
        self.assertEqual(ftp.rmd_calls, [])
        self.assertIn(unknown_path, ftp.files)

    def test_rejects_path_outside_exact_revision_location_before_connecting(self):
        replica, _expected, _files = replica_fixture()
        replica.remote_path = "/netbox-config-backup/devices/router-01/backups/2026-08-27_08-11-06"

        with (
            patch("netbox_config_backup.services.destination_ftp._connect") as connect,
            self.assertRaises(DestinationError) as raised,
        ):
            delete_revision_replica_ftp(replica)

        self.assertEqual(raised.exception.error_code, "DELETE_PATH_INVALID")
        connect.assert_not_called()

    def test_device_rename_does_not_orphan_historical_copy(self):
        replica, expected, files = replica_fixture()
        replica.revision.target.device.name = "router-renamed"
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.deleted_file_count, len(expected))
        self.assertTrue(result.directory_removed)

    def test_accepts_existing_legacy_revision_path(self):
        replica, expected, files = replica_fixture(legacy=True)
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.deleted_file_count, len(expected))
        self.assertTrue(result.directory_removed)

    def test_accepts_historical_readable_path_without_revision_uuid(self):
        replica, expected, files = replica_fixture(historical_readable=True)
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.deleted_file_count, len(expected))
        self.assertTrue(result.directory_removed)

    def test_accepts_historical_nested_uuid_path(self):
        replica, expected, files = replica_fixture(historical_nested=True)
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)

        self.assertEqual(result.deleted_file_count, len(expected))
        self.assertTrue(result.directory_removed)

    def test_same_device_and_second_use_different_revision_directories(self):
        first, _expected, _files = replica_fixture(
            revision_uuid=UUID("11111111-2222-3333-4444-555555555555"),
            revision_pk=22,
        )
        second, _expected, _files = replica_fixture(
            revision_uuid=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            revision_pk=23,
        )

        self.assertNotEqual(first.remote_path, second.remote_path)
        self.assertEqual(
            first.remote_path.rsplit("-r", 1)[0],
            second.remote_path.rsplit("-r", 1)[0],
        )

    def test_permission_denied_does_not_treat_existing_file_as_missing(self):
        replica, expected, files = replica_fixture()
        denied = expected[0].filename
        ftp = DeniedDeleteFakeFTP(
            files,
            {replica.remote_path},
            denied_filename=denied,
        )

        with (
            patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp),
            self.assertRaises(DestinationError) as raised,
        ):
            delete_revision_replica_ftp(replica)

        self.assertEqual(raised.exception.error_code, "DESTINATION_DELETE_DENIED")
        self.assertIn(f"{replica.remote_path}/{denied}", ftp.files)
        self.assertEqual(ftp.rmd_calls, [])

    def test_accepts_failed_replica_with_a_recorded_path_for_orphan_cleanup(self):
        replica, _expected, _files = replica_fixture()
        replica.status = "failed"
        replica.remote_available = False
        ftp = MutableFakeFTP({}, set())
        with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
            result = delete_revision_replica_ftp(replica)
        self.assertTrue(result.already_absent)

    def test_refuses_active_or_non_ftp_replica(self):
        replica, _expected, _files = replica_fixture()
        replica.status = "running"
        with self.assertRaises(DestinationError) as raised:
            delete_revision_replica_ftp(replica)
        self.assertEqual(raised.exception.error_code, "DELETE_REPLICA_BUSY")

        replica.status = "success"
        replica.destination.protocol = "sftp"
        with self.assertRaises(DestinationError) as raised:
            delete_revision_replica_ftp(replica)
        self.assertEqual(raised.exception.error_code, "DELETE_PROTOCOL_UNSUPPORTED")

    def test_disabled_destination_is_a_primitive_delete_kill_switch(self):
        replica, _expected, files = replica_fixture()
        replica.destination.enabled = False
        ftp = MutableFakeFTP(files, {replica.remote_path})

        with (
            patch("netbox_config_backup.services.destination_ftp._connect") as connect,
            self.assertRaises(DestinationError) as raised,
        ):
            delete_revision_replica_ftp(replica)

        self.assertEqual(raised.exception.error_code, "DESTINATION_DISABLED")
        connect.assert_not_called()
        self.assertEqual(ftp.files, files)


class FtpImmutableRepairTests(unittest.TestCase):
    def test_device_rename_repairs_the_recorded_historical_path(self):
        replica, _expected, _files = replica_fixture()
        original_path = replica.remote_path
        replica.revision.target.device.name = "router-renamed"
        contents = {
            "devices/1/configuration.txt": b"hostname router-01\n",
            "devices/1/native-backup.tgz": b"native backup",
        }
        storage = SimpleNamespace(get=contents.__getitem__)
        ftp = SimpleNamespace(sock=None, close=lambda: None)

        with (
            patch(
                "netbox_config_backup.services.destination_ftp.build_config_storage",
                return_value=storage,
            ),
            patch(
                "netbox_config_backup.services.destination_ftp._connect",
                return_value=ftp,
            ),
            patch("netbox_config_backup.services.destination_ftp._mkdirs"),
            patch(
                "netbox_config_backup.services.destination_ftp._remote_exists",
                return_value=False,
            ),
            patch(
                "netbox_config_backup.services.destination_ftp._put_immutable",
                return_value=True,
            ) as put,
        ):
            result = replicate_revision_ftp(
                replica.destination,
                replica.revision,
                recorded_remote_path=original_path,
            )

        self.assertEqual(result.remote_path, original_path)
        self.assertEqual(put.call_count, 3)
        self.assertTrue(
            all(call.args[1].startswith(original_path + "/") for call in put.call_args_list)
        )

    def test_repair_rejects_an_unrelated_recorded_path_before_connecting(self):
        replica, _expected, _files = replica_fixture()
        unrelated = "/netbox-config-backup/devices/router-01/backups/2026-08-27_08-11-06"

        with (
            patch("netbox_config_backup.services.destination_ftp._connect") as connect,
            self.assertRaises(DestinationError) as raised,
        ):
            replicate_revision_ftp(
                replica.destination,
                replica.revision,
                recorded_remote_path=unrelated,
            )

        self.assertEqual(raised.exception.error_code, "REPLICATION_PATH_INVALID")
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
