from __future__ import annotations

import io
import stat
import unittest
from tempfile import SpooledTemporaryFile
from unittest.mock import patch
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

import pyzipper

from netbox_config_backup.services.download_encryption import (
    DownloadEncryptionError,
    build_password_protected_zip,
    encrypt_zip_package_stream,
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


class RecoveryZipEncryptionTests(unittest.TestCase):
    password = "test-only recovery ZIP password"

    def package(self, files):
        source = io.BytesIO()
        with ZipFile(source, "w", compression=ZIP_STORED) as archive:
            for name, payload in files:
                archive.writestr(name, payload)
        source.seek(0)
        return source

    def encrypt(self, source, **kwargs):
        return encrypt_zip_package_stream(
            source=source,
            password=kwargs.get("password", self.password),
            max_total_bytes=kwargs.get("max_total_bytes", 1024 * 1024),
        )

    def test_single_zip_preserves_every_file_including_native_archives_and_metadata(self):
        native_zip = self.package([("vendor.cfg", b"native backup")]).getvalue()
        metadata = ZipInfo("router/revision/configuration.txt", (2026, 9, 2, 10, 11, 12))
        metadata.create_system = 3
        metadata.external_attr = (stat.S_IFREG | 0o600) << 16
        files = [
            (metadata, b"hostname router\n"),
            ("router/revision/_netbox_manifest.json", b'{"artifacts": []}'),
            ("router/revision/RECOVERY_README.txt", b"manual recovery only"),
            ("router/revision/native.tgz", b"native tgz bytes"),
            ("router/revision/native.zip", native_zip),
            ("router/revision/empty/", b""),
        ]
        source = self.package(files)
        original_bytes = source.getvalue()
        with self.encrypt(source) as result, pyzipper.AESZipFile(result) as archive:
            self.assertEqual(len(archive.infolist()), len(files))
            for (name, content), info in zip(files, archive.infolist(), strict=True):
                expected_name = name.filename if isinstance(name, ZipInfo) else name
                self.assertEqual(info.filename, expected_name)
                self.assertEqual(info.wz_aes_strength, 3)
                self.assertTrue(info.flag_bits & 1)
                if content:
                    with self.assertRaises(RuntimeError):
                        archive.read(info)
                    with self.assertRaises((RuntimeError, BadZipFile)):
                        archive.read(info, pwd=b"wrong-password")
                self.assertEqual(archive.read(info, pwd=self.password.encode()), content)
            actual = archive.getinfo(metadata.filename)
            self.assertEqual(actual.date_time, metadata.date_time)
            self.assertEqual(actual.external_attr, metadata.external_attr)
        self.assertFalse(source.closed)
        self.assertEqual(source.getvalue(), original_bytes)

    def test_rejects_unsafe_paths_and_symlinks(self):
        symlink = ZipInfo("router/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in (
            "../secret", "/secret", "router/../secret", "router//file",
            "C:/secret", "router\\file", "router/file:stream", "router/./file",
            "router/file\n", "router/file\x00hidden", symlink,
        ):
            if isinstance(name, str) and ("\\" in name or "\x00" in name):
                # ZipInfo sanitizes these during writing, so corrupt both on-disk
                # filename headers without changing their lengths.
                safe_name = name.replace("\\", "!").replace("\x00", "!")
                raw = self.package([(safe_name, b"bad entry")]).getvalue()
                source = io.BytesIO(raw.replace(safe_name.encode(), name.encode()))
            else:
                source = self.package([(name, b"bad entry")])
            with (
                self.subTest(name=name),
                self.assertRaises(DownloadEncryptionError),
                self.encrypt(source),
            ):
                pass

    def test_rejects_case_insensitive_duplicate_paths(self):
        source = self.package([("router/file", b"one"), ("router/FILE", b"two")])
        with self.assertRaises(DownloadEncryptionError):
            self.encrypt(source)

    def test_rejects_empty_invalid_and_already_encrypted_packages(self):
        encrypted = build_password_protected_zip(
            content=b"secret", member_filename="file", password=self.password
        )
        for source in (self.package([]), io.BytesIO(b"not a zip"), io.BytesIO(encrypted)):
            with self.subTest(source=source), self.assertRaises(DownloadEncryptionError):
                self.encrypt(source)

    def test_rejects_missing_password_and_excessive_uncompressed_size(self):
        for options in ({"password": ""}, {"max_total_bytes": 0}, {"max_total_bytes": 9}):
            with self.subTest(options=options), self.assertRaises(DownloadEncryptionError):
                self.encrypt(self.package([("file", b"1234567890")]), **options)
        with self.encrypt(self.package([("file", b"1234567890")]), max_total_bytes=10):
            pass

    def test_bad_crc_fails_closed_and_closes_partial_output(self):
        source = self.package([("file", b"unique payload")]).getvalue()
        damaged = io.BytesIO(source.replace(b"unique payload", b"broken payload"))
        with SpooledTemporaryFile(max_size=1, mode="w+b") as output:
            with patch(
                "netbox_config_backup.services.download_encryption.SpooledTemporaryFile",
                return_value=output,
            ), self.assertRaises(DownloadEncryptionError):
                self.encrypt(damaged)
            self.assertTrue(output.closed)
        self.assertFalse(damaged.closed)

    def test_stream_spills_to_disk_without_extracting_entry_files(self):
        with SpooledTemporaryFile(max_size=1, mode="w+b") as output, patch(
            "netbox_config_backup.services.download_encryption.SpooledTemporaryFile",
            return_value=output,
        ), self.encrypt(self.package([("file", b"configuration")])) as result:
            self.assertTrue(result._rolled)
            with pyzipper.AESZipFile(result) as archive:
                self.assertEqual(archive.read("file", pwd=self.password.encode()), b"configuration")
        self.assertTrue(output.closed)


if __name__ == "__main__":
    unittest.main()
