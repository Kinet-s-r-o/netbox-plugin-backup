from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from django.conf import settings

from netbox_config_backup.choices import MOUNTED_DESTINATION_PROTOCOLS, ReplicaStatusChoices
from netbox_config_backup.storage import build_config_storage
from netbox_config_backup.storage.base import StorageError

from .destination_ftp import (
    _build_manifest,
    _expected_revision_files,
    _manifest_matches_revision,
    _readable_stem_from_revision_path,
    _uses_readable_ftp_layout,
    _validate_recorded_revision_path,
)
from .destination_paths import ftp_revision_destination_path
from .destination_types import DestinationError, ReplicationResult

if TYPE_CHECKING:
    from netbox_config_backup.models import BackupDestination, ConfigRevision, RevisionReplica


@dataclass(frozen=True, slots=True)
class DeletedMountedRevisionResult:
    remote_path: str
    expected_file_count: int
    deleted_file_count: int
    missing_file_count: int
    deleted_bytes: int
    directory_removed: bool
    already_absent: bool


@dataclass(frozen=True, slots=True)
class _ExpectedContent:
    size: int
    sha256: str


def test_mounted_destination(destination: BackupDestination) -> dict[str, object]:
    _require_mounted_protocol(destination)
    root = _destination_root(destination)
    directory = _safe_path(root, f"/{destination.base_path.strip('/')}")
    test_path = directory / f".netbox-config-backup-test-{uuid.uuid4().hex}"
    payload = b"netbox-config-backup-mounted-storage-test\n"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(root, test_path.parent)
        _write_new_file(test_path, payload)
        if test_path.read_bytes() != payload:
            raise DestinationError(
                "DESTINATION_VERIFY_FAILED",
                "The mounted-storage test object could not be verified after writing it.",
            )
        test_path.unlink()
        if test_path.exists():
            raise DestinationError(
                "DESTINATION_DELETE_FAILED",
                "The mounted-storage test object still exists after deletion.",
            )
        return {
            "success": True,
            "safe_message": (
                f"{destination.get_protocol_display()} write, integrity verification, and delete "
                "succeeded."
            ),
            "host_key_candidate": None,
            "protocol": destination.protocol,
        }
    except DestinationError:
        raise
    except PermissionError as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The NetBox worker cannot write to the mounted storage directory.",
        ) from exc
    except OSError as exc:
        raise DestinationError(
            "DESTINATION_TEST_FAILED",
            "The mounted storage write verification failed.",
        ) from exc
    finally:
        try:
            test_path.unlink(missing_ok=True)
        except OSError:
            pass


def replicate_revision_mounted(
    destination: BackupDestination,
    revision: ConfigRevision,
    *,
    recorded_remote_path: str | None = None,
) -> ReplicationResult:
    _require_mounted_protocol(destination)
    if not destination.enabled:
        raise DestinationError("DESTINATION_DISABLED", "The mounted storage is disabled.")

    artifacts = tuple(revision.artifacts.all())
    if not artifacts:
        raise DestinationError("NO_ARTIFACTS", "The revision contains no backup artifacts.")

    revision_path = recorded_remote_path or ftp_revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        created_at=revision.created,
        revision_id=revision.pk,
    )
    _layout, recorded_device_directory = _validate_recorded_revision_path(
        destination,
        revision,
        revision_path,
        error_code="REPLICATION_PATH_INVALID",
    )
    readable_names = _uses_readable_ftp_layout(revision_path)
    readable_stem = _readable_stem_from_revision_path(revision_path, revision)
    expected_files = _expected_revision_files(
        revision,
        readable_names=readable_names,
        readable_stem=readable_stem,
        manifest_device_directory=recorded_device_directory,
    )
    expected_by_type = {
        item.artifact_type: item for item in expected_files if item.artifact_type != "manifest"
    }
    root = _destination_root(destination)
    revision_directory = _safe_path(root, revision_path)
    storage = build_config_storage()
    transferred = 0
    try:
        revision_directory.mkdir(parents=True, exist_ok=True)
        _reject_symlink_path(root, revision_directory)
        for artifact in artifacts:
            if artifact.size > destination.max_artifact_size:
                raise DestinationError(
                    "ARTIFACT_TOO_LARGE",
                    "An artifact exceeds the mounted storage size limit.",
                )
            try:
                content = storage.get(artifact.storage_key)
            except StorageError as exc:
                raise DestinationError(
                    "PRIMARY_ARTIFACT_MISSING",
                    "A primary backup artifact could not be read for replication.",
                ) from exc
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != artifact.size or digest != artifact.raw_hash:
                raise DestinationError(
                    "PRIMARY_ARTIFACT_INVALID",
                    "A primary backup artifact failed integrity verification.",
                )
            expected = expected_by_type[artifact.artifact_type]
            if _put_immutable(revision_directory / expected.filename, content, digest):
                transferred += len(content)

        manifest = _build_manifest(
            revision,
            artifacts,
            readable_names=readable_names,
            readable_stem=readable_stem,
            device_directory_override=recorded_device_directory,
        )
        if _put_immutable(
            revision_directory / "_netbox_manifest.json",
            manifest,
            hashlib.sha256(manifest).hexdigest(),
        ):
            transferred += len(manifest)
    except DestinationError:
        raise
    except PermissionError as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The NetBox worker cannot write to the mounted storage directory.",
        ) from exc
    except OSError as exc:
        raise DestinationError(
            "DESTINATION_WRITE_FAILED",
            "The revision could not be written to mounted storage.",
        ) from exc
    return ReplicationResult(
        remote_path=revision_path,
        bytes_transferred=transferred,
        artifact_count=len(artifacts),
    )


