from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .destination_paths import backup_filename_stem, device_directory_name
from .destination_types import DestinationError

if TYPE_CHECKING:
    from netbox_config_backup.models import ConfigRevision


def unique_json_object(pairs):
    """Reject duplicate keys while decoding a JSON object."""

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def artifact_filename(storage_key: str, artifact_type: str) -> str:
    """Return a safe basename for an artifact stored by the primary backend."""

    filename = PurePosixPath(storage_key).name
    return filename if filename and filename not in {".", ".."} else f"{artifact_type}.bin"


def readable_artifact_filename(
    revision: ConfigRevision,
    artifact,
    *,
    stem_override: str | None = None,
) -> str:
    """Build the human-readable immutable filename used by FTP replicas."""

    original = artifact_filename(artifact.storage_key, artifact.artifact_type)
    suffix = "".join(PurePosixPath(original).suffixes)
    if (
        not suffix
        or len(suffix) > 24
        or any(
            character not in ".abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            for character in suffix
        )
    ):
        suffix = ".bin"
    stem = stem_override or backup_filename_stem(
        revision.target.device.name,
        revision.target.device_id,
        revision.created,
    )
    if artifact.is_primary:
        return f"{stem}{suffix}"
    safe_type = device_directory_name(artifact.artifact_type, 0)[:48] or "artifact"
    return f"{stem}_{safe_type}{suffix}"


def uses_readable_ftp_layout(remote_path: str) -> bool:
    parts = PurePosixPath(remote_path).parts
    return len(parts) >= 2 and (
        parts[-2] == "backups" or (len(parts) >= 3 and parts[-3] == "backups")
    )


def validate_direct_filename(filename: str) -> None:
    """Reject paths and control characters where one direct child is required."""

    path = PurePosixPath(filename)
    if (
        not filename
        or path.name != filename
        or filename in {".", ".."}
        or "\\" in filename
        or "\x00" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise DestinationError(
            "DELETE_FILESET_INVALID",
            "An FTP revision filename is not safe to delete.",
        )


def is_missing_ftp_error(exc: BaseException) -> bool:
    message = str(exc).strip().lower()
    if not message.startswith("550"):
        return False
    return any(
        marker in message
        for marker in (
            "not found",
            "no such",
            "does not exist",
            "cannot find",
            "no files",
            "empty",
        )
    )


def is_denied_ftp_error(exc: BaseException) -> bool:
    message = str(exc).strip().lower()
    return any(
        marker in message
        for marker in (
            "permission",
            "denied",
            "not allowed",
            "access is denied",
            "access denied",
        )
    )


def join_ftp_path(base: str, *parts: str) -> str:
    return posixpath.join(base.rstrip("/"), *parts)


def absolute_ftp_path(path: str) -> str:
    return "/" + path.lstrip("/")
