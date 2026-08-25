from __future__ import annotations

import hashlib
import json
import re
from typing import ClassVar

from netbox_config_backup.transports import (
    CeragonCeraOSTransport,
    SiaeAlfoplusWebLctTransport,
    SshArtifactTransport,
)

from .archive_safety import ArchiveExtractionError, extract_zip_member, validate_archive
from .base import BackupDriver, CollectedArtifact, DriverContext, DriverError, ValidationResult


class SftpNativeBackupDriver(BackupDriver):
    """Model profile for vendor-native backups exposed through SFTP.

    A firmware-specific export command may be configured by an administrator. It is
    deliberately never guessed by the plugin. With no command, the driver only reads
    an already generated file.
    """

    capabilities = frozenset({"native_backup", "sftp", "optional_ssh_export"})
    normalizer_version = "1"
    vendor_name = "Network device"
    native_filename = "configuration.backup"
    native_format = "vendor_native_backup"
    archive_kind: str | None = None
    default_remote_path = ""

    def __init__(self, transport=None) -> None:
        self.transport = transport or SshArtifactTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        options = self._options(context.options)
        result = self.transport.collect(
            context,
            remote_path=options["remote_path"],
            export_command=options["export_command"],
            max_bytes=options["max_output_bytes"],
        )
        return self._artifacts_from_content(result.content, source="sftp")

    def _artifacts_from_content(self, content: bytes, *, source: str) -> list[CollectedArtifact]:
        digest = hashlib.sha256(content).hexdigest()
        manifest = (
            json.dumps(
                {
                    "format": self.native_format,
                    "native_filename": self.native_filename,
                    "sha256": digest,
                    "size": len(content),
                    "vendor": self.vendor_name,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        return [
            CollectedArtifact(
                artifact_type="backup_manifest",
                filename="configuration.json",
                content=manifest,
                format="native_backup_manifest",
                is_primary=True,
                metadata={"source": source},
            ),
            CollectedArtifact(
                artifact_type="native_backup",
                filename=self.native_filename,
                content=content,
                format=self.native_format,
                metadata={"source": source, "sensitive": "contains_secrets"},
            ),
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        if artifact.artifact_type == "backup_manifest":
            try:
                value = json.loads(artifact.content)
            except (UnicodeError, json.JSONDecodeError):
                return ValidationResult(
                    False,
                    "INVALID_OUTPUT",
                    f"{self.vendor_name} backup manifest is invalid.",
                )
            complete = bool(value.get("sha256")) and value.get("size", 0) > 0
            return ValidationResult(
                complete,
                "INVALID_OUTPUT",
                f"{self.vendor_name} backup manifest is incomplete.",
            )
        if artifact.artifact_type == "native_backup":
            if not artifact.content:
                return ValidationResult(
                    False,
                    "EMPTY_CONFIG",
                    f"{self.vendor_name} backup file is empty.",
                )
            if self.archive_kind:
                check = validate_archive(artifact.content, kind=self.archive_kind)
                return ValidationResult(check.valid, "INVALID_ARCHIVE", check.message)
            return ValidationResult(True)
        return ValidationResult(
            False,
            "INVALID_OUTPUT",
            f"{self.vendor_name} returned an unknown artifact type.",
        )

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        return artifact.content

    def _options(self, options):
        allowed = {
            "remote_path",
            "export_command",
            "allow_export_command",
            "max_output_bytes",
        }
        if set(options) - allowed:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                f"{self.vendor_name} driver options contain an unsupported setting.",
            )
        remote_path = options.get("remote_path", self.default_remote_path)
        export_command = options.get("export_command", "")
        allow_command = options.get("allow_export_command", False)
        max_output_bytes = options.get("max_output_bytes", 50 * 1024 * 1024)
        if not isinstance(remote_path, str) or not remote_path:
            raise DriverError(
                "DRIVER_SETUP_REQUIRED",
                f"Configure the remote_path option for the {self.vendor_name} driver.",
            )
        if not isinstance(export_command, str) or not isinstance(allow_command, bool):
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                f"{self.vendor_name} export options are invalid.",
            )
        if export_command and not allow_command:
            raise DriverError(
                "EXPORT_COMMAND_NOT_CONFIRMED",
                "Set allow_export_command to true after verifying the vendor export command.",
            )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes <= 0
            or max_output_bytes > 100 * 1024 * 1024
        ):
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                f"{self.vendor_name} maximum backup size is invalid.",
            )
        return {
            "remote_path": remote_path,
            "export_command": export_command,
            "max_output_bytes": max_output_bytes,
        }


