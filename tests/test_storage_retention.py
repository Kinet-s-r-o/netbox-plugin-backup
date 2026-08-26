import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from netbox_config_backup.services.retention import (
    effective_local_retention_policy,
    effective_remote_retention_policy,
    effective_remote_retention_policy_id,
    local_retention_policy_source,
    remote_retention_policy_source,
)


def target(
    *,
    local=None,
    backup_policy_retention=None,
    remote=None,
):
    backup_policy = (
        SimpleNamespace(retention_policy=backup_policy_retention)
        if backup_policy_retention is not None
        else None
    )
    return SimpleNamespace(
        retention_override_id=getattr(local, "pk", None),
        retention_override=local,
        policy_override_id=1 if backup_policy is not None else None,
        policy_override=backup_policy,
        remote_retention_policy_id=getattr(remote, "pk", None),
        remote_retention_policy=remote,
    )


def local_storage(*, policy=None, enforced=False):
    return SimpleNamespace(
        enforce_retention_policy=enforced,
        local_retention_policy_id=getattr(policy, "pk", None),
        local_retention_policy=policy,
    )


def ftp_storage(*, policy=None, enforced=False):
    return SimpleNamespace(
        enforce_retention_policy=enforced,
        remote_retention_policy_id=getattr(policy, "pk", None),
        remote_retention_policy=policy,
    )


class LocalStorageRetentionPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.storage_policy = SimpleNamespace(pk=1, name="Local storage")
        self.device_policy = SimpleNamespace(pk=2, name="Device local")
        self.backup_policy = SimpleNamespace(pk=3, name="Backup policy")

    def test_enforced_storage_policy_wins_over_device_and_backup_policy(self):
        storage = local_storage(policy=self.storage_policy, enforced=True)
        device = target(
            local=self.device_policy,
            backup_policy_retention=self.backup_policy,
        )

        self.assertIs(effective_local_retention_policy(device, storage), self.storage_policy)
        self.assertEqual(local_retention_policy_source(device, storage), "Storage enforced")

    def test_device_policy_wins_over_backup_policy_and_storage_fallback(self):
        storage = local_storage(policy=self.storage_policy)
        device = target(
            local=self.device_policy,
            backup_policy_retention=self.backup_policy,
        )

        self.assertIs(effective_local_retention_policy(device, storage), self.device_policy)
        self.assertEqual(local_retention_policy_source(device, storage), "Device override")

    def test_backup_policy_wins_over_storage_fallback(self):
        storage = local_storage(policy=self.storage_policy)
        device = target(backup_policy_retention=self.backup_policy)

        self.assertIs(effective_local_retention_policy(device, storage), self.backup_policy)
        self.assertEqual(local_retention_policy_source(device, storage), "Backup policy")

    def test_storage_policy_is_the_final_fallback(self):
        storage = local_storage(policy=self.storage_policy)
        device = target()

        self.assertIs(effective_local_retention_policy(device, storage), self.storage_policy)
        self.assertEqual(local_retention_policy_source(device, storage), "Storage default")

    def test_no_policy_means_keep_indefinitely(self):
        storage = local_storage()
        device = target()

        self.assertIsNone(effective_local_retention_policy(device, storage))
        self.assertEqual(local_retention_policy_source(device, storage), "Keep indefinitely")


class FtpStorageRetentionPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.storage_policy = SimpleNamespace(pk=11, name="FTP storage")
        self.device_policy = SimpleNamespace(pk=12, name="Device FTP")

    def test_enforced_storage_policy_wins_over_device_policy(self):
        storage = ftp_storage(policy=self.storage_policy, enforced=True)
        device = target(remote=self.device_policy)

        self.assertIs(effective_remote_retention_policy(device, storage), self.storage_policy)
        self.assertEqual(
            effective_remote_retention_policy_id(device, storage), self.storage_policy.pk
        )
        self.assertEqual(remote_retention_policy_source(device, storage), "Storage enforced")

    def test_device_policy_wins_over_storage_fallback(self):
        storage = ftp_storage(policy=self.storage_policy)
        device = target(remote=self.device_policy)

        self.assertIs(effective_remote_retention_policy(device, storage), self.device_policy)
        self.assertEqual(
            effective_remote_retention_policy_id(device, storage), self.device_policy.pk
        )
        self.assertEqual(remote_retention_policy_source(device, storage), "Device override")

    def test_storage_policy_is_the_final_fallback(self):
        storage = ftp_storage(policy=self.storage_policy)
        device = target()

        self.assertIs(effective_remote_retention_policy(device, storage), self.storage_policy)
        self.assertEqual(remote_retention_policy_source(device, storage), "Storage default")

    def test_no_policy_means_keep_indefinitely(self):
        storage = ftp_storage()
        device = target()

        self.assertIsNone(effective_remote_retention_policy(device, storage))
        self.assertIsNone(effective_remote_retention_policy_id(device, storage))
        self.assertEqual(remote_retention_policy_source(device, storage), "Keep indefinitely")

    def test_two_ftp_storages_resolve_independently(self):
        first_policy = SimpleNamespace(pk=21, name="FTP short")
        second_policy = SimpleNamespace(pk=22, name="FTP archive")
        device_policy = SimpleNamespace(pk=23, name="Device FTP")
        device = target(remote=device_policy)
        first = ftp_storage(policy=first_policy, enforced=True)
        second = ftp_storage(policy=second_policy, enforced=False)

        self.assertIs(effective_remote_retention_policy(device, first), first_policy)
        self.assertIs(effective_remote_retention_policy(device, second), device_policy)
        self.assertEqual(effective_remote_retention_policy_id(device, first), first_policy.pk)
        self.assertEqual(effective_remote_retention_policy_id(device, second), device_policy.pk)
        self.assertEqual(remote_retention_policy_source(device, first), "Storage enforced")
        self.assertEqual(remote_retention_policy_source(device, second), "Device override")


class ReplicationStorageSelectionTests(unittest.TestCase):
    @staticmethod
    def _has_explicit_ftp_filter(function_name):
        source = (
            Path(__file__).resolve().parents[1]
            / "netbox_config_backup"
            / "services"
            / "replication.py"
        ).read_text(encoding="utf-8")
        module = ast.parse(source)
        function = next(
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        )
        for call in ast.walk(function):
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "filter":
                continue
            for keyword in call.keywords:
                value = keyword.value
                if (
                    keyword.arg == "protocol"
                    and isinstance(value, ast.Attribute)
                    and value.attr == "FTP"
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "DestinationProtocolChoices"
                ):
                    return True
        return False

    def test_new_revision_replication_explicitly_selects_ftp_storage(self):
        self.assertTrue(self._has_explicit_ftp_filter("create_revision_replicas"))

    def test_unchanged_revision_repair_explicitly_selects_ftp_storage(self):
        self.assertTrue(self._has_explicit_ftp_filter("ensure_revision_replicas"))


if __name__ == "__main__":
    unittest.main()
