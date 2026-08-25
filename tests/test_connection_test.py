import unittest
from dataclasses import replace

from netbox_config_backup.credentials.base import CredentialMaterial, SecretProvider
from netbox_config_backup.credentials.registry import SecretProviderRegistry
from netbox_config_backup.drivers.base import BackupDriver, DriverError
from netbox_config_backup.drivers.fake import FakeDriver
from netbox_config_backup.drivers.registry import DriverRegistry
from netbox_config_backup.services.connection_test import ConnectionTester
from netbox_config_backup.services.repository import ExecutionContext


class MemoryTargetRepository:
    def __init__(self, context):
        self.context = context
        self.target_ids = []

    def get_target_execution_context(self, target_id):
        self.target_ids.append(target_id)
        return self.context


class RecordingProvider(SecretProvider):
    provider_id = "test"

    def __init__(self):
        self.references = []

    def resolve(self, reference):
        self.references.append(reference)
        return CredentialMaterial(username="backup", password="resolved-secret")


class CredentialCheckingDriver(FakeDriver):
    driver_id = "credential_check"

    def collect(self, context):
        if context.credentials is None or context.credentials.password != "resolved-secret":
            raise DriverError("AUTH_FAILED", "Resolved credentials were not supplied.")
        return super().collect(context)


class UnexpectedDriver(BackupDriver):
    driver_id = "unexpected"
    display_name = "Unexpected"

    def collect(self, context):
        raise ValueError("programming bug")

    def validate(self, artifact):
        raise NotImplementedError

    def normalize(self, artifact):
        raise NotImplementedError


class ConnectionTesterTests(unittest.TestCase):
    def setUp(self):
        self.context = ExecutionContext(
            run_id=None,
            target_id=10,
            device_id=20,
            device_name="router-1",
            driver_id="fake",
            driver_options={"config": "hostname router-1\n"},
        )

    @staticmethod
    def registry(*drivers):
        registry = DriverRegistry()
        for driver in drivers:
            registry.register(driver)
        return registry

    def test_success_validates_collection_without_repository_writes(self):
        repository = MemoryTargetRepository(self.context)
        tester = ConnectionTester(
            repository=repository,
            drivers=self.registry(FakeDriver),
        )

        result = tester.execute(10)

        self.assertTrue(result.success)
        self.assertEqual(result.driver_id, "fake")
        self.assertEqual(result.artifact_count, 1)
        self.assertGreater(result.total_bytes, 0)
        self.assertEqual(repository.target_ids, [10])

    def test_credentials_are_resolved_only_for_the_test(self):
        repository = MemoryTargetRepository(
            replace(
                self.context,
                driver_id="credential_check",
                secret_provider_id="test",
                secret_reference="env://ROUTER_1",
            )
        )
        providers = SecretProviderRegistry()
        provider = RecordingProvider()
        providers.register(provider)

        result = ConnectionTester(
            repository=repository,
            drivers=self.registry(CredentialCheckingDriver),
            secret_providers=providers,
        ).execute(10)

        self.assertTrue(result.success)
        self.assertEqual(provider.references, ["env://ROUTER_1"])

    def test_expected_failures_return_safe_codes(self):
        cases = (
            (replace(self.context, driver_id="missing"), "UNSUPPORTED_PLATFORM"),
            (
                replace(
                    self.context,
                    secret_provider_id="missing",
                    secret_reference="sensitive-reference",
                ),
                "SECRET_RESOLUTION_FAILED",
            ),
            (
                replace(self.context, driver_options={"failure_code": "TIMEOUT"}),
                "TIMEOUT",
            ),
            (
                replace(self.context, driver_options={"config": "  \n"}),
                "EMPTY_CONFIG",
            ),
        )
        for context, error_code in cases:
            with self.subTest(error_code=error_code):
                result = ConnectionTester(
                    repository=MemoryTargetRepository(context),
                    drivers=self.registry(FakeDriver),
                ).execute(10)
                self.assertFalse(result.success)
                self.assertEqual(result.error_code, error_code)
                self.assertNotIn("sensitive-reference", result.safe_message)

    def test_unexpected_driver_error_is_not_hidden(self):
        tester = ConnectionTester(
            repository=MemoryTargetRepository(replace(self.context, driver_id="unexpected")),
            drivers=self.registry(UnexpectedDriver),
        )

        with self.assertRaisesRegex(ValueError, "programming bug"):
            tester.execute(10)


if __name__ == "__main__":
    unittest.main()