class CeragonIP20Driver(SftpNativeBackupDriver):
    driver_id = "ceragon_ip20"
    display_name = "Ceragon IP-20 (SFTP native backup)"
    vendor_name = "Ceragon IP-20"
    native_filename = "ceragon-ip20-backup.zip"
    native_format = "ceragon_ip20_zip"
    archive_kind = "zip"


class CeragonIP50Driver(SftpNativeBackupDriver):
    driver_id = "ceragon_ip50"
    display_name = "Ceragon IP-50 / CeraOS (SFTP native backup)"
    vendor_name = "Ceragon IP-50"
    native_filename = "ceragon-ip50-backup.zip"
    native_format = "ceragon_ip50_zip"
    archive_kind = "zip"
    normalizer_version = "2"

    configuration_member = "config_dump.txt"
    configuration_filename = "config_dump.txt"
    configuration_format = "ceragon_ceraos_config_dump"
    max_configuration_bytes = 25 * 1024 * 1024

    _sensitive_assignment = re.compile(
        r"(?i)^(?P<key>[^=]*(?:password|passphrase|secret|community|"
        r"private[-_ ]?key|pre[-_ ]?shared|credential|token)[^=]*)=(?P<value>.*)$"
    )
    _volatile_assignment_prefixes = (
        b"creation_date_time",
        b"-signature",
        b"configuration-table-file-transfer-config.",
        b"configuration-table-file-transfer-log-status.",
    )
    _volatile_table_names: ClassVar[set[bytes]] = {
        b"configuration-table-file-transfer-config",
        b"configuration-table-file-transfer-log-status",
    }

    capabilities = frozenset({"native_backup", "sftp_push", "device_export", "reverse_ssh_tunnel"})

    def __init__(self, transport=None) -> None:
        self.transport = transport or CeragonCeraOSTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        result = self.transport.collect(context, options=dict(context.options))
        # SshArtifactResult compatibility keeps third-party/test transports simple.
        content = result.content if hasattr(result, "content") else result
        try:
            configuration = self.extract_configuration(content)
        except ArchiveExtractionError as exc:
            raise DriverError("INVALID_ARCHIVE", str(exc)) from exc

        artifacts = self._artifacts_from_content(
            content,
            source="plugin_sftp_receiver",
        )
        artifacts[0] = CollectedArtifact(
            artifact_type=artifacts[0].artifact_type,
            filename=artifacts[0].filename,
            content=artifacts[0].content,
            format=artifacts[0].format,
            is_primary=False,
            metadata=artifacts[0].metadata,
        )
        return [
            CollectedArtifact(
                artifact_type="configuration_dump",
                filename=self.configuration_filename,
                content=configuration,
                format=self.configuration_format,
                is_primary=True,
                metadata={
                    "source": f"native_zip:{self.configuration_member}",
                    "sensitive": "redacted_for_display",
                },
            ),
            *artifacts,
        ]

    @classmethod
    def extract_configuration(cls, content: bytes) -> bytes:
        return extract_zip_member(
            content,
            member_name=cls.configuration_member,
            max_bytes=cls.max_configuration_bytes,
        )

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        if artifact.artifact_type == "configuration_dump":
            if not artifact.content.strip():
                return ValidationResult(
                    False,
                    "EMPTY_CONFIG",
                    "Ceragon IP-50 configuration dump is empty.",
                )
            try:
                text = artifact.content.decode("utf-8")
            except UnicodeError:
                return ValidationResult(
                    False,
                    "INVALID_OUTPUT",
                    "Ceragon IP-50 configuration dump is not valid UTF-8 text.",
                )
            if "\x00" in text or "header_ver" not in text:
                return ValidationResult(
                    False,
                    "INCOMPLETE_CONFIG",
                    "Ceragon IP-50 configuration dump is incomplete.",
                )
            return ValidationResult(True)
        return super().validate(artifact)

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        if artifact.artifact_type != "configuration_dump":
            return artifact.content

        normalized: list[bytes] = []
        skip_table_rows = False
        for raw_line in artifact.content.splitlines():
            line = raw_line.rstrip()
            lowered = line.lower()
            if lowered.startswith(self._volatile_assignment_prefixes):
                continue
            if line in self._volatile_table_names:
                skip_table_rows = True
                continue
            if skip_table_rows:
                if line == b"%%%":
                    skip_table_rows = False
                continue
            normalized.append(line)
        return b"\n".join(normalized).strip() + b"\n"

    def redact_for_display(self, text: str) -> str:
        redacted = []
        for line in text.splitlines():
            match = self._sensitive_assignment.match(line)
            if match:
                redacted.append(f"{match.group('key')}=<redacted>")
                continue
            if "|" in line and re.search(r"(?i)\|(?:s?ftp|scp)\|", line):
                fields = line.split("|")
                if len(fields) >= 4:
                    fields[2] = "<redacted>"
                    fields[3] = "<redacted>"
                    line = "|".join(fields)
            redacted.append(line)
        return "\n".join(redacted)


