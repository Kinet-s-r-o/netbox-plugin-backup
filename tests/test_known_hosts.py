import tempfile
import unittest
from pathlib import Path

from netbox_config_backup.drivers.base import ConnectionParameters, DriverError
from netbox_config_backup.transports.known_hosts import materialized_known_hosts


class MaterializedKnownHostsTests(unittest.TestCase):
    def test_database_keys_are_merged_with_the_deployment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "known_hosts"
            source.write_text("old.example ssh-ed25519 AAAAold\n", encoding="utf-8")
            connection = ConnectionParameters(
                known_hosts_path=str(source),
                trusted_host_keys=("[192.0.2.10]:2222 ssh-rsa AAAAnew",),
            )

            with materialized_known_hosts(connection) as merged_path:
                self.assertNotEqual(merged_path, str(source))
                merged = Path(merged_path)
                self.assertTrue(merged.exists())
                content = merged.read_text(encoding="utf-8")
                self.assertIn("old.example ssh-ed25519 AAAAold", content)
                self.assertIn("[192.0.2.10]:2222 ssh-rsa AAAAnew", content)

            self.assertFalse(merged.exists())
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "old.example ssh-ed25519 AAAAold\n",
            )

    def test_invalid_database_key_is_rejected(self):
        connection = ConnectionParameters(
            trusted_host_keys=("host ssh-rsa AAAA\ninjected",),
        )

        with self.assertRaises(DriverError) as raised, materialized_known_hosts(connection):
            pass

        self.assertEqual(raised.exception.error_code, "KNOWN_HOSTS_INVALID")


if __name__ == "__main__":
    unittest.main()
