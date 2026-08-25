from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from .destination_ftp import write_verified_ftp_replica_to_archive
from .destination_paths import device_directory_name
from .destination_types import DestinationError

_PACKAGE_DIRECTORY = ".recovery-packages"
_PACKAGE_NAME = re.compile(
    r"^(?P<token>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})\.zip$",
    re.IGNORECASE,
)
_TEMP_NAME = re.compile(r"^\.[0-9a-f-]{36}\..+\.tmp$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RecoveryPackageResult:
    ready: bool
    token: str
    filename: str
    size: int
    sha256: str
    file_count: int
    verified_bytes: int
    expires_at: str
    replica_id: int
    revision_id: int
    destination_id: int
    destination_name: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_ftp_recovery_package(
    replica,
    *,
    storage_root: str | os.PathLike[str],
    package_token: UUID | str,
    ttl_minutes: int,
    max_total_bytes: int,
    now: datetime | None = None,
) -> RecoveryPackageResult:
    """Create an atomic, temporary ZIP from a successful FTP replica.

    The source transport is strictly read-only. A partially downloaded or
    integrity-invalid package is deleted and is never exposed to a user.
    """

    token = _canonical_token(package_token)
    now = _aware_utc(now)
    if ttl_minutes < 1:
        raise DestinationError(
            "RECOVERY_TTL_INVALID",
            "The recovery package lifetime is not configured correctly.",
        )

    package_root = recovery_package_root(storage_root)
    cleanup_expired_recovery_packages(
        storage_root=storage_root,
        ttl_minutes=ttl_minutes,
        now=now,
    )
    final_path = _package_path(package_root, token)
    if final_path.exists():
        raise DestinationError(
            "RECOVERY_TOKEN_CONFLICT",
            "A recovery package with this identifier already exists.",
        )

    revision = replica.revision
    device = revision.target.device
    device_directory = device_directory_name(device.name, revision.target.device_id)
    archive_prefix = f"{device_directory}/{revision.revision_uuid}"
    download_filename = f"{device_directory}_{revision.revision_uuid}_verified-ftp-backup.zip"
    descriptor = -1
    temporary_path: Path | None = None
    published = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{token}.", suffix=".tmp", dir=package_root
        )
        temporary_path = Path(temporary_name)
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w+b") as handle:
            descriptor = -1
            with zipfile.ZipFile(
                handle,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                transfer = write_verified_ftp_replica_to_archive(
                    replica,
                    archive,
                    archive_prefix=archive_prefix,
                    max_total_bytes=max_total_bytes,
                )
                archive.writestr(
                    f"{archive_prefix}/RECOVERY_README.txt",
                    _recovery_readme(replica, transfer.remote_path, now),
                )
            handle.flush()
            os.fsync(handle.fileno())
        if final_path.exists():
            raise DestinationError(
                "RECOVERY_TOKEN_CONFLICT",
                "A recovery package with this identifier already exists.",
            )
        os.replace(temporary_path, final_path)
        temporary_path = None
        published = True
        os.chmod(final_path, 0o600)
    except DestinationError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        if published:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise DestinationError(
            "RECOVERY_PACKAGE_WRITE_FAILED",
            "The temporary recovery package could not be created safely.",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        package_size, package_hash = _file_integrity(final_path)
    except OSError as exc:
        if published:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise DestinationError(
            "RECOVERY_PACKAGE_WRITE_FAILED",
            "The temporary recovery package could not be verified safely.",
        ) from exc

    expires_at = now + timedelta(minutes=ttl_minutes)
    return RecoveryPackageResult(
        ready=True,
        token=token,
        filename=download_filename,
        size=package_size,
        sha256=package_hash,
        file_count=transfer.file_count,
        verified_bytes=transfer.verified_bytes,
        expires_at=expires_at.isoformat(),
        replica_id=replica.pk,
        revision_id=revision.pk,
        destination_id=replica.destination.pk,
        destination_name=replica.destination.name,
    )


def validate_recovery_package(
    *,
    storage_root: str | os.PathLike[str],
    package_token: UUID | str,
    expected_size: int,
    expected_sha256: str,
) -> Path:
    """Return a confined package path only when its recorded integrity matches."""

    token = _canonical_token(package_token)
    path = _package_path(recovery_package_root(storage_root), token)
    if path.is_symlink() or not path.is_file():
        raise DestinationError(
            "RECOVERY_PACKAGE_MISSING",
            "The temporary recovery package is no longer available.",
        )
    try:
        size, digest = _file_integrity(path)
    except OSError as exc:
        raise DestinationError(
            "RECOVERY_PACKAGE_UNREADABLE",
            "The temporary recovery package could not be read.",
        ) from exc
    if size != expected_size or digest != expected_sha256:
        raise DestinationError(
            "RECOVERY_PACKAGE_INVALID",
            "The temporary recovery package failed local integrity verification.",
        )
    return path


def cleanup_expired_recovery_packages(
    *,
    storage_root: str | os.PathLike[str],
    ttl_minutes: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete only expired plugin-generated ZIPs and abandoned temporary files."""

    if ttl_minutes < 1:
        return {"deleted": 0, "failed": 0}
    now = _aware_utc(now)
    package_root = recovery_package_root(storage_root)
    cutoff = now.timestamp() - (ttl_minutes * 60)
    deleted = 0
    failed = 0
    for candidate in package_root.iterdir():
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        if not (_PACKAGE_NAME.fullmatch(candidate.name) or _TEMP_NAME.fullmatch(candidate.name)):
            continue
        try:
            if candidate.stat().st_mtime >= cutoff:
                continue
            candidate.unlink()
            deleted += 1
        except OSError:
            failed += 1
    return {"deleted": deleted, "failed": failed}


def recovery_package_root(storage_root: str | os.PathLike[str]) -> Path:
    root = Path(storage_root).resolve()
    package_root = (root / _PACKAGE_DIRECTORY).resolve()
    if package_root.parent != root:
        raise DestinationError(
            "RECOVERY_STORAGE_INVALID",
            "The temporary recovery package directory is invalid.",
        )
    try:
        package_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(package_root, 0o700)
    except OSError as exc:
        raise DestinationError(
            "RECOVERY_STORAGE_UNAVAILABLE",
            "The temporary recovery package directory is unavailable.",
        ) from exc
    return package_root


def recovery_package_is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    try:
        expires = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    return _aware_utc(expires) <= _aware_utc(now)


def _package_path(package_root: Path, token: str) -> Path:
    path = (package_root / f"{token}.zip").resolve()
    if path.parent != package_root:
        raise DestinationError(
            "RECOVERY_STORAGE_INVALID",
            "The temporary recovery package path is invalid.",
        )
    return path


def _canonical_token(value: UUID | str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DestinationError(
            "RECOVERY_TOKEN_INVALID",
            "The recovery package identifier is invalid.",
        ) from exc


def _aware_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _file_integrity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _recovery_readme(replica, remote_path: str, created_at: datetime) -> str:
    revision = replica.revision
    return (
        "NetBox Config Backup - verified FTP recovery package\n"
        "\n"
        f"Device: {_single_line(revision.target.device.name)}\n"
        f"Revision UUID: {revision.revision_uuid}\n"
        f"FTP destination: {_single_line(replica.destination.name)}\n"
        f"FTP revision path: {_single_line(remote_path)}\n"
        f"Package prepared: {created_at.isoformat()}\n"
        "\n"
        "Every copied FTP file was verified against the size and SHA256 recorded "
        "by NetBox.\n"
        "This package is for manual recovery only. The plugin did not connect to "
        "the device and does not import, restore, or apply any configuration.\n"
    )


def _single_line(value) -> str:
    return " ".join(str(value).splitlines()).strip()
