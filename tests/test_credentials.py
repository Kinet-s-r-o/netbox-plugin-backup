import unittest

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.credentials.base import (
    CredentialMaterial,
    SecretProvider,
    SecretProviderError,
)
from netbox_config_backup.credentials.environment import EnvironmentSecretProvider
from netbox_config_backup.credentials.registry import SecretProviderRegistry


class ExampleProvider(SecretProvider):
    provider_id = "example"

    def resolve(self, reference: str) -> CredentialMaterial:
        return CredentialMaterial(username=reference, password="very-secret")


class CredentialTests(unittest.TestCase):
    def test_repr_does_not_expose_secret(self):
        material = CredentialMaterial(username="backup", password="very-secret")

        rendered = repr(material)

        self.assertNotIn("very-secret", rendered)
        self.assertIn("<redacted>", rendered)

    def test_exactly_one_authentication_material_is_required(self):
        with self.assertRaises(ValueError):
            CredentialMaterial(username="backup")
        with self.assertRaises(ValueError):
            CredentialMaterial(username="backup", password="secret", private_key="key")

    def test_provider_registry_resolves_registered_provider(self):
        registry = SecretProviderRegistry()
        registry.register(ExampleProvider())

        result = registry.get("example").resolve("backup")

        self.assertEqual(result.username, "backup")

    def test_environment_provider_is_registered(self):
        self.assertTrue(secret_provider_registry.contains("environment"))

    def test_environment_provider_resolves_password_credentials(self):
        provider = EnvironmentSecretProvider(
            {
                "ROUTER_1_USERNAME": "backup",
                "ROUTER_1_PASSWORD": "very-secret",
                "ROUTER_1_ENABLE_SECRET": "enable-secret",
            }
        )

        result = provider.resolve("env://ROUTER_1")

        self.assertEqual(result.username, "backup")
        self.assertEqual(result.password, "very-secret")
        self.assertEqual(result.enable_secret, "enable-secret")
        self.assertIsNone(result.private_key)

    def test_environment_provider_resolves_private_key_credentials(self):
        provider = EnvironmentSecretProvider(
            {
                "CORE_SWITCH_USERNAME": "backup",
                "CORE_SWITCH_PRIVATE_KEY": "private-key-data",
            }
        )

        result = provider.resolve("env://CORE_SWITCH")

        self.assertEqual(result.username, "backup")
        self.assertEqual(result.private_key, "private-key-data")
        self.assertIsNone(result.password)

    def test_environment_provider_rejects_invalid_or_incomplete_configuration(self):
        cases = (
            ({}, "env://ROUTER_1"),
            ({"ROUTER_1_USERNAME": "backup"}, "env://ROUTER_1"),
            (
                {
                    "ROUTER_1_USERNAME": "backup",
                    "ROUTER_1_PASSWORD": "password-value",
                    "ROUTER_1_PRIVATE_KEY": "private-key-value",
                },
                "env://ROUTER_1",
            ),
            ({"ROUTER_1_USERNAME": "backup", "ROUTER_1_PASSWORD": "secret"}, "ROUTER_1"),
            ({"ROUTER_1_USERNAME": "backup", "ROUTER_1_PASSWORD": "secret"}, "env://bad-name"),
        )

        for environment, reference in cases:
            with self.subTest(reference=reference, keys=tuple(environment)):
                with self.assertRaises(SecretProviderError) as raised:
                    EnvironmentSecretProvider(environment).resolve(reference)
                self.assertEqual(str(raised.exception), "Credential resolution failed.")

    def test_environment_provider_error_does_not_expose_values(self):
        provider = EnvironmentSecretProvider(
            {
                "ROUTER_1_USERNAME": "sensitive-username",
                "ROUTER_1_PASSWORD": "sensitive-password",
                "ROUTER_1_PRIVATE_KEY": "sensitive-private-key",
            }
        )

        with self.assertRaises(SecretProviderError) as raised:
            provider.resolve("env://ROUTER_1")

        rendered = repr(raised.exception)
        self.assertNotIn("sensitive-username", rendered)
        self.assertNotIn("sensitive-password", rendered)
        self.assertNotIn("sensitive-private-key", rendered)


if __name__ == "__main__":
    unittest.main()
