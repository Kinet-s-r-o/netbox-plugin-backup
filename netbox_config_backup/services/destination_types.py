from __future__ import annotations

from dataclasses import dataclass


class DestinationError(RuntimeError):
    """Expected external destination failure with a persistence-safe message."""

    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class ReplicationResult:
    remote_path: str
    bytes_transferred: int
    artifact_count: int
