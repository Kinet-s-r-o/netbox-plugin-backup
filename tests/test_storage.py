import tempfile
import unittest
from pathlib import Path

from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.factory import build_config_storage
from netbox_config_backup.storage.local import LocalConfigStorage


class LocalStorageTests(unittest.TestCase):
    def test_factory_keeps_local_backend_as_default(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = build_config_storage({"storage_root": directory})

            self.assertIsInstance(storage, LocalConfigStorage)

    def test_factory_rejects_unknown_backend(self):
        with self.assertRaisesRegex(StorageError, "Unknown"):
            build_config_storage({"storage_backend": "mystery", "storage_root": "."})

    def test_round_trip_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)
            key = "devices/1/revisions/abc/running-config.txt"

            stored = storage.put(key, b"hostname router\n", {"kind": "running"})

            self.assertEqual(stored.size, 16)
            self.assertTrue(storage.exists(key))
            self.assertEqual(storage.get(key), b"hostname router\n")
            storage.delete(key)
            self.assertFalse(storage.exists(key))

    def test_rejects_path_traversal_and_windows_separators(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)
            for key in ("../secret", "/absolute", "devices\\1\\config"):
                with self.subTest(key=key), self.assertRaises(StorageError):
                    storage.put(key, b"secret")

    def test_write_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)
            key = "devices/1/revisions/abc/running-config.txt"
            storage.put(key, b"one")
            storage.put(key, b"two")

            files = [path for path in Path(directory).rglob("*") if path.is_file()]

            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"two")

    def test_missing_object_read_is_a_safe_storage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)

            with self.assertRaisesRegex(StorageError, "read failed"):
                storage.get("devices/1/missing.txt")

    def test_staged_delete_can_be_restored_or_purged(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)
            key = "devices/1/revisions/abc/running-config.txt"
            storage.put(key, b"hostname router\n")

            staged_key = storage.stage_delete(key, "cleanup-1")

            self.assertIsNotNone(staged_key)
            self.assertFalse(storage.exists(key))
            self.assertTrue(storage.exists(staged_key))
            storage.restore_staged_delete(key, staged_key)
            self.assertTrue(storage.exists(key))
            self.assertFalse(storage.exists(staged_key))

            staged_key = storage.stage_delete(key, "cleanup-2")
            storage.purge_staged_delete(staged_key)
            self.assertFalse(storage.exists(key))
            self.assertFalse(storage.exists(staged_key))

    def test_stage_delete_rejects_invalid_namespace_and_reports_missing_object(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalConfigStorage(directory)
            with self.assertRaisesRegex(StorageError, "namespace"):
                storage.stage_delete("devices/1/config.txt", "../unsafe")
            self.assertIsNone(storage.stage_delete("devices/1/missing.txt", "cleanup-1"))


if __name__ == "__main__":
    unittest.main()
