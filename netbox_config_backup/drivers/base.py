from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from netbox_config_backup.credentials.base import CredentialMaterial


class DriverError(RuntimeError):
    """Expected driver failure with a stable, non-secret error code."""

    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class ConnectionParameters:
    protocol: str = "auto"
    port: int = 22
    connect_timeout: int = 15
    command_timeout: int = 60
    keepalive: int = 30
    verify_host_key: bool = True
    known_hosts_path: str = ""
    trusted_host_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReceiverParameters:
    profile_id: int
    mode: str
    advertised_host: str
    advertised_port: int
    bridge_host: str
    bridge_port: int
    remote_bind_host: str
    remote_bind_port: int
    upload_directory: str
    inbox_path: str
    protocol: str = "sftp"
    export_timeout: int = 180
    max_upload_bytes: int = 100 * 1024 * 1024
    passive_port_start: int = 30000
    passive_port_end: int = 30009
    credentials: CredentialMaterial | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DriverContext:
    device_id: int
    device_name: str
    address: str | None = None
    credentials: CredentialMaterial | None = field(default=None, repr=False)
    connection: ConnectionParameters = field(default_factory=ConnectionParameters)
    receiver: ReceiverParameters | None = None
    options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class CollectedArtifact:
    artifact_type: str
    filename: str
    content: bytes
    format: str = "text"
    is_primary: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.artifact_type:
            raise ValueError("artifact_type must not be empty")
        if not self.filename or "/" in self.filename or "\\" in self.filename:
            raise ValueError("filename must be a plain file name")
        if not isinstance(self.content, bytes):
            raise TypeError("artifact content must be bytes")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    error_code: str = "VALIDATION_FAILED"
    safe_message: str = "Artifact validation failed."


class BackupDriver(ABC):
    driver_api_version: int = 1
    driver_id: str
    display_name: str
    user_selectable: bool = True
    capabilities: frozenset[str] = frozenset()
    normalizer_version: str = "1"

    @abstractmethod
    def collect(self, context: DriverContext) -> list[CollectedArtifact]:
        """Collect one or more read-only configuration artifacts."""

    @abstractmethod
    def validate(self, artifact: CollectedArtifact) -> ValidationResult:
        """Validate an artifact without changing it."""

    @abstractmethod
    def normalize(self, artifact: CollectedArtifact) -> bytes:
        """Return deterministic bytes used for change detection."""

    def redact_for_display(self, text: str) -> str:
        return text
