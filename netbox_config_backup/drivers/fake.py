from __future__ import annotations

from .base import (
    BackupDriver,
    CollectedArtifact,
    DriverContext,
    DriverError,
    ValidationResult,
)


class FakeDriver(BackupDriver):
    """Deterministic device simulator used to exercise the complete core flow."""

    driver_id = "fake"
    display_name = "Fake device"
    capabilities = frozenset({"running_config"})
    normalizer_version = "1"

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        failure_code = context.options.get("failure_code")
        if failure_code:
            raise DriverError(
                str(failure_code),
                str(context.options.get("failure_message", "Simulated driver failure.")),
            )

        configured = context.options.get("config")
        if configured is None:
            configured = (
                f"hostname {context.device_name}\n"
                "interface Loopback0\n"
                " description managed by netbox_config_backup\n"
            )
        if isinstance(configured, str):
            content = configured.encode("utf-8")
        elif isinstance(configured, bytes):
            content = configured
        else:
            raise DriverError("VALIDATION_FAILED", "Fake config must be text or bytes.")

        return [
            CollectedArtifact(
                artifact_type="running_config",
                filename="running-config.txt",
                content=content,
                is_primary=True,
                metadata={"simulated": "true"},
            )
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        if not artifact.content.strip():
            return ValidationResult(
                valid=False,
                error_code="EMPTY_CONFIG",
                safe_message="The simulated device returned an empty configuration.",
            )
        return ValidationResult(valid=True)

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        text = artifact.content.decode("utf-8")
        stable_lines = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line.startswith("! Last configuration change at "):
                continue
            stable_lines.append(line.rstrip())
        return ("\n".join(stable_lines).strip() + "\n").encode("utf-8")
