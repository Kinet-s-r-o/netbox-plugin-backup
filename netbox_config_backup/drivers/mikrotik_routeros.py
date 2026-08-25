from __future__ import annotations

import re
from typing import Any

from netbox_config_backup.transports import NetmikoTransport

from .base import (
    BackupDriver,
    CollectedArtifact,
    DriverContext,
    DriverError,
    ValidationResult,
)


class MikroTikRouterOSDriver(BackupDriver):
    """Collect a read-only, secret-hidden RouterOS text export over SSH."""

    driver_id = "mikrotik_routeros"
    display_name = "MikroTik RouterOS (Netmiko)"
    capabilities = frozenset({"running_config"})
    normalizer_version = "1"

    netmiko_device_type = "mikrotik_routeros"
    export_command = "/export terse hide-sensitive"
    default_max_output_bytes = 5 * 1024 * 1024
    absolute_max_output_bytes = 50 * 1024 * 1024

    _volatile_header = re.compile(
        rb"^# (?:[a-z]{3}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}) "
        rb"\d{2}:\d{2}:\d{2} by RouterOS(?P<version> .*)?$",
        re.IGNORECASE,
    )

    def __init__(self, transport: Any | None = None) -> None:
        self.transport = transport or NetmikoTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        max_output_bytes = self._max_output_bytes(context.options)
        with self.transport.open(
            device_type=self.netmiko_device_type,
            context=context,
        ) as session:
            output = session.send_command(
                self.export_command,
                strip_command=True,
                strip_prompt=True,
            )

        if not isinstance(output, str):
            raise DriverError("INVALID_OUTPUT", "RouterOS returned an invalid export format.")
        try:
            content = output.encode("utf-8")
        except UnicodeError as exc:
            raise DriverError("INVALID_OUTPUT", "RouterOS export could not be encoded.") from exc
        if len(content) > max_output_bytes:
            raise DriverError(
                "CONFIG_TOO_LARGE",
                "RouterOS export exceeds the configured maximum size.",
            )

        return [
            CollectedArtifact(
                artifact_type="running_config",
                filename="running-config.rsc",
                content=content,
                format="routeros_script",
                is_primary=True,
                metadata={"source": "routeros_export", "sensitive": "hidden"},
            )
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        content = artifact.content.strip()
        if not content:
            return ValidationResult(
                valid=False,
                error_code="EMPTY_CONFIG",
                safe_message="RouterOS returned an empty export.",
            )

        text = content.decode("utf-8", errors="replace")
        if any(line.lstrip().lower().startswith("#error exporting") for line in text.splitlines()):
            return ValidationResult(
                valid=False,
                error_code="PARTIAL_CONFIG",
                safe_message="RouterOS reported an incomplete configuration export.",
            )
        if not any(line.lstrip().startswith("/") for line in text.splitlines()):
            return ValidationResult(
                valid=False,
                error_code="EMPTY_CONFIG",
                safe_message="RouterOS export contains no configuration commands.",
            )
        return ValidationResult(valid=True)

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        normalized_lines = []
        for line in artifact.content.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
            line = line.rstrip()
            header = self._volatile_header.fullmatch(line)
            if header:
                line = b"# by RouterOS" + (header.group("version") or b"")
            normalized_lines.append(line)
        return b"\n".join(normalized_lines).strip() + b"\n"

    def redact_for_display(self, text: str) -> str:
        # Defense in depth for future content previews. The export command already
        # requests hidden secrets; these patterns cover common RouterOS assignments.
        return re.sub(
            r'(?i)(\b(?:password|secret|private-key|preshared-key)\s*=\s*)("[^"]*"|\S+)',
            r"\1<redacted>",
            text,
        )

    def _max_output_bytes(self, options) -> int:
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
