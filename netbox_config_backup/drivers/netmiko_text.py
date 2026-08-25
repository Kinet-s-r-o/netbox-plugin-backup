from __future__ import annotations

import re
from re import Pattern
from typing import Any

from netbox_config_backup.transports import NetmikoTransport

from .base import (
    BackupDriver,
    CollectedArtifact,
    DriverContext,
    DriverError,
    ValidationResult,
)


class NetmikoTextConfigDriver(BackupDriver):
    """Declarative base for one-command, read-only Netmiko configuration drivers."""

    capabilities = frozenset({"running_config"})
    normalizer_version = "1"

    vendor_name = "Network device"
    netmiko_device_type: str
    command: str
    filename = "running-config.cfg"
    artifact_format = "network_config"
    source = "running_configuration"
    supports_enable = False

    default_max_output_bytes = 10 * 1024 * 1024
    absolute_max_output_bytes = 50 * 1024 * 1024

    validation_patterns: tuple[Pattern[str], ...] = ()
    volatile_line_patterns: tuple[Pattern[bytes], ...] = ()
    command_error_pattern = re.compile(
        r"^\s*(?:%\s*)?(?:invalid input|authorization failed|ambiguous command|"
        r"incomplete command|access denied|permission denied|privilege level too low|"
        r"unrecognized command|unknown command|command not found|error\s*:)",
        re.IGNORECASE | re.MULTILINE,
    )

    _secret_assignment = re.compile(
        r"(?i)((?<![\w-])(?:password|secret|community|key-string|authentication-key)\s+"
        r"(?:(?:0|4|5|6|7|8|9)\s+)?)(\"[^\"]*\"|'[^']*'|\S+)"
    )
    _isakmp_key = re.compile(r"(?i)(\bcrypto\s+isakmp\s+key\s+)(\S+)")
    _server_key = re.compile(
        r"(?i)(\b(?:tacacs-server|radius-server)\s+key\s+"
        r"(?:(?:0|6|7)\s+)?)(\S+)"
    )
    _pre_shared_key = re.compile(r"(?i)(\bpre-shared-key\b.*\bkey\s+)(\S+)")

    def __init__(self, transport: Any | None = None) -> None:
        self.transport = transport or NetmikoTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        max_output_bytes = self._max_output_bytes(context.options)
        with self.transport.open(
            device_type=self.netmiko_device_type,
            context=context,
        ) as session:
            if self.supports_enable and context.credentials and context.credentials.enable_secret:
                session.enable()
            output = session.send_command(
                self.command,
                strip_command=True,
                strip_prompt=True,
            )

        if not isinstance(output, str):
            raise DriverError(
                "INVALID_OUTPUT",
                f"{self.vendor_name} returned an invalid output format.",
            )
        try:
            content = output.encode("utf-8")
        except UnicodeError as exc:
            raise DriverError(
                "INVALID_OUTPUT",
                f"{self.vendor_name} output could not be encoded.",
            ) from exc
        if len(content) > max_output_bytes:
            raise DriverError(
                "CONFIG_TOO_LARGE",
                f"{self.vendor_name} configuration exceeds the configured maximum size.",
            )

        return [
            CollectedArtifact(
                artifact_type="running_config",
                filename=self.filename,
                content=content,
                format=self.artifact_format,
                is_primary=True,
                metadata={
                    "source": self.source,
                    "sensitive": "redacted_for_display",
                },
            )
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        content = artifact.content.strip()
        if not content:
            return ValidationResult(
                valid=False,
                error_code="EMPTY_CONFIG",
                safe_message=f"{self.vendor_name} returned an empty configuration.",
            )
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            return ValidationResult(
                valid=False,
                error_code="INVALID_OUTPUT",
                safe_message=f"{self.vendor_name} returned invalid text output.",
            )
        if self.command_error_pattern.search(text):
            return ValidationResult(
                valid=False,
                error_code="COMMAND_REJECTED",
                safe_message=f"{self.vendor_name} rejected the configuration command.",
            )
        if not self.validation_patterns or not any(
            pattern.search(text) for pattern in self.validation_patterns
        ):
            return ValidationResult(
                valid=False,
                error_code="INCOMPLETE_CONFIG",
                safe_message=(
                    f"{self.vendor_name} output does not contain a complete running configuration."
                ),
            )
        return ValidationResult(valid=True)

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        normalized_lines = []
        lines = artifact.content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        for line in lines.split(b"\n"):
            line = line.rstrip()
            if any(pattern.fullmatch(line) for pattern in self.volatile_line_patterns):
                continue
            normalized_lines.append(line)
        return b"\n".join(normalized_lines).strip() + b"\n"

    def redact_for_display(self, text: str) -> str:
        redacted = self._secret_assignment.sub(r"\1<redacted>", text)
        redacted = self._isakmp_key.sub(r"\1<redacted>", redacted)
        redacted = self._server_key.sub(r"\1<redacted>", redacted)
        return self._pre_shared_key.sub(r"\1<redacted>", redacted)

    def _max_output_bytes(self, options) -> int:
        if set(options) - {"max_output_bytes"}:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                f"{self.vendor_name} driver options contain an unsupported setting.",
            )
        value = options.get("max_output_bytes", self.default_max_output_bytes)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                "max_output_bytes must be a positive integer.",
            )
        if value > self.absolute_max_output_bytes:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                "max_output_bytes exceeds the allowed safety limit.",
            )
        return value
