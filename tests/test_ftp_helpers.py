from __future__ import annotations

import ftplib
import unittest

from netbox_config_backup.services.destination_types import DestinationError
from netbox_config_backup.services.ftp_helpers import (
    absolute_ftp_path,
    artifact_filename,
    is_denied_ftp_error,
    is_missing_ftp_error,
    join_ftp_path,
    unique_json_object,
    uses_readable_ftp_layout,
    validate_direct_filename,
)


class FtpHelperTests(unittest.TestCase):
    def test_ftp_path_helpers_normalize_expected_layouts(self):
        self.assertEqual(
            join_ftp_path("root/", "devices", "router"),
            "root/devices/router",
        )
        self.assertEqual(
            absolute_ftp_path("root/devices/router"),
            "/root/devices/router",
        )
        self.assertEqual(
            absolute_ftp_path("/root/devices/router"),
            "/root/devices/router",
        )
        self.assertTrue(
            uses_readable_ftp_layout("/root/devices/router/backups/2026-08-27-r1")
        )
        self.assertTrue(
            uses_readable_ftp_layout("/root/devices/router/backups/2026-08-27/revision")
        )
        self.assertFalse(
            uses_readable_ftp_layout("/root/devices/router/revisions/revision")
        )

    def test_artifact_filename_uses_only_the_storage_key_basename(self):
        self.assertEqual(
            artifact_filename("devices/1/configuration.cfg", "configuration"),
            "configuration.cfg",
        )
        self.assertEqual(
            artifact_filename("", "configuration"),
            "configuration.bin",
        )

    def test_direct_filename_rejects_unsafe_values(self):
        unsafe = (
            "",
            ".",
            "..",
            "nested/file.cfg",
            r"nested\file.cfg",
            "line\nbreak.cfg",
            "nul\x00.cfg",
        )
        for filename in unsafe:
            with self.subTest(filename=filename), self.assertRaises(DestinationError) as raised:
                validate_direct_filename(filename)
            self.assertEqual(raised.exception.error_code, "DELETE_FILESET_INVALID")

    def test_direct_filename_accepts_one_safe_child(self):
        validate_direct_filename("router_2026-08-27_10-30-00.cfg")

    def test_ftp_error_classification_is_conservative(self):
        self.assertTrue(is_missing_ftp_error(ftplib.error_perm("550 File not found")))
        self.assertFalse(is_missing_ftp_error(ftplib.error_perm("550 Permission denied")))
        self.assertTrue(is_denied_ftp_error(ftplib.error_perm("550 Permission denied")))
        self.assertFalse(is_denied_ftp_error(ftplib.error_perm("550 File not found")))

    def test_duplicate_json_keys_are_rejected(self):
        self.assertEqual(
            unique_json_object([("one", 1), ("two", 2)]),
            {"one": 1, "two": 2},
        )
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            unique_json_object([("one", 1), ("one", 2)])


if __name__ == "__main__":
    unittest.main()