def reconcile_mounted_destination(
    destination: BackupDestination,
    *,
    replicas: Iterable[RevisionReplica] | None = None,
    issue_limit: int = 100,
) -> dict[str, object]:
    _require_mounted_protocol(destination)
    if replicas is None:
        supplied = tuple(
            destination.replicas.filter(
                status=ReplicaStatusChoices.SUCCESS,
                remote_available=True,
                remote_deleted_at__isnull=True,
            )
            .select_related("revision__target__device")
            .prefetch_related("revision__artifacts")
            .order_by("pk")
        )
        skipped = destination.replicas.exclude(
            status=ReplicaStatusChoices.SUCCESS,
            remote_available=True,
            remote_deleted_at__isnull=True,
        ).count()
    else:
        all_supplied = tuple(replicas)
        supplied = tuple(
            replica
            for replica in all_supplied
            if replica.destination_id == destination.pk
            and replica.status == ReplicaStatusChoices.SUCCESS
            and replica.remote_available
            and replica.remote_deleted_at is None
        )
        skipped = len(all_supplied) - len(supplied)

    summary: dict[str, object] = {
        "success": True,
        "safe_message": "All expected mounted-storage copies passed integrity verification.",
        "checked_replicas": 0,
        "healthy_replicas": 0,
        "failed_replicas": 0,
        "skipped_replicas": skipped,
        "checked_files": 0,
        "verified_bytes": 0,
        "missing_files": 0,
        "size_mismatches": 0,
        "hash_mismatches": 0,
        "unreadable_files": 0,
        "issues": [],
        "issues_truncated": False,
        "protocol": destination.protocol,
    }
    root = _destination_root(destination)
    for replica in supplied:
        revision = replica.revision
        revision_path = str(replica.remote_path or "")
        _layout, recorded_device_directory = _validate_recorded_revision_path(
            destination,
            revision,
            revision_path,
            error_code="RECONCILIATION_PATH_INVALID",
        )
        readable_names = _uses_readable_ftp_layout(revision_path)
        expected_files = _expected_revision_files(
            revision,
            readable_names=readable_names,
            readable_stem=_readable_stem_from_revision_path(revision_path, revision),
            manifest_device_directory=recorded_device_directory,
        )
        revision_directory = _safe_path(root, revision_path)
        summary["checked_replicas"] += 1
        failed = False
        for expected in expected_files:
            summary["checked_files"] += 1
            path = revision_directory / expected.filename
            if expected.artifact_type == "manifest":
                state, actual_size = _verify_manifest_file(
                    path,
                    revision=revision,
                    revision_path=revision_path,
                    expected_files=expected_files,
                )
            else:
                state, actual_size = _verify_file(path, expected)
            if state == "ok":
                summary["verified_bytes"] += actual_size
                continue
            failed = True
            summary[
                {
                    "missing": "missing_files",
                    "size_mismatch": "size_mismatches",
                    "hash_mismatch": "hash_mismatches",
                    "unreadable": "unreadable_files",
                }[state]
            ] += 1
            issues = summary["issues"]
            if len(issues) < issue_limit:
                issues.append(
                    {
                        "replica_id": replica.pk,
                        "revision_id": revision.pk,
                        "revision_uuid": str(revision.revision_uuid),
                        "device": revision.target.device.name,
                        "filename": expected.filename,
                        "artifact_type": expected.artifact_type,
                        "problem": state,
                    }
                )
            else:
                summary["issues_truncated"] = True
        summary["failed_replicas" if failed else "healthy_replicas"] += 1

    problem_count = sum(
        summary[key]
        for key in ("missing_files", "size_mismatches", "hash_mismatches", "unreadable_files")
    )
    if problem_count:
        summary["success"] = False
        summary["safe_message"] = (
            f"The integrity audit found {problem_count} problem(s) in "
            f"{summary['failed_replicas']} revision copy/copies."
        )
    elif not summary["checked_replicas"]:
        summary["safe_message"] = "There are no successful revision copies to audit yet."
    return summary


