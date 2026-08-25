from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from netbox_config_backup.drivers.base import ConnectionParameters, ReceiverParameters


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: Any | None
    target_id: int
    device_id: int
    device_name: str
    driver_id: str
    address: str | None = None
    connection: ConnectionParameters = field(default_factory=ConnectionParameters)
    driver_options: Mapping[str, Any] = field(default_factory=dict)
    store_mode: str = "changed_only"
    secret_provider_id: str | None = None
    secret_reference: str | None = None
    receiver: ReceiverParameters | None = None
    receiver_secret_provider_id: str | None = None
    receiver_secret_reference: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    revision_id: Any
    normalized_hash: str
    primary_raw_hash: str


@dataclass(frozen=True, slots=True)
class StoredArtifactRecord:
    artifact_type: str
    format: str
    storage_key: str
    size: int
    raw_hash: str
    normalized_hash: str
    is_primary: bool


class BackupRepository(Protocol):
    def get_execution_context(self, run_id: Any) -> ExecutionContext: ...

    def mark_running(self, run_id: Any, *, started_at: datetime) -> None: ...

    def get_latest_revision(self, target_id: int) -> RevisionSnapshot | None: ...

    def commit_unchanged(
        self,
        run_id: Any,
        *,
        raw_changed: bool,
        finished_at: datetime,
    ) -> Any: ...

    def commit_revision(
        self,
        run_id: Any,
        *,
        revision_uuid: UUID,
        normalized_hash: str,
        normalizer_version: str,
        driver_id: str,
        content_changed: bool,
        raw_changed: bool,
        artifacts: list[StoredArtifactRecord],
        finished_at: datetime,
    ) -> Any: ...

    def mark_failed(
        self,
        run_id: Any,
        *,
        status: str,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None: ...
