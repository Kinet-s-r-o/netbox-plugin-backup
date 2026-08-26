from __future__ import annotations

import posixpath
import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_DASH = re.compile(r"-+")
_MAX_DEVICE_SEGMENT_LENGTH = 128
_MAX_BACKUP_DEVICE_SEGMENT_LENGTH = 96


def device_directory_name(device_name: str | None, device_id: int) -> str:
    """Return a readable, traversal-safe remote directory for a NetBox device."""
    normalized = unicodedata.normalize("NFKD", str(device_name or ""))
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    safe_name = _UNSAFE_SEGMENT.sub("-", ascii_name.strip())
    safe_name = _REPEATED_DASH.sub("-", safe_name).strip("._-")
    safe_name = "-".join(part for part in safe_name.split("-") if part.strip("."))
    safe_name = safe_name[:_MAX_DEVICE_SEGMENT_LENGTH].rstrip("._-")
    return safe_name or f"device-{device_id}"


def revision_destination_path(
    base_path: str,
    *,
    device_name: str | None,
    device_id: int,
    revision_uuid: UUID | str,
) -> str:
    """Build the canonical absolute remote path for a replicated revision."""
    return "/" + posixpath.join(
        base_path.strip("/"),
        "devices",
        device_directory_name(device_name, device_id),
        "revisions",
        str(revision_uuid),
    ).lstrip("/")


def backup_filename_stem(
    device_name: str | None,
    device_id: int,
    created_at: datetime,
) -> str:
    """Return a stable, readable device-and-UTC-time stem for an FTP backup."""
    device_segment = device_directory_name(device_name, device_id)
    device_segment = device_segment[:_MAX_BACKUP_DEVICE_SEGMENT_LENGTH].rstrip("._-")
    return f"{device_segment}_{backup_creation_timestamp(created_at)}"


def backup_creation_timestamp(created_at: datetime) -> str:
    """Format one revision creation time for a portable FTP path segment."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(UTC).strftime("%Y-%m-%d_%H-%M-%S")


def ftp_revision_destination_path(
    base_path: str,
    *,
    device_name: str | None,
    device_id: int,
    created_at: datetime,
) -> str:
    """Build the readable canonical path used by new FTP revision copies."""
    return "/" + posixpath.join(
        base_path.strip("/"),
        "devices",
        device_directory_name(device_name, device_id),
        "backups",
        backup_creation_timestamp(created_at),
    ).lstrip("/")
