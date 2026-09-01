from __future__ import annotations

import io
import unittest
from zipfile import BadZipFile

import pyzipper

from netbox_config_backup.services.download_encryption import (
    DownloadEncryptionError,
    build_password_protected_zip,
    protected_zip_filename,
)


class DownloadEncryptionTests(unittest.TestCase):
    def test_archive_uses_aes_256_and_round_trips_with_the_password(self):
        payload = b"hostname edge-router-01\ninterface Ethernet1\n"
        encrypted = build_password_protected_zip(
            content=payload,
            member_filename="edge-router-01_2026-09-01.cfg",
            password="correct horse battery staple",
        )

        self.assertTrue(encrypted.startswith(b"PK"))
        self.assertNotIn(payload, encrypted)
        with pyzipper.AESZipFile(io.BytesIO(encrypted)) as archive:
            self.assertEqual(archive.namelist(), ["edge-router-01_2026-09-01.cfg"])
            archive.setpassword(b"correct horse battery staple")
            self.assertEqual(archive.read(archive.namelist()[0]), payload)

    def test_wrong_password_cannot_read_the_archive(self):
        encrypted = build_password_protected_zip(
            content=b"secret configuration",
            member_filename="device.cfg",
            password="right-password",
        )
        with pyzipper.AESZipFile(io.BytesIO(encrypted)) as archive:
            archive.setpassword(b"wrong-password")
            with self.assertRaises((RuntimeError, BadZipFile)):
                archive.read("device.cfg")

    def test_unsafe_member_names_are_rejected(self):
        for filename in ("", ".", "..", "nested/device.cfg", "nested\\device.cfg", "a\nb"):
            with self.subTest(filename=filename), self.assertRaises(DownloadEncryptionError):
                build_password_protected_zip(
                    content=b"configuration",
                    member_filename=filename,
                    password="long-enough-password",
                )

    def test_protected_archive_name_is_clear_and_does_not_overwrite_an_inner_zip(self):
        self.assertEqual(protected_zip_filename("router.cfg"), "router.zip")
        self.assertEqual(protected_zip_filename("native.zip"), "native_protected.zip")


if __name__ == "__main__":
    unittest.main()
