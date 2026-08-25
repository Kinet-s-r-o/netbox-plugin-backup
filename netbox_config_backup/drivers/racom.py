from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
from types import MappingProxyType
from urllib.parse import quote

from netbox_config_backup.transports import HttpJsonTransport, RacomRaySshArtifactTransport

from .archive_safety import ArchiveExtractionError, extract_tgz_members, validate_archive
from .base import BackupDriver, CollectedArtifact, DriverContext, DriverError, ValidationResult


class RacomRipEX2Driver(BackupDriver):
    driver_id = "racom_ripex2"
    display_name = "RACOM RipEX2 (HTTPS API)"
    capabilities = frozenset({"native_backup", "structured_config", "https_api"})
    normalizer_version = "1"

    def __init__(self, transport=None) -> None:
        self.transport = transport or HttpJsonTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        options = self._options(context.options)
        if not context.address:
            raise DriverError("NO_ADDRESS", "The device has no usable management address.")
        if context.credentials is None or not context.credentials.password:
            raise DriverError("NO_CREDENTIALS", "RipEX2 username and password are required.")
        host = self._url_host(context.address)
        base_url = f"https://{host}:{context.connection.port}/cgi-bin"
        request = {
            "timeout": context.connection.command_timeout,
            "verify_tls": options["verify_tls"],
            "ca_bundle_path": options["ca_bundle_path"],
            "max_response_bytes": options["max_response_bytes"],
        }
        token = ""
        try:
            login = self.transport.post_json(
                f"{base_url}/login.cgi",
                {
                    "username": context.credentials.username,
                    "password": context.credentials.password,
                    "language_code": "en",
                },
                **request,
            )
            if not isinstance(login, dict) or not isinstance(login.get("token"), str):
                raise DriverError("AUTH_FAILED", "RipEX2 login returned no session token.")
            token = login["token"]
            headers = {"apikey": token}
            settings_response = self.transport.post_json(
                f"{base_url}/rpc.cgi",
                {"method": "settings_get"},
                headers=headers,
                **request,
            )
            package_response = self.transport.post_json(
                f"{base_url}/rpc.cgi",
                {"method": "settings_package_get"},
                headers=headers,
                **request,
            )
        finally:
            if token:
                try:
                    self.transport.post_json(
                        f"{base_url}/logout.cgi",
                        {},
                        headers={"apikey": token},
                        **request,
                    )
                except DriverError:
                    pass

        settings = self._rpc_result(settings_response, "settings_get")
        package = self._rpc_result(package_response, "settings_package_get")
        encoded = package.get("base64") if isinstance(package, dict) else package
        if not isinstance(encoded, str):
            raise DriverError("INVALID_OUTPUT", "RipEX2 returned no configuration package.")
        try:
            archive = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DriverError(
                "INVALID_OUTPUT", "RipEX2 returned an invalid backup package."
            ) from exc

        config_json = (
            json.dumps(
                settings,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        return [
            CollectedArtifact(
                artifact_type="structured_config",
                filename="configuration.json",
                content=config_json,
                format="racom_ripex2_json",
                is_primary=True,
                metadata={"source": "settings_get", "sensitive": "contains_secrets"},
            ),
            CollectedArtifact(
                artifact_type="native_backup",
                filename="configuration.zip",
                content=archive,
                format="racom_ripex2_zip",
                metadata={"source": "settings_package_get", "sensitive": "contains_secrets"},
            ),
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        if artifact.artifact_type == "structured_config":
            try:
                value = json.loads(artifact.content)
            except (UnicodeError, json.JSONDecodeError):
                return ValidationResult(
                    False, "INVALID_OUTPUT", "RipEX2 configuration JSON is invalid."
                )
            if not isinstance(value, dict) or "config_data" not in value:
                return ValidationResult(
                    False, "INCOMPLETE_CONFIG", "RipEX2 configuration is incomplete."
                )
            return ValidationResult(True)
        if artifact.artifact_type == "native_backup":
            check = validate_archive(artifact.content, kind="zip")
            return ValidationResult(check.valid, "INVALID_ARCHIVE", check.message)
        return ValidationResult(
            False, "INVALID_OUTPUT", "RipEX2 returned an unknown artifact type."
        )

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        if artifact.artifact_type == "structured_config":
            value = json.loads(artifact.content)
            return (
                json.dumps(
                    value["config_data"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        return artifact.content

    def redact_for_display(self, text: str) -> str:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return "Configuration cannot be displayed safely."
        return json.dumps(self._redact(value), ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            return {
                key: (
                    "<redacted>"
                    if any(
                        marker in key.lower()
                        for marker in ("password", "secret", "key", "community")
                    )
                    else cls._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

    @staticmethod
    def _rpc_result(response, method: str):
        if not isinstance(response, dict):
            raise DriverError("INVALID_OUTPUT", f"RipEX2 {method} returned an invalid response.")
        if "error" in response:
            raise DriverError("API_REJECTED", f"RipEX2 rejected the {method} request.")
        if "result" not in response:
            raise DriverError("INVALID_OUTPUT", f"RipEX2 {method} returned no result.")
        return response["result"]

    @staticmethod
    def _url_host(address: str) -> str:
        if any(character in address for character in "/?#@"):
            raise DriverError("INVALID_ADDRESS", "The RipEX2 management address is invalid.")
        try:
            parsed = ipaddress.ip_address(address)
            return f"[{parsed.compressed}]" if parsed.version == 6 else parsed.compressed
        except ValueError:
            if not address or len(address) > 253 or ":" in address:
                raise DriverError("INVALID_ADDRESS", "The RipEX2 management address is invalid.")
            return quote(address, safe=".-")

    @staticmethod
    def _options(options):
        allowed = {"verify_tls", "ca_bundle_path", "max_response_bytes"}
        if set(options) - allowed:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS", "RipEX2 driver options contain an unsupported setting."
            )
        verify_tls = options.get("verify_tls", True)
        ca_bundle_path = options.get("ca_bundle_path", "")
        max_response_bytes = options.get("max_response_bytes", 20 * 1024 * 1024)
        if not isinstance(verify_tls, bool) or not isinstance(ca_bundle_path, str):
            raise DriverError("INVALID_DRIVER_OPTIONS", "RipEX2 TLS options are invalid.")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
            raise DriverError("INVALID_DRIVER_OPTIONS", "RipEX2 response size limit is invalid.")
        return MappingProxyType(
            {
                "verify_tls": verify_tls,
                "ca_bundle_path": ca_bundle_path,
                "max_response_bytes": max_response_bytes,
            }
        )


class _RacomRayDriver(BackupDriver):
    capabilities = frozenset({"native_backup", "ssh_export", "scp"})
    export_command = ". /etc/profile >/dev/null 2>&1; cli_cnf_backup_get"
    filename = "cnf_backup.tgz"
    normalizer_version = "2"

    def __init__(self, transport=None) -> None:
        # Compatibility with old DSA host keys is kept inside the RAy driver.
        # Connection profiles remain vendor-neutral.
        self.transport = transport or RacomRaySshArtifactTransport()

    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        options = self._options(context.options)
        result = self.transport.collect(
            context,
            remote_path=options["remote_path"],
            export_command=self.export_command,
            max_bytes=options["max_output_bytes"],
        )
        digest = hashlib.sha256(result.content).hexdigest()
        configuration = self.extract_configuration(result.content)
        metadata = (
            json.dumps(
                {"filename": self.filename, "sha256": digest},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        return [
            CollectedArtifact(
                artifact_type="configuration_dump",
                filename="configuration.json",
                content=configuration,
                format="racom_ray_json",
                is_primary=True,
                metadata={"source": self.filename, "sensitive": "redacted_in_preview"},
            ),
            CollectedArtifact(
                artifact_type="backup_manifest",
                filename="backup_manifest.json",
                content=metadata,
                format="racom_ray_manifest",
                metadata={"source": self.export_command},
            ),
            CollectedArtifact(
                artifact_type="native_backup",
                filename=self.filename,
                content=result.content,
                format="racom_ray_tgz",
                metadata={"source": "scp", "sensitive": "contains_secrets"},
            ),
        ]

    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        if artifact.artifact_type == "configuration_dump":
            try:
                value = json.loads(artifact.content)
            except (UnicodeError, json.JSONDecodeError):
                return ValidationResult(
                    False, "INVALID_OUTPUT", "RAy configuration JSON is invalid."
                )
            if (
                not isinstance(value, dict)
                or set(value) != {"L", "U"}
                or not all(isinstance(item, dict) for item in value.values())
            ):
                return ValidationResult(
                    False,
                    "INCOMPLETE_CONFIG",
                    "RAy configuration does not contain both link units.",
                )
            return ValidationResult(True)
        if artifact.artifact_type == "backup_manifest":
            try:
                value = json.loads(artifact.content)
            except (UnicodeError, json.JSONDecodeError):
                return ValidationResult(False, "INVALID_OUTPUT", "RAy backup manifest is invalid.")
            return ValidationResult(
                bool(value.get("sha256")), "INVALID_OUTPUT", "RAy backup manifest is incomplete."
            )
        if artifact.artifact_type == "native_backup":
            check = validate_archive(artifact.content, kind="tgz")
            return ValidationResult(check.valid, "INVALID_ARCHIVE", check.message)
        return ValidationResult(False, "INVALID_OUTPUT", "RAy returned an unknown artifact type.")

    def normalize(self, artifact: CollectedArtifact) -> bytes:
        if artifact.artifact_type == "configuration_dump":
            value = json.loads(artifact.content)
            return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        return artifact.content

    def extract_configuration(self, native_content: bytes) -> bytes:
        try:
            members = extract_tgz_members(
                native_content,
                member_names=("L.conf", "U.conf"),
            )
            configuration = {
                unit: self._parse_unit_configuration(members[f"{unit}.conf"]) for unit in ("L", "U")
            }
        except (ArchiveExtractionError, UnicodeError, json.JSONDecodeError) as exc:
            raise DriverError(
                "INVALID_ARCHIVE",
                "RAy unit configurations could not be extracted safely.",
            ) from exc
        return (
            json.dumps(configuration, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
        )

    def redact_for_display(self, text: str) -> str:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return "Configuration cannot be displayed safely."
        return json.dumps(self._redact(value), ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            return {
                key: (
                    "<redacted>"
                    if any(
                        marker in key.lower()
                        for marker in (
                            "password",
                            "passwd",
                            "secret",
                            "community",
                            "private",
                            "preshared",
                            "psk",
                            "authkey",
                            "auth_key",
                        )
                    )
                    else cls._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

    @staticmethod
    def _parse_unit_configuration(content: bytes):
        text = content.decode("utf-8")
        stripped = text.strip()
        if not stripped.startswith("{"):
            return _RacomRayDriver._parse_legacy_assignments(text)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            # Current RAy backup files omit exactly the final outer-object brace.
            # Repair only that documented shape for the derived browser artifact;
            # the native backup remains stored byte-for-byte.
            stripped = text.rstrip()
            if exc.pos < len(stripped) or not stripped.endswith("}"):
                raise
            value = json.loads(f"{stripped}\n}}")
        if not isinstance(value, dict):
            raise json.JSONDecodeError("RAy unit configuration is not an object", text, 0)
        return value

    @staticmethod
    def _parse_legacy_assignments(text: str) -> dict[str, str]:
        """Parse the bounded KEY=VALUE format emitted by older RAy firmware."""
        configuration = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise json.JSONDecodeError("Invalid legacy RAy configuration line", text, 0)
            key, value = stripped.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) or key in configuration:
                raise json.JSONDecodeError("Invalid legacy RAy configuration key", text, 0)
            configuration[key] = value.strip()
        if not configuration:
            raise json.JSONDecodeError("Legacy RAy configuration is empty", text, 0)
        return configuration

    @classmethod
    def _options(cls, options):
        if set(options) - {"remote_path", "max_output_bytes"}:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS", "RAy driver options contain an unsupported setting."
            )
        remote_path = options.get("remote_path", cls.filename)
        max_output_bytes = options.get("max_output_bytes", 50 * 1024 * 1024)
        if (
            not isinstance(remote_path, str)
            or isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes <= 0
            or max_output_bytes > 100 * 1024 * 1024
        ):
            raise DriverError("INVALID_DRIVER_OPTIONS", "RAy driver options are invalid.")
        return {"remote_path": remote_path, "max_output_bytes": max_output_bytes}


class RacomRAy2Driver(_RacomRayDriver):
    driver_id = "racom_ray2"
    display_name = "RACOM RAy2 (SSH/SCP native backup)"


class RacomRAy3Driver(_RacomRayDriver):
    driver_id = "racom_ray3"
    display_name = "RACOM RAy3 (SSH/SCP native backup)"


RACOM_DRIVERS = (RacomRipEX2Driver, RacomRAy2Driver, RacomRAy3Driver)
