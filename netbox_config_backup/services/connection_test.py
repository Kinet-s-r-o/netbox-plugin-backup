from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.credentials.registry import (
    SecretProviderLookupError,
    SecretProviderRegistry,
)
from netbox_config_backup.drivers.base import DriverContext, DriverError
from netbox_config_backup.drivers.registry import DriverRegistry, DriverRegistryError

from .repository import ExecutionContext


class TargetContextRepository(Protocol):
    def get_target_execution_context(self, target_id: Any) -> ExecutionContext: ...


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    success: bool
    driver_id: str = ""
    artifact_count: int = 0
    total_bytes: int = 0
    error_code: str = ""
    safe_message: str = ""


class ConnectionTester:
    """Exercise credential, transport, collection, validation, and normalization only."""

    def __init__(
        self,
        *,
        repository: TargetContextRepository,
        drivers: DriverRegistry,
        secret_providers: SecretProviderRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.drivers = drivers
        self.secret_providers = secret_providers or SecretProviderRegistry()

    def execute(self, target_id: Any) -> ConnectionTestResult:
        driver_id = ""
        try:
            context = self.repository.get_target_execution_context(target_id)
            driver_id = context.driver_id
            driver = self.drivers.create(driver_id)
            credentials = self._resolve_credentials(context)
            receiver = self._resolve_receiver(context)
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
            self._validate_artifacts(driver, artifacts)
            return ConnectionTestResult(
                success=True,
                driver_id=driver_id,
                artifact_count=len(artifacts),
                total_bytes=sum(len(artifact.content) for artifact in artifacts),
                safe_message="Connection and configuration collection test succeeded.",
            )
        except DriverRegistryError:
            return self._failure(
                driver_id,
                "UNSUPPORTED_PLATFORM",
                "No supported backup driver is configured for this target.",
            )
        except (SecretProviderError, SecretProviderLookupError):
            return self._failure(
                driver_id,
                "SECRET_RESOLUTION_FAILED",
                "Credential resolution failed.",
            )
        except DriverError as exc:
            return self._failure(driver_id, exc.error_code, exc.safe_message)

    def _resolve_credentials(self, context: ExecutionContext):
        if not context.secret_provider_id and not context.secret_reference:
            return None
        if not context.secret_provider_id or not context.secret_reference:
            raise DriverError(
                "NO_CREDENTIAL_PROFILE",
                "Credential provider and reference must both be configured.",
            )
        provider = self.secret_providers.get(context.secret_provider_id)
        return provider.resolve(context.secret_reference)

    def _resolve_receiver(self, context: ExecutionContext):
        if context.receiver is None:
            return None
        if not context.receiver_secret_provider_id or not context.receiver_secret_reference:
            raise DriverError(
                "NO_RECEIVER_CREDENTIALS",
                "The backup receiver has no complete credential profile.",
            )
        provider = self.secret_providers.get(context.receiver_secret_provider_id)
        return replace(
            context.receiver,
            credentials=provider.resolve(context.receiver_secret_reference),
        )

    @staticmethod
    def _validate_artifacts(driver, artifacts) -> None:
        if not artifacts:
            raise DriverError("EMPTY_CONFIG", "Driver returned no artifacts.")
        if len({artifact.artifact_type for artifact in artifacts}) != len(artifacts):
            raise DriverError("VALIDATION_FAILED", "Driver returned duplicate artifact types.")
        if sum(artifact.is_primary for artifact in artifacts) != 1:
            raise DriverError(
                "VALIDATION_FAILED",
                "Exactly one primary artifact is required.",
            )
        for artifact in artifacts:
            validation = driver.validate(artifact)
            if not validation.valid:
                raise DriverError(validation.error_code, validation.safe_message)
            try:
                normalized = driver.normalize(artifact)
            except (UnicodeError, ValueError) as exc:
                raise DriverError(
                    "NORMALIZATION_FAILED",
                    "Artifact normalization failed.",
                ) from exc
            if not isinstance(normalized, bytes) or not normalized:
                raise DriverError(
                    "NORMALIZATION_FAILED",
                    "Artifact normalization returned no data.",
                )

    @staticmethod
    def _failure(
        driver_id: str,
        error_code: str,
        safe_message: str,
    ) -> ConnectionTestResult:
        return ConnectionTestResult(
            success=False,
            driver_id=driver_id,
            error_code=error_code,
            safe_message=safe_message,
        )