def delete_revision_replica_mounted(
    replica: RevisionReplica,
) -> DeletedMountedRevisionResult:
    destination = replica.destination
    _require_mounted_protocol(destination)
    if not destination.enabled:
        raise DestinationError(
            "DESTINATION_DISABLED",
            "The mounted storage is disabled and no remote file was deleted.",
        )
    revision = replica.revision
    remote_path = str(replica.remote_path or "")
    _layout, recorded_device_directory = _validate_recorded_revision_path(
        destination,
        revision,
        remote_path,
        error_code="DELETE_PATH_INVALID",
    )
    expected = _expected_revision_files(
        revision,
        readable_names=_uses_readable_ftp_layout(remote_path),
        readable_stem=_readable_stem_from_revision_path(remote_path, revision),
        manifest_device_directory=recorded_device_directory,
    )
    root = _destination_root(destination)
    directory = _safe_path(root, remote_path)
    if not directory.exists():
        return DeletedMountedRevisionResult(
            remote_path, len(expected), 0, len(expected), 0, False, True
        )
    _reject_symlink_path(root, directory)
    expected_names = {item.filename for item in expected}
    actual_names = {entry.name for entry in directory.iterdir()}
    unknown = actual_names - expected_names
    if unknown:
        raise DestinationError(
            "DELETE_FILESET_CONFLICT",
            "The revision directory contains unknown files and was not deleted.",
        )
    deleted = 0
    missing = 0
    deleted_bytes = 0
    try:
        for item in expected:
            path = directory / item.filename
            if not path.exists():
                missing += 1
                continue
            if path.is_symlink() or not path.is_file():
                raise DestinationError(
                    "DELETE_FILESET_INVALID",
                    "The revision directory contains an unsafe file entry.",
                )
            deleted_bytes += path.stat().st_size
            path.unlink()
            deleted += 1
        directory.rmdir()
    except DestinationError:
        raise
    except PermissionError as exc:
        raise DestinationError(
            "DESTINATION_DELETE_DENIED",
            "The NetBox worker cannot delete the expired mounted-storage copy.",
        ) from exc
    except OSError as exc:
        raise DestinationError(
            "DESTINATION_DELETE_FAILED",
            "The mounted-storage revision copy could not be deleted safely.",
        ) from exc
    return DeletedMountedRevisionResult(
        remote_path,
        len(expected),
        deleted,
        missing,
        deleted_bytes,
        True,
        False,
    )


def _require_mounted_protocol(destination: BackupDestination) -> None:
    if destination.protocol not in MOUNTED_DESTINATION_PROTOCOLS:
        raise DestinationError(
            "PROTOCOL_UNSUPPORTED",
            "This operation requires an NFS or SMB3 mounted storage profile.",
        )


