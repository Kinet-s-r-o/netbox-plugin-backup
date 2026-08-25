from __future__ import annotations

import posixpath
import re
import unicodedata
from uuid import UUID

_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_DASH = re.compile(r"-+")
_MAX_DEVICE_SEGMENT_LENGTH = 128


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
