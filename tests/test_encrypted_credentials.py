import base64
import json
import unittest
from uuid import UUID

from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.credentials.encrypted_database import (
    DatabaseCredentialCipher,
    EncryptedDatabaseSecretProvider,
    MasterKeyConfigurationError,
    StoredCredentialData,
)

REFERENCE = UUID("12345678-1234-5678-1234-567812345678")
MASTER_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
SECOND_MASTER_KEY = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")


class EncryptedCredentialTests(unittest.TestCase):
    def make_cipher(self, **overrides):
        environment = {
            "NETBOX_CONFIG_BACKUP_MASTER_KEY": MASTER_KEY,
            "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "v1",
        }
        environment.update(overrides)
        return DatabaseCredentialCipher(
            environment,
            nonce_factory=lambda size: b"n" * size,
        )

    def test_aes_gcm_round_trip_and_redacted_repr(self):
        cipher = self.make_cipher()

        payload = cipher.encrypt(reference=REFERENCE, plaintext="very-secret-password")
        decrypted = cipher.decrypt(
            reference=REFERENCE,
            ciphertext=payload.ciphertext,
            nonce=payload.nonce,
            key_version=payload.key_version,
        )

        self.assertEqual(decrypted, "very-secret-password")
        self.assertNotIn("very-secret-password", repr(payload))
        self.assertNotIn(base64.b64encode(payload.ciphertext).decode(), repr(payload))
        self.assertIn("<redacted>", repr(payload))

    def test_ciphertext_is_bound_to_reference_and_key_version(self):
        cipher = self.make_cipher()
        payload = cipher.encrypt(reference=REFERENCE, plaintext="very-secret-password")

        with self.assertRaises(MasterKeyConfigurationError):
            cipher.decrypt(
                reference=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                ciphertext=payload.ciphertext,
                nonce=payload.nonce,
                key_version=payload.key_version,
            )
        with self.assertRaises(MasterKeyConfigurationError):
            self.make_cipher(NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION="v2").decrypt(
                reference=REFERENCE,
                ciphertext=payload.ciphertext,
                nonce=payload.nonce,
                key_version=payload.key_version,
            )

    def test_previous_key_can_decrypt_while_new_writes_use_active_key(self):
        old_cipher = self.make_cipher()
        old_payload = old_cipher.encrypt(reference=REFERENCE, plaintext="very-secret-password")
        rotating_cipher = self.make_cipher(
            NETBOX_CONFIG_BACKUP_MASTER_KEY=SECOND_MASTER_KEY,
            NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION="v2",
            NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS=json.dumps({"v1": MASTER_KEY}),
        )

        self.assertEqual(
            rotating_cipher.decrypt(
                reference=REFERENCE,
                ciphertext=old_payload.ciphertext,
                nonce=old_payload.nonce,
                key_version=old_payload.key_version,
            ),
            "very-secret-password",
        )
        new_payload = rotating_cipher.encrypt(reference=REFERENCE, plaintext="replacement-password")
        self.assertEqual(new_payload.key_version, "v2")
        self.assertEqual(rotating_cipher.configured_key_versions(), ("v2", "v1"))

    def test_previous_key_configuration_is_strictly_validated(self):
        cases = (
            "not-json",
            "[]",
            '{"v1":"invalid"}',
            json.dumps({"v1": MASTER_KEY, "v2": SECOND_MASTER_KEY}),
            f'{{"v1":"{MASTER_KEY}","v1":"{SECOND_MASTER_KEY}"}}',
            json.dumps({"v0": SECOND_MASTER_KEY, "v1": SECOND_MASTER_KEY}),
        )
        for previous_keys in cases:
            with (
                self.subTest(previous_keys=previous_keys[:20]),
                self.assertRaises(MasterKeyConfigurationError),
            ):
                self.make_cipher(
                    NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS=previous_keys
                ).configured_key_versions()

    def test_master_key_validation(self):
        cases = (
            {},
            {"NETBOX_CONFIG_BACKUP_MASTER_KEY": "invalid"},
            {
                "NETBOX_CONFIG_BACKUP_MASTER_KEY": MASTER_KEY,
                "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "bad version",
            },
        )
        for environment in cases:
            with (
                self.subTest(environment_keys=tuple(environment)),
                self.assertRaises(MasterKeyConfigurationError),
            ):
                DatabaseCredentialCipher(environment).active_key()

    def test_provider_resolves_database_reference_without_exposing_secret(self):
        cipher = self.make_cipher()
        payload = cipher.encrypt(reference=REFERENCE, plaintext="very-secret-password")

        def lookup(reference):
            self.assertEqual(reference, REFERENCE)
            return StoredCredentialData(
                username="backup-user",
                ciphertext=payload.ciphertext,
                nonce=payload.nonce,
                key_version=payload.key_version,
            )

        provider = EncryptedDatabaseSecretProvider(cipher=cipher, lookup=lookup)
        material = provider.resolve(f"db://{REFERENCE}")

        self.assertEqual(material.username, "backup-user")
        self.assertEqual(material.password, "very-secret-password")
        self.assertNotIn("very-secret-password", repr(material))

    def test_provider_returns_only_generic_errors(self):
        provider = EncryptedDatabaseSecretProvider(
            cipher=self.make_cipher(),
            lookup=lambda _reference: (_ for _ in ()).throw(RuntimeError("database detail")),
        )

        for reference in ("not-db-reference", f"db://{REFERENCE}"):
            with self.subTest(reference=reference):
                with self.assertRaises(SecretProviderError) as raised:
                    provider.resolve(reference)
                self.assertEqual(str(raised.exception), "Credential resolution failed.")
                self.assertNotIn("database detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