def _destination_root(destination: BackupDestination) -> Path:
    configured = settings.PLUGINS_CONFIG.get("netbox_config_backup", {})
    roots = tuple(configured.get("network_storage_mount_roots") or ())
    if not roots:
        raise DestinationError(
            "MOUNT_ROOT_NOT_CONFIGURED",
            "No allowed network-storage mount root is configured for this deployment.",
        )
    try:
        configured_path = Path(destination.mount_path)
        if configured_path.is_symlink():
            raise DestinationError(
                "MOUNT_PATH_NOT_ALLOWED",
                "The configured network-storage mount path cannot be a symbolic link.",
            )
        root = configured_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DestinationError(
            "MOUNT_UNAVAILABLE",
            "The configured network-storage mount is not available to the NetBox worker.",
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise DestinationError("MOUNT_UNAVAILABLE", "The configured mount path is not a directory.")
    allowed = False
    for configured_root in roots:
        try:
            configured_root_path = Path(configured_root)
            if configured_root_path.is_symlink():
                continue
            allowed_root = configured_root_path.resolve(strict=True)
            if allowed_root == allowed_root.parent:
                # Never accept a filesystem root as the mount allow-list.
                continue
            root.relative_to(allowed_root)
        except (OSError, RuntimeError, ValueError):
            continue
        allowed = True
        break
    if not allowed:
        raise DestinationError(
            "MOUNT_PATH_NOT_ALLOWED",
            "The mounted storage path is outside the deployment's allowed mount roots.",
        )
    if configured.get("network_storage_require_mountpoint", True) and not os.path.ismount(root):
        raise DestinationError(
            "MOUNT_NOT_ACTIVE",
            "The configured NFS/SMB3 directory is not an active mount in the NetBox worker.",
        )
    return root


def _safe_path(root: Path, remote_path: str) -> Path:
    pure = PurePosixPath(str(remote_path))
    if not pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts[1:]):
        raise DestinationError("DESTINATION_PATH_INVALID", "The mounted-storage path is unsafe.")
    candidate = root.joinpath(*pure.parts[1:])
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DestinationError(
            "DESTINATION_PATH_INVALID", "The mounted-storage path escapes its configured root."
        ) from exc
    return candidate


def _reject_symlink_path(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise DestinationError(
                "DESTINATION_PATH_INVALID",
                "The mounted-storage path contains a symbolic link.",
            )


def _write_new_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _put_immutable(path: Path, content: bytes, digest: str) -> bool:
    if path.exists():
        state, _size = _verify_file(
            path,
            _ExpectedContent(size=len(content), sha256=digest),
        )
        if state == "ok":
            return False
        raise DestinationError(
            "DESTINATION_CONFLICT",
            "A different object already exists at the immutable revision path.",
        )
    try:
        _write_new_file(path, content)
    except FileExistsError:
        return _put_immutable(path, content, digest)
    state, _size = _verify_file(
        path,
        _ExpectedContent(size=len(content), sha256=digest),
    )
    if state != "ok":
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED",
            "A mounted-storage artifact failed verification after writing it.",
        )
    return True


def _verify_file(path: Path, expected) -> tuple[str, int]:
    try:
        if path.is_symlink() or not path.is_file():
            return "missing", 0
        stat = path.stat()
        if stat.st_size != expected.size:
            return "size_mismatch", stat.st_size
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected.sha256:
            return "hash_mismatch", stat.st_size
        return "ok", stat.st_size
    except FileNotFoundError:
        return "missing", 0
    except OSError:
        return "unreadable", 0


def _verify_manifest_file(
    path: Path,
    *,
    revision: ConfigRevision,
    revision_path: str,
    expected_files,
) -> tuple[str, int]:
    """Verify manifest semantics without depending on the mutable device name.

    The manifest intentionally records the device name at backup time. A later
    rename in NetBox must therefore not make an otherwise immutable copy fail
    its integrity audit.
    """

    try:
        if path.is_symlink() or not path.is_file():
            return "missing", 0
        size = path.stat().st_size
        if size > 1024 * 1024:
            return "size_mismatch", size
        content = path.read_bytes()
        if not _manifest_matches_revision(
            content,
            revision=revision,
            revision_path=revision_path,
            expected_files=expected_files,
        ):
            return "hash_mismatch", size
        return "ok", size
    except FileNotFoundError:
        return "missing", 0
    except OSError:
        return "unreadable", 0
