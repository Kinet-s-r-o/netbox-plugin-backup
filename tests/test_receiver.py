import tempfile
import unittest
from pathlib import Path

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.receiver.paths import receiver_inbox_path
from netbox_config_backup.receiver.server import (
    UploadOnlySFTPServer,
    ensure_host_key,
    prepare_receiver_root,
    validate_receiver_credentials,
)


class ReceiverTests(unittest.TestCase):
    def test_paths_are_profile_scoped_and_reject_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            result = receiver_inbox_path(directory, 7, "incoming")
            self.assertEqual(result, Path(directory).resolve() / "profile-7" / "incoming")
            for unsafe in ("", ".", "..", "../outside", "a/b", "a\\b"):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    receiver_inbox_path(directory, 7, unsafe)

    def test_receiver_credentials_use_ceraos_safe_values(self):
        validate_receiver_credentials(
            CredentialMaterial(username="ceragon.backup", password="Safe-Value_123")
        )
        for credentials in (
            CredentialMaterial(username="bad user", password="Safe-Value_123"),
            CredentialMaterial(username="backup", password="short"),
            CredentialMaterial(username="backup", password="unsafe password"),
        ):
            with self.subTest(credentials=credentials), self.assertRaises(ValueError):
                validate_receiver_credentials(credentials)

    def test_host_key_and_chroot_are_persistent_and_symlink_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile-1"
            inbox = prepare_receiver_root(root, "incoming")
            key_path = ensure_host_key(Path(directory) / "host-key")
            first_key = key_path.read_bytes()
            self.assertTrue(inbox.is_dir())
            self.assertIn(b"PRIVATE KEY", first_key)
            self.assertEqual(ensure_host_key(key_path).read_bytes(), first_key)

    def test_double_slash_client_path_stays_inside_chroot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile-1"
            prepare_receiver_root(root, "incoming")
            server = UploadOnlySFTPServer(object(), chroot=str(root).encode())
            mapped = Path(server.map_path(b"//incoming/config.zip").decode())
            expected = root.resolve() / "incoming" / "config.zip"
            self.assertEqual(mapped.as_posix().lstrip("/"), expected.as_posix().lstrip("/"))


if __name__ == "__main__":
    unittest.main()