class SiaeALFOplusDriver(SftpNativeBackupDriver):
    driver_id = "siae_alfoplus"
    display_name = "SIAE ALFOplus (WebLCT native backup)"
    user_selectable = False
    vendor_name = "SIAE ALFOplus"
    native_filename = "siae-alfoplus-backup.bak"
    native_format = "siae_alfoplus_bak"
    capabilities = frozenset({"native_backup", "weblct", "legacy_ftp", "device_export"})

    def __init__(self, transport=None) -> None:
        self.transport = transport or SiaeAlfoplusWebLctTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        content = self.transport.collect(context, options=dict(context.options))
        return self._artifacts_from_content(content, source="legacy_weblct_ftp_receiver")


class SiaeALFOplus2Driver(SftpNativeBackupDriver):
    driver_id = "siae_alfoplus2"
    display_name = "SIAE ALFOplus2 (SFTP native backup)"
    user_selectable = False
    vendor_name = "SIAE ALFOplus2"
    native_filename = "siae-alfoplus2-backup.bku"
    native_format = "siae_alfoplus2_bku"


class SiaeALFOplus80HDDriver(SftpNativeBackupDriver):
    driver_id = "siae_alfoplus80hd"
    display_name = "SIAE ALFOplus80HD (SFTP native backup)"
    user_selectable = False
    vendor_name = "SIAE ALFOplus80HD"
    native_filename = "siae-alfoplus80hd-backup.bak"
    native_format = "siae_alfoplus80hd_bak"


class SiaeAGS20Driver(SftpNativeBackupDriver):
    driver_id = "siae_ags20"
    display_name = "SIAE AGS-20 (SFTP native backup)"
    user_selectable = False
    vendor_name = "SIAE AGS-20"
    native_filename = "siae-ags20-backup.bak"
    native_format = "siae_ags20_bak"


CERAGON_SIAE_DRIVERS = (
    CeragonIP20Driver,
    CeragonIP50Driver,
    SiaeALFOplusDriver,
    SiaeALFOplus2Driver,
    SiaeALFOplus80HDDriver,
    SiaeAGS20Driver,
)
