import unittest
from types import SimpleNamespace
from unittest.mock import patch

from netbox_config_backup.services.destination_types import DestinationError
from netbox_config_backup.services.target_deletion_ftp import (
    delete_target_ftp_copies,
    validate_target_external_copies,
)


def replica_fixture(
    *,
    protocol="ftp",
    status="success",
    remote_path="/backup/devices/router/backups/2026-08-26_08-11-06",
    remote_available=True,
    destination_enabled=True,
):
    return SimpleNamespace(
        destination=SimpleNamespace(protocol=protocol, enabled=destination_enabled),
        status=status,
        remote_path=remote_path,
        remote_available=remote_available,
    )


class TargetDeletionFtpSafetyTests(unittest.TestCase):
    def test_active_replica_blocks_target_deletion(self):
        with self.assertRaises(ValueError):
            validate_target_external_copies((replica_fixture(status="running"),))

    def test_non_ftp_copy_blocks_ftp_only_target_deletion(self):
        with self.assertRaises(ValueError):
            validate_target_external_copies((replica_fixture(protocol="sftp"),))

    def test_disabled_ftp_destination_is_a_delete_kill_switch(self):
        with self.assertRaises(ValueError):
            validate_target_external_copies((replica_fixture(destination_enabled=False),))

    def test_deletes_available_and_uncertain_recorded_ftp_paths(self):
        available = replica_fixture()
        uncertain = replica_fixture(status="failed", remote_available=False)
        never_uploaded = replica_fixture(
            status="failed",
            remote_available=False,
            remote_path="",
        )

        with patch(
            "netbox_config_backup.services.target_deletion_ftp.delete_revision_replica_ftp"
        ) as delete:
            delete_target_ftp_copies((available, uncertain, never_uploaded))

        self.assertEqual(delete.call_count, 2)
        delete.assert_any_call(available)
        delete.assert_any_call(uncertain)

    def test_ftp_delete_failure_is_safe_for_ui_and_aborts(self):
        replica = replica_fixture()
        with (
            patch(
                "netbox_config_backup.services.target_deletion_ftp.delete_revision_replica_ftp",
                side_effect=DestinationError("DESTINATION_DELETE_CONFLICT", "unsafe"),
            ),
            self.assertRaises(ValueError) as raised,
        ):
            delete_target_ftp_copies((replica,))

        self.assertIn("DESTINATION_DELETE_CONFLICT", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
