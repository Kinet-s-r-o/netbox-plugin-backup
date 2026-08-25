import unittest
from types import SimpleNamespace
from unittest.mock import patch

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.credentials.vault import VaultKV2SecretProvider


class FakeKV2:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def read_secret_version(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.data, Exception):
            raise self.data
        return {"data": {"data": self.data, "metadata": {"version": 1}}}


class FakeAppRole:
    def __init__(self):
        self.calls = []

    def login(self, **kwargs):
        self.calls.append(kwargs)


class FakeVaultClient:
    def __init__(self, data, **kwargs):
        self.init_kwargs = kwargs
        self.token = None
        self.kv2 = FakeKV2(data)
        self.approle = FakeAppRole()
        self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=self.kv2))
        self.auth = SimpleNamespace(approle=self.approle)


class VaultCredentialTests(unittest.TestCase):
    def make_provider(self, data, *, config=None, environment=None):
        clients = []

        def factory(**kwargs):
            client = FakeVaultClient(data, **kwargs)
            clients.append(client)
            return client

        settings = {
            "vault_enabled": True,
            "vault_addr": "https://vault.example.test:8200",
            "vault_auth_method": "token",
            "vault_verify_tls": True,
            "vault_timeout": 7,
            **(config or {}),
        }
        provider = VaultKV2SecretProvider(
            settings,
            environment or {"NETBOX_CONFIG_BACKUP_VAULT_TOKEN": "test-token"},
            factory,
        )
        return provider, clients

    def test_provider_is_registered(self):
        self.assertTrue(secret_provider_registry.contains("vault_kv2"))

    def test_resolves_password_from_kv_v2(self):
        provider, clients = self.make_provider(
            {
                "username": "backup",
                "password": "very-secret",
                "enable_secret": "enable-secret",
            }
        )

        material = provider.resolve("vault://network/device/router-1")

        self.assertEqual(material.username, "backup")
        self.assertEqual(material.password, "very-secret")
        self.assertEqual(material.enable_secret, "enable-secret")
        self.assertEqual(clients[0].token, "test-token")
        self.assertEqual(
            clients[0].kv2.calls,
            [
                {
                    "path": "device/router-1",
                    "mount_point": "network",
                    "raise_on_deleted_version": True,
                }
            ],
        )
        self.assertTrue(clients[0].init_kwargs["verify"])

    def test_resolves_private_key_with_approle(self):
        provider, clients = self.make_provider(
            {"username": "backup", "private_key": "private-key"},
            config={"vault_auth_method": "approle", "vault_auth_mount_point": "netbox"},
            environment={
                "NETBOX_CONFIG_BACKUP_VAULT_ROLE_ID": "role-id",
                "NETBOX_CONFIG_BACKUP_VAULT_SECRET_ID": "secret-id",
            },
        )

        material = provider.resolve("vault://secret/devices/core")

        self.assertEqual(material.private_key, "private-key")
        self.assertEqual(
            clients[0].approle.calls,
            [{"role_id": "role-id", "secret_id": "secret-id", "mount_point": "netbox"}],
        )

    def test_rejects_disabled_insecure_or_invalid_configuration(self):
        cases = (
            ({"vault_enabled": False}, "vault://secret/router"),
            ({"vault_addr": "http://vault:8200"}, "vault://secret/router"),
            ({}, "vault://secret/../router"),
            ({}, "vault://secret/router?version=1"),
            ({}, "env://ROUTER"),
        )
        for config, reference in cases:
            with self.subTest(config=config, reference=reference):
                provider, _clients = self.make_provider({}, config=config)
                with self.assertRaises(SecretProviderError):
                    provider.resolve(reference)

    def test_errors_do_not_expose_secret_values_or_vault_exception(self):
        provider, _clients = self.make_provider(RuntimeError("server leaked sensitive-value"))

        with self.assertRaises(SecretProviderError) as raised:
            provider.resolve("vault://secret/device/router")

        self.assertEqual(str(raised.exception), "Credential resolution failed.")
        self.assertNotIn("sensitive-value", str(raised.exception))

    def test_missing_optional_dependency_has_actionable_error(self):
        provider, _clients = self.make_provider({})
        provider._client_factory = None
        original_import = __import__

        def import_without_hvac(name, *args, **kwargs):
            if name == "hvac":
                raise ModuleNotFoundError("No module named 'hvac'", name="hvac")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=import_without_hvac),
            self.assertRaisesRegex(SecretProviderError, "optional 'vault'"),
        ):
            provider.resolve("vault://secret/device/router")


if __name__ == "__main__":
    unittest.main()
