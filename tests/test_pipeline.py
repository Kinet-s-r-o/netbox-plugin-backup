from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from netbox_config_backup.credentials.base import CredentialMaterial, SecretProvider
from netbox_config_backup.credentials.registry import SecretProviderRegistry
from netbox_config_backup.drivers.fake import FakeDriver
from netbox_config_backup.drivers.registry import DriverRegistry
from netbox_config_backup.services.backup import BackupPipeline
from netbox_config_backup.services.repository import (
    ExecutionContext,
    RevisionSnapshot,
    StoredArtifactRecord,
)
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.local import LocalConfigStorage

FIXED_TIME = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
FIXED_UUID = UUID("11111111-2222-3333-4444-555555555555")


class MemoryRepository:
    def __init__(self, context: ExecutionContext) -> None:
        self.context = context
        self.status = "queued"
        self.latest: RevisionSnapshot | None = None
        self.revisions: list[dict] = []
        self.failure: dict | None = None

    def get_execution_context(self, run_id):
        return self.context

    def mark_running(self, run_id, *, started_at):
        self.status = "running"

    def get_latest_revision(self, target_id):
        return self.latest

    def commit_unchanged(self, run_id, *, raw_changed, finished_at):
        self.status = "success_unchanged"
        return self.latest.revision_id if self.latest else None

    def commit_revision(
        self,
        run_id,
        *,
        revision_uuid,
        normalized_hash,
        normalizer_version,
        driver_id,
        content_changed,
        raw_changed,
        artifacts,
        finished_at,
    ):
        revision_id = len(self.revisions) + 1
        self.revisions.append(
            {
                "id": revision_id,
                "uuid": revision_uuid,
                "normalized_hash": normalized_hash,
                "normalizer_version": normalizer_version,
                "driver_id": driver_id,
                "content_changed": content_changed,
                "raw_changed": raw_changed,
                "artifacts": artifacts,
            }
        )
        primary = next(item for item in artifacts if item.is_primary)
        self.latest = RevisionSnapshot(
            revision_id=revision_id,
            normalized_hash=normalized_hash,
            primary_raw_hash=primary.raw_hash,
        )
        self.status = "success_changed" if content_changed else "success_unchanged"
        return revision_id

    def mark_failed(
        self,
        run_id,
        *,
        status,
        error_code,
        error_message,
        finished_at,
    ):
        self.status = status
        self.failure = {
            "error_code": error_code,
            "error_message": error_message,
        }


class FailingStorage(LocalConfigStorage):
    def put(self, key, content, metadata=None):
        raise StorageError("Simulated storage failure.")


class RecordingSecretProvider(SecretProvider):
    provider_id = "test"

    def __init__(self):
        self.references = []

    def resolve(self, reference):
        self.references.append(reference)
        return CredentialMaterial(username="backup", password="safe-test-value")


