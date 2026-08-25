from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field


class StorageError(RuntimeError):
    """Expected storage failure carrying only a safe message."""

    def __init__(self, safe_message: str = "Configuration storage operation failed."):
        super().__init__(safe_message)
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class StorageObject:
    key: str
    size: int
    metadata: Mapping[str, str] = field(default_factory=dict)


class ConfigStorage(ABC):
    @abstractmethod
    def put(
        self,
        key: str,
        content: bytes,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObject:
        pass

    @abstractmethod
    def get(self, key: str) -> bytes:
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        pass

    @abstractmethod
    def stage_delete(self, key: str, namespace: str) -> str | None:
        """Move/copy an object into reversible quarantine, or return None if missing."""

    @abstractmethod
    def restore_staged_delete(self, key: str, staged_key: str) -> None:
        """Restore an object from quarantine after a failed database transaction."""

    @abstractmethod
    def purge_staged_delete(self, staged_key: str) -> None:
        """Permanently remove a quarantined object after a successful transaction."""

    @abstractmethod
    def healthcheck(self) -> bool:
        pass
