import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from netbox_config_backup.services.destination_paths import (
    backup_filename_stem,
    device_directory_name,
    ftp_revision_destination_path,
    revision_destination_path,
)


class DestinationPathTests(unittest.TestCase):
    def test_device_directory_uses_readable_hostname(self):
        self.assertEqual(
            device_directory_name("router-01.example.sk", 187),
            "router-01.example.sk",
        )

    def test_device_directory_sanitizes_display_names_and_traversal(self):
        self.assertEqual(
            device_directory_name("SIAE – Žilina / ../../ALFO+", 42),
            "SIAE-Zilina-ALFO",
        )

    def test_device_directory_falls_back_to_stable_device_id(self):
        self.assertEqual(device_directory_name("../..", 42), "device-42")

    def test_revision_destination_path_is_absolute_and_hostname_based(self):
        revision_uuid = UUID("11111111-2222-3333-4444-555555555555")
        self.assertEqual(
            revision_destination_path(
                "/netbox-config-backup/",
                device_name="core-router-01",
                device_id=187,
                revision_uuid=revision_uuid,
            ),
            (
                "/netbox-config-backup/devices/core-router-01/revisions/"
                "11111111-2222-3333-4444-555555555555"
            ),
        )

    def test_ftp_path_uses_device_name_and_stable_utc_creation_time(self):
        created = datetime(
            2026,
            8,
            26,
            14,
            35,
            8,
            123456,
            tzinfo=timezone(timedelta(hours=2)),
        )
        stem = "core-router-01_2026-08-26_12-35-08"

        self.assertEqual(backup_filename_stem("core-router-01", 187, created), stem)
        self.assertEqual(
            ftp_revision_destination_path(
                "/netbox-config-backup/",
                device_name="core-router-01",
                device_id=187,
                created_at=created,
            ),
            "/netbox-config-backup/devices/core-router-01/backups/"
            "2026-08-26_12-35-08",
        )


if __name__ == "__main__":
    unittest.main()
