from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.credentials.registry import (
    SecretProviderLookupError,
    SecretProviderRegistry,
)
from netbox_config_backup.drivers.base import CollectedArtifact, DriverContext, DriverError
from netbox_config_backup.drivers.registry import DriverRegistry, DriverRegistryError
from netbox_config_backup.storage.base import ConfigStorage, StorageError

from .hashing import sha256_hex
from .repository import BackupRepository, StoredArtifactRecord


@dataclass(frozen=True, slots=True)
class BackupResult:
    run_id: Any
    status: str
    changed: bool
    raw_changed: bool
    revision_id: Any | None = None
    error_code: str = ""


class BackupPipeline:
    def __init__(
        self,
        *,
        repository: BackupRepository,
        drivers: DriverRegistry,
        storage: ConfigStorage,
        secret_providers: SecretProviderRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
        error_message_max_length: int = 1000,
    ) -> None:
        self.repository = repository
        self.drivers = drivers
        self.storage = storage
        self.secret_providers = secret_providers or SecretProviderRegistry()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.uuid_factory = uuid_factory
        self.error_message_max_length = error_message_max_length

    def execute(self, run_id: Any) -> BackupResult:
        stored_keys: list[str] = []
        try:
            context = self.repository.get_execution_context(run_id)
            self.repository.mark_running(run_id, started_at=self.clock())
            driver = self.drivers.create(context.driver_id)

            credentials = None
            if context.secret_provider_id or context.secret_reference:
                if not context.secret_provider_id or not context.secret_reference:
                    raise PipelineOperationError(
                        "NO_CREDENTIAL_PROFILE",
                        "Credential provider and reference must both be configured.",
                    )
                provider = self.secret_providers.get(context.secret_provider_id)
                credentials = provider.resolve(context.secret_reference)

            receiver = context.receiver
            if receiver is not None:
                if not context.receiver_secret_provider_id or not context.receiver_secret_reference:
                    raise PipelineOperationError(
                        "NO_RECEIVER_CREDENTIALS",
                        "The device upload receiver has no complete credential profile.",
                    )
                receiver_provider = self.secret_providers.get(context.receiver_secret_provider_id)
                receiver = replace(
                    receiver,
                    credentials=receiver_provider.resolve(context.receiver_secret_reference),
                )

            artifacts = driver.collect(
                DriverContext(
                    device_id=context.device_id,
                    device_name=context.device_name,
                    address=context.address,
                    credentials=credentials,
                    connection=context.connection,
                    receiver=receiver,
                    options=context.driver_options,
                )
            )
            prepared = self._prepare_artifacts(driver, artifacts)
            primary = next(item for item in prepared if item.is_primary)

            latest = self.repository.get_latest_revision(context.target_id)
            content_changed = latest is None or latest.normalized_hash != primary.normalized_hash
            raw_changed = latest is None or latest.primary_raw_hash != primary.raw_hash

            if not content_changed and context.store_mode == "changed_only":
                revision_id = self.repository.commit_unchanged(
                    run_id,
                    raw_changed=raw_changed,
                    finished_at=self.clock(),
                )
                return BackupResult(
                    run_id=run_id,
                    status="success_unchanged",
                    changed=False,
                    raw_changed=raw_changed,
                    revision_id=revision_id,
                )

            revision_uuid = self.uuid_factory()
            stored_records: list[StoredArtifactRecord] = []
            for item in prepared:
                key = (
                    f"devices/{context.device_id}/revisions/"
                    f"{revision_uuid}/{item.artifact.filename}"
                )
                stored = self.storage.put(
                    key,
                    item.artifact.content,
                    metadata={
                        "artifact_type": item.artifact.artifact_type,
                        "driver_id": context.driver_id,
                        "raw_hash": item.raw_hash,
                    },
                )
                stored_keys.append(key)
                stored_records.append(
                    StoredArtifactRecord(
                        artifact_type=item.artifact.artifact_type,
                        format=item.artifact.format,
                        storage_key=key,
                        size=stored.size,
                        raw_hash=item.raw_hash,
                        normalized_hash=item.normalized_hash,
                        is_primary=item.is_primary,
                    )
                )

            revision_id = self.repository.commit_revision(
                run_id,
                revision_uuid=revision_uuid,
                normalized_hash=primary.normalized_hash,
                normalizer_version=driver.normalizer_version,
                driver_id=context.driver_id,
                content_changed=content_changed,
                raw_changed=raw_changed,
                artifacts=stored_records,
                finished_at=self.clock(),
            )
            return BackupResult(
                run_id=run_id,
                status="success_changed" if content_changed else "success_unchanged",
                changed=content_changed,
                raw_changed=raw_changed,
                revision_id=revision_id,
            )
        except (
            DriverError,
            DriverRegistryError,
            SecretProviderError,
            SecretProviderLookupError,
            StorageError,
            PipelineOperationError,
        ) as exc:
            self._rollback_storage(stored_keys)
            error_code, safe_message = self._expected_error(exc)
            self.repository.mark_failed(
                run_id,
                status="failed",
                error_code=error_code,
                error_message=safe_message[: self.error_message_max_length],
                finished_at=self.clock(),
            )
            return BackupResult(
                run_id=run_id,
                status="failed",
                changed=False,
                raw_changed=False,
                error_code=error_code,
            )
        except Exception:
            self._rollback_storage(stored_keys)
            self.repository.mark_failed(
                run_id,
                status="errored",
                error_code="INTERNAL_ERROR",
                error_message="Unexpected internal backup error.",
                finished_at=self.clock(),
            )
            raise

    def _prepare_artifacts(self, driver: Any, artifacts: list[CollectedArtifact]):
        if not artifacts:
            raise PipelineOperationError("EMPTY_CONFIG", "Driver returned no artifacts.")
        if len({artifact.artifact_type for artifact in artifacts}) != len(artifacts):
            raise PipelineOperationError(
                "VALIDATION_FAILED", "Driver returned duplicate artifact types."
            )
        if sum(artifact.is_primary for artifact in artifacts) != 1:
            raise PipelineOperationError(
                "VALIDATION_FAILED", "Exactly one primary artifact is required."
            )

        prepared = []
        for artifact in artifacts:
            validation = driver.validate(artifact)
            if not validation.valid:
                raise PipelineOperationError(validation.error_code, validation.safe_message)
            try:
                normalized = driver.normalize(artifact)
            except (UnicodeError, ValueError) as exc:
                raise PipelineOperationError(
                    "NORMALIZATION_FAILED", "Artifact normalization failed."
                ) from exc
            if not isinstance(normalized, bytes) or not normalized:
                raise PipelineOperationError(
                    "NORMALIZATION_FAILED", "Artifact normalization returned no data."
                )
            prepared.append(
                PreparedArtifact(
                    artifact=artifact,
                    raw_hash=sha256_hex(artifact.content),
                    normalized_hash=sha256_hex(normalized),
                    is_primary=artifact.is_primary,
                )
            )
        return prepared

    def _rollback_storage(self, stored_keys: list[str]) -> None:
        for key in reversed(stored_keys):
            try:
                self.storage.delete(key)
            except StorageError:
                # A later housekeeping stage will reconcile rare rollback orphans.
                pass

    @staticmethod
    def _expected_error(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, (DriverError, PipelineOperationError)):
            return exc.error_code, exc.safe_message
        if isinstance(exc, DriverRegistryError):
            return "UNSUPPORTED_PLATFORM", str(exc)
        if isinstance(exc, (SecretProviderError, SecretProviderLookupError)):
            return "SECRET_RESOLUTION_FAILED", "Credential resolution failed."
        if isinstance(exc, StorageError):
            return "STORAGE_FAILED", exc.safe_message
        return "INTERNAL_ERROR", "Unexpected internal backup error."


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    artifact: CollectedArtifact
    raw_hash: str
    normalized_hash: str
    is_primary: bool


class PipelineOperationError(RuntimeError):
    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message