class FailingCommitRepository(MemoryRepository):
    def commit_revision(self, *args, **kwargs):
        raise RuntimeError("database unavailable")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.context = ExecutionContext(
            run_id=1,
            target_id=10,
            device_id=100,
            device_name="router-1",
            driver_id="fake",
        )
        self.drivers = DriverRegistry()
        self.drivers.register(FakeDriver)

    def make_pipeline(self, repository, storage, secret_providers=None):
        return BackupPipeline(
            repository=repository,
            drivers=self.drivers,
            storage=storage,
            secret_providers=secret_providers,
            clock=lambda: FIXED_TIME,
            uuid_factory=lambda: FIXED_UUID,
        )

    def test_first_backup_creates_revision_and_stores_raw_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(self.context)
            storage = LocalConfigStorage(directory)

            result = self.make_pipeline(repository, storage).execute(1)

            self.assertEqual(result.status, "success_changed")
            self.assertTrue(result.changed)
            self.assertEqual(len(repository.revisions), 1)
            artifact: StoredArtifactRecord = repository.revisions[0]["artifacts"][0]
            self.assertEqual(
                artifact.storage_key,
                "devices/100/revisions/11111111-2222-3333-4444-555555555555/running-config.txt",
            )
            self.assertTrue(storage.exists(artifact.storage_key))

    def test_second_identical_backup_does_not_create_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(self.context)
            storage = LocalConfigStorage(directory)
            pipeline = self.make_pipeline(repository, storage)
            pipeline.execute(1)

            result = pipeline.execute(1)

            self.assertEqual(result.status, "success_unchanged")
            self.assertFalse(result.changed)
            self.assertEqual(len(repository.revisions), 1)

    def test_raw_change_ignored_by_normalizer_is_audited_without_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            first_context = replace(
                self.context,
                driver_options={
                    "config": "! Last configuration change at 10:00\nhostname router-1\n"
                },
            )
            repository = MemoryRepository(first_context)
            storage = LocalConfigStorage(directory)
            pipeline = self.make_pipeline(repository, storage)
            pipeline.execute(1)
            repository.context = replace(
                first_context,
                driver_options={
                    "config": "! Last configuration change at 11:00\nhostname router-1\n"
                },
            )

            result = pipeline.execute(1)

            self.assertEqual(result.status, "success_unchanged")
            self.assertTrue(result.raw_changed)
            self.assertEqual(len(repository.revisions), 1)

    def test_every_success_stores_revision_without_claiming_content_change(self):
        with tempfile.TemporaryDirectory() as directory:
            context = replace(self.context, store_mode="every_success")
            repository = MemoryRepository(context)
            storage = LocalConfigStorage(directory)
            pipeline = self.make_pipeline(repository, storage)
            pipeline.execute(1)

            result = pipeline.execute(1)

            self.assertEqual(result.status, "success_unchanged")
            self.assertFalse(repository.revisions[-1]["content_changed"])
            self.assertEqual(len(repository.revisions), 2)

    def test_driver_failure_marks_run_failed_without_writing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            context = replace(
                self.context,
                driver_options={"failure_code": "CONNECTION_TIMEOUT"},
            )
            repository = MemoryRepository(context)
            storage = LocalConfigStorage(directory)

            result = self.make_pipeline(repository, storage).execute(1)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "CONNECTION_TIMEOUT")
            self.assertEqual(repository.revisions, [])

    def test_storage_failure_marks_run_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(self.context)

            result = self.make_pipeline(repository, FailingStorage(directory)).execute(1)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "STORAGE_FAILED")
            self.assertEqual(repository.revisions, [])

    def test_empty_config_is_a_stable_validation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(replace(self.context, driver_options={"config": "  \n"}))

            result = self.make_pipeline(repository, LocalConfigStorage(directory)).execute(1)

            self.assertEqual(result.error_code, "EMPTY_CONFIG")
            self.assertEqual(repository.status, "failed")

    def test_unknown_driver_is_reported_as_unsupported_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(replace(self.context, driver_id="missing"))

            result = self.make_pipeline(repository, LocalConfigStorage(directory)).execute(1)

            self.assertEqual(result.error_code, "UNSUPPORTED_PLATFORM")

    def test_secret_reference_is_resolved_only_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(
                replace(
                    self.context,
                    secret_provider_id="test",
                    secret_reference="network/router-1",
                )
            )
            providers = SecretProviderRegistry()
            provider = RecordingSecretProvider()
            providers.register(provider)

            result = self.make_pipeline(
                repository, LocalConfigStorage(directory), providers
            ).execute(1)

            self.assertEqual(result.status, "success_changed")
            self.assertEqual(provider.references, ["network/router-1"])

    def test_incomplete_secret_configuration_fails_before_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MemoryRepository(replace(self.context, secret_provider_id="test"))

            result = self.make_pipeline(repository, LocalConfigStorage(directory)).execute(1)

            self.assertEqual(result.error_code, "NO_CREDENTIAL_PROFILE")

    def test_database_failure_rolls_back_stored_artifact_and_is_reraised(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = FailingCommitRepository(self.context)
            storage = LocalConfigStorage(directory)

            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.make_pipeline(repository, storage).execute(1)

            self.assertEqual(repository.status, "errored")
            self.assertEqual(repository.failure["error_code"], "INTERNAL_ERROR")
            self.assertEqual([path for path in storage.root.rglob("*") if path.is_file()], [])


if __name__ == "__main__":
    unittest.main()
