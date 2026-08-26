from __future__ import annotations

import ftplib
import hashlib
import io
import json
import posixpath
import time
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from netbox_config_backup.choices import ReplicaStatusChoices
from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.storage import build_config_storage
from netbox_config_backup.storage.base import StorageError

from .destination_paths import (
    backup_creation_timestamp,
    backup_filename_stem,
    device_directory_name,
    ftp_revision_destination_path,
)
from .destination_types import DestinationError, ReplicationResult

if TYPE_CHECKING:
    from netbox_config_backup.models import BackupDestination, ConfigRevision, RevisionReplica


@dataclass(frozen=True, slots=True)
class ExpectedRemoteFile:
    filename: str
    size: int
    sha256: str
    artifact_type: str


@dataclass(frozen=True, slots=True)
class VerifiedFtpDownloadResult:
    """Summary of a read-only, hash-verified FTP download."""

    file_count: int
    verified_bytes: int
    remote_path: str


@dataclass(frozen=True, slots=True)
class DeletedFtpRevisionResult:
    """Summary of one narrowly scoped, idempotent FTP replica deletion."""

    remote_path: str
    expected_file_count: int
    deleted_file_count: int
    missing_file_count: int
    deleted_bytes: int
    directory_removed: bool
    already_absent: bool


_MAX_MANIFEST_BYTES = 1024 * 1024


def reconcile_ftp_destination(
    destination: BackupDestination,
    *,
    replicas: Iterable[RevisionReplica] | None = None,
    issue_limit: int = 100,
) -> dict[str, object]:
    """Read and hash successful FTP replicas without changing remote state."""

    explicit_replicas = replicas is not None
    if replicas is None:
        replica_queryset = (
            destination.replicas.filter(
                status=ReplicaStatusChoices.SUCCESS,
                remote_available=True,
                remote_deleted_at__isnull=True,
            )
            .select_related("revision__target__device")
            .prefetch_related("revision__artifacts")
            .order_by("pk")
        )
        replica_iterable = replica_queryset.iterator(chunk_size=100)
        skipped_replicas = destination.replicas.exclude(
            status=ReplicaStatusChoices.SUCCESS,
            remote_available=True,
            remote_deleted_at__isnull=True,
        ).count()
    else:
        supplied_replicas = tuple(replicas)
        auditable_replicas = tuple(
            replica
            for replica in supplied_replicas
            if (
                getattr(replica, "destination_id", None)
                or getattr(getattr(replica, "destination", None), "pk", None)
            )
            == destination.pk
            and replica.status == ReplicaStatusChoices.SUCCESS
            and replica.remote_available
            and replica.remote_deleted_at is None
        )
        replica_iterable = iter(auditable_replicas)
        skipped_replicas = len(supplied_replicas) - len(auditable_replicas)

    summary: dict[str, object] = {
        "success": True,
        "safe_message": "All expected FTP revision copies passed integrity verification.",
        "checked_replicas": 0,
        "healthy_replicas": 0,
        "failed_replicas": 0,
        "skipped_replicas": skipped_replicas,
        "checked_files": 0,
        "verified_bytes": 0,
        "missing_files": 0,
        "size_mismatches": 0,
        "hash_mismatches": 0,
        "unreadable_files": 0,
        "issues": [],
        "issues_truncated": False,
        "protocol": "ftp",
    }

    if explicit_replicas and not auditable_replicas:
        summary["safe_message"] = "There are no available FTP revision copies to audit."
        return summary

    ftp = _connect(destination)
    try:
        for replica in replica_iterable:
            revision = replica.revision
            # The database path is the immutable identity of this copy. Never
            # regenerate it from the mutable NetBox device name: doing so after
            # a rename would audit a different directory and orphan the real
            # historical copy.
            revision_path = str(replica.remote_path or "")
            _validate_recorded_revision_path(
                destination,
                revision,
                revision_path,
                error_code="RECONCILIATION_PATH_INVALID",
            )
            expected_files = _expected_revision_files(
                revision,
                readable_names=_uses_readable_ftp_layout(revision_path),
                readable_stem=_readable_stem_from_revision_path(revision_path, revision),
            )
            summary["checked_replicas"] += 1
            replica_failed = False

            for expected in expected_files:
                summary["checked_files"] += 1
                remote_path = _join(revision_path, expected.filename)
                result, actual_size = _verify_remote_file(
                    ftp,
                    remote_path,
                    expected,
                    revision=revision,
                    revision_path=revision_path,
                    expected_files=expected_files,
                )
                if result == "ok":
                    summary["verified_bytes"] += actual_size
                    continue

                replica_failed = True
                counter = {
                    "missing": "missing_files",
                    "size_mismatch": "size_mismatches",
                    "hash_mismatch": "hash_mismatches",
                    "unreadable": "unreadable_files",
                }[result]
                summary[counter] += 1
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
                            "problem": result,
                        }
                    )
                else:
                    summary["issues_truncated"] = True

            if replica_failed:
                summary["failed_replicas"] += 1
            else:
                summary["healthy_replicas"] += 1
    except DestinationError:
        raise
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_RECONCILIATION_FAILED",
            "The FTP integrity audit could not complete.",
        ) from exc
    finally:
        _close(ftp)

    issue_count = (
        summary["missing_files"]
        + summary["size_mismatches"]
        + summary["hash_mismatches"]
        + summary["unreadable_files"]
    )
    if issue_count:
        summary["success"] = False
        summary["safe_message"] = (
            f"The FTP audit found {issue_count} problem(s) in "
            f"{summary['failed_replicas']} revision copy/copies."
        )
    elif not summary["checked_replicas"]:
        summary["safe_message"] = "There are no successful FTP revision copies to audit yet."
    return summary


def test_ftp_destination(destination: BackupDestination) -> dict[str, object]:
    ftp = _connect(destination)
    test_name = f".netbox-config-backup-test-{uuid.uuid4().hex}"
    remote_path = _absolute(_join(destination.base_path, test_name))
    payload = b"netbox-config-backup-ftp-test\n"
    try:
        _mkdirs(ftp, destination.base_path)
        _store(ftp, remote_path, payload)
        downloaded = _read_remote(ftp, remote_path, len(payload))
        if downloaded != payload:
            raise DestinationError(
                "DESTINATION_VERIFY_FAILED",
                "The FTP test object could not be verified after upload.",
            )
        _delete_verified(ftp, remote_path)
        return {
            "success": True,
            "safe_message": "FTP login, upload, integrity verification, and delete succeeded.",
            "host_key_candidate": None,
            "protocol": "ftp",
        }
    except DestinationError:
        raise
    except ftplib.error_perm as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The FTP account cannot write to the configured destination path.",
        ) from exc
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_TEST_FAILED", "The FTP write verification failed."
        ) from exc
    finally:
        try:
            ftp.delete(remote_path)
        except ftplib.all_errors:
            pass
        _close(ftp)


def replicate_revision_ftp(
    destination: BackupDestination,
    revision: ConfigRevision,
    *,
    recorded_remote_path: str | None = None,
) -> ReplicationResult:
    if not destination.enabled:
        raise DestinationError("DESTINATION_DISABLED", "The FTP destination is disabled.")

    artifacts = tuple(revision.artifacts.all())
    if not artifacts:
        raise DestinationError("NO_ARTIFACTS", "The revision contains no backup artifacts.")

    if recorded_remote_path:
        revision_path = str(recorded_remote_path)
        _validate_recorded_revision_path(
            destination,
            revision,
            revision_path,
            error_code="REPLICATION_PATH_INVALID",
        )
    else:
        revision_path = ftp_revision_destination_path(
            destination.base_path,
            device_name=revision.target.device.name,
            device_id=revision.target.device_id,
            created_at=revision.created,
            revision_id=revision.pk,
        )
    readable_names = _uses_readable_ftp_layout(revision_path)
    readable_stem = _readable_stem_from_revision_path(revision_path, revision)
    _layout, recorded_device_directory = _validate_recorded_revision_path(
        destination,
        revision,
        revision_path,
        error_code="REPLICATION_PATH_INVALID",
    )
    storage = build_config_storage()
    ftp = _connect(destination)
    transferred = 0
    expected_files = _expected_revision_files(
        revision,
        readable_names=readable_names,
        readable_stem=readable_stem,
        manifest_device_directory=recorded_device_directory,
    )
    expected_by_type = {
        item.artifact_type: item for item in expected_files if item.artifact_type != "manifest"
    }
    ftp_phase = "directory"
    try:
        _mkdirs(ftp, revision_path)
        ftp_phase = "artifact"
        for artifact in artifacts:
            if artifact.size > destination.max_artifact_size:
                raise DestinationError(
                    "ARTIFACT_TOO_LARGE",
                    "An artifact exceeds the configured FTP destination size limit.",
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
            filename = expected.filename
            remote_file = _join(revision_path, filename)
            if _put_immutable(ftp, remote_file, content, digest):
                transferred += len(content)

        ftp_phase = "manifest"
        manifest = _build_manifest(
            revision,
            artifacts,
            readable_names=readable_names,
            readable_stem=readable_stem,
            device_directory_override=recorded_device_directory,
        )
        manifest_path = _join(revision_path, "_netbox_manifest.json")
        manifest_exists = _remote_exists(ftp, manifest_path)
        if manifest_exists:
            try:
                existing_manifest = _read_remote(ftp, manifest_path, _MAX_MANIFEST_BYTES)
            except DestinationError as exc:
                raise DestinationError(
                    "DESTINATION_CONFLICT",
                    "The immutable FTP revision manifest could not be verified.",
                ) from exc
            if not _manifest_matches_revision(
                existing_manifest,
                revision=revision,
                revision_path=revision_path,
                expected_files=expected_files,
            ):
                raise DestinationError(
                    "DESTINATION_CONFLICT",
                    "A different manifest exists at the immutable FTP revision path.",
                )
        elif _put_immutable(
            ftp,
            manifest_path,
            manifest,
            hashlib.sha256(manifest).hexdigest(),
        ):
            transferred += len(manifest)
    except DestinationError:
        raise
    except ftplib.error_perm as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The FTP account cannot write to the configured destination path.",
        ) from exc
    except ftplib.all_errors as exc:
        phase_errors = {
            "directory": (
                "DESTINATION_DIRECTORY_FAILED",
                "The FTP revision directory could not be prepared.",
            ),
            "artifact": (
                "DESTINATION_ARTIFACT_FAILED",
                "An FTP revision artifact operation failed.",
            ),
            "manifest": (
                "DESTINATION_MANIFEST_FAILED",
                "The FTP revision manifest operation failed.",
            ),
        }
        error_code, safe_message = phase_errors[ftp_phase]
        raise DestinationError(
            error_code,
            safe_message,
        ) from exc
    finally:
        _close(ftp)
    return ReplicationResult(
        remote_path=revision_path,
        bytes_transferred=transferred,
        artifact_count=len(artifacts),
    )


def delete_revision_replica_ftp(replica: RevisionReplica) -> DeletedFtpRevisionResult:
    """Delete exactly one known FTP revision copy and nothing else.

    FTP has no transactional delete operation.  This primitive is therefore
    deliberately idempotent: an already missing expected file or revision
    directory is treated as success, while an unknown directory entry aborts
    the operation.  The caller owns all database state transitions and retry
    bookkeeping.
    """

    destination = replica.destination
    revision = replica.revision
    if destination.protocol != "ftp":
        raise DestinationError(
            "DELETE_PROTOCOL_UNSUPPORTED",
            "Revision deletion is available only for FTP destinations.",
        )
    if not destination.enabled:
        raise DestinationError(
            "DESTINATION_DISABLED",
            "The FTP destination is disabled and no remote file was deleted.",
        )
    if revision is None:
        raise DestinationError(
            "DELETE_REVISION_UNAVAILABLE",
            "The local revision metadata required for safe FTP deletion is unavailable.",
        )
    if replica.status in {
        ReplicaStatusChoices.QUEUED,
        ReplicaStatusChoices.RUNNING,
    }:
        raise DestinationError(
            "DELETE_REPLICA_BUSY",
            "An active FTP revision transfer cannot be deleted safely.",
        )
    if replica.remote_deleted_at is not None:
        raise DestinationError(
            "DELETE_REPLICA_EXPIRED",
            "The FTP revision copy has already expired.",
        )

    revision_path = _validated_replica_delete_path(replica)
    expected_files = _expected_revision_files(
        revision,
        readable_names=_uses_readable_ftp_layout(revision_path),
        readable_stem=_readable_stem_from_revision_path(revision_path, revision),
    )
    expected_by_name = {item.filename: item for item in expected_files}
    if len(expected_by_name) != len(expected_files):
        raise DestinationError(
            "DELETE_FILESET_INVALID",
            "The FTP revision file set contains duplicate filenames.",
        )
    for filename in expected_by_name:
        _validate_direct_filename(filename)
    temporary_names = {f"{filename}.part" for filename in expected_by_name}
    for filename in temporary_names:
        _validate_direct_filename(filename)
    allowed_entries = set(expected_by_name) | temporary_names

    ftp = _connect(destination)
    deleted_count = 0
    missing_count = 0
    deleted_bytes = 0
    try:
        entries = _list_revision_directory(ftp, revision_path, missing_ok=True)
        if entries is None:
            return DeletedFtpRevisionResult(
                remote_path=revision_path,
                expected_file_count=len(expected_files),
                deleted_file_count=0,
                missing_file_count=len(expected_files),
                deleted_bytes=0,
                directory_removed=False,
                already_absent=True,
            )
        _reject_unknown_revision_entries(entries, allowed_entries)

        # A worker process can stop after writing a deterministic temporary
        # object but before its atomic rename. These names are derived only
        # from the exact expected file set, so they can be removed without
        # broad wildcard deletion or touching an operator-created file.
        for temporary_name in sorted(entries & temporary_names):
            temporary_file = _join(revision_path, temporary_name)
            try:
                ftp.delete(temporary_file)
            except ftplib.error_perm as exc:
                after_error = _list_revision_directory(ftp, revision_path, missing_ok=True)
                if after_error is not None and temporary_name in after_error:
                    raise DestinationError(
                        "DESTINATION_DELETE_DENIED",
                        "The FTP account could not delete an interrupted temporary file.",
                    ) from exc
            after_delete = _list_revision_directory(ftp, revision_path, missing_ok=True)
            if after_delete is not None:
                _reject_unknown_revision_entries(after_delete, allowed_entries)
                if temporary_name in after_delete:
                    raise DestinationError(
                        "DESTINATION_DELETE_FAILED",
                        "An interrupted FTP temporary file still exists after deletion.",
                    )

        # The manifest is intentionally removed last.  If the worker stops in
        # the middle, the remaining manifest still identifies the incomplete
        # remote copy for an integrity audit or a retry.
        ordered_files = sorted(
            expected_files,
            key=lambda item: item.filename == "_netbox_manifest.json",
        )
        for expected in ordered_files:
            entries = _list_revision_directory(ftp, revision_path, missing_ok=True)
            if entries is None or expected.filename not in entries:
                missing_count += 1
                continue
            _reject_unknown_revision_entries(entries, set(expected_by_name))
            remote_file = _join(revision_path, expected.filename)
            try:
                ftp.delete(remote_file)
            except ftplib.error_perm as exc:
                after_error = _list_revision_directory(ftp, revision_path, missing_ok=True)
                if after_error is not None and expected.filename in after_error:
                    raise DestinationError(
                        "DESTINATION_DELETE_DENIED",
                        "The FTP account could not delete an expected revision file.",
                    ) from exc
            after_delete = _list_revision_directory(ftp, revision_path, missing_ok=True)
            if after_delete is not None:
                _reject_unknown_revision_entries(after_delete, set(expected_by_name))
                if expected.filename in after_delete:
                    raise DestinationError(
                        "DESTINATION_DELETE_FAILED",
                        "An expected FTP revision file still exists after deletion.",
                    )
            deleted_count += 1
            deleted_bytes += expected.size

        remaining = _list_revision_directory(ftp, revision_path, missing_ok=True)
        if remaining is None:
            directory_removed = True
        else:
            _reject_unknown_revision_entries(remaining, set())
            if remaining:
                raise DestinationError(
                    "DESTINATION_DELETE_INCOMPLETE",
                    "The FTP revision directory is not empty after deleting expected files.",
                )
            try:
                # Do not ask an FTP server to remove the process' current
                # working directory; implementations disagree on that case.
                ftp.cwd("/")
                ftp.rmd(revision_path)
            except ftplib.error_perm as exc:
                after_rmd = _list_revision_directory(ftp, revision_path, missing_ok=True)
                if after_rmd is not None:
                    raise DestinationError(
                        "DESTINATION_DIRECTORY_DELETE_FAILED",
                        "The empty FTP revision directory could not be removed.",
                    ) from exc
            directory_removed = (
                _list_revision_directory(ftp, revision_path, missing_ok=True) is None
            )
            if not directory_removed:
                raise DestinationError(
                    "DESTINATION_DIRECTORY_DELETE_FAILED",
                    "The FTP revision directory still exists after deletion.",
                )
    except DestinationError:
        raise
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_DELETE_FAILED",
            "The FTP revision copy could not be deleted safely.",
        ) from exc
    finally:
        _close(ftp)

    return DeletedFtpRevisionResult(
        remote_path=revision_path,
        expected_file_count=len(expected_files),
        deleted_file_count=deleted_count,
        missing_file_count=missing_count,
        deleted_bytes=deleted_bytes,
        directory_removed=directory_removed,
        already_absent=False,
    )


def write_verified_ftp_replica_to_archive(
    replica: RevisionReplica,
    archive: zipfile.ZipFile,
    *,
    archive_prefix: str,
    max_total_bytes: int,
) -> VerifiedFtpDownloadResult:
    """Stream one successful FTP replica into a ZIP after strict verification.

    This function deliberately exposes no FTP write operation. Every expected
    artifact must match the database-recorded size and SHA256. The manifest is
    validated structurally against immutable revision metadata so historical
    copies remain recoverable after a NetBox device rename.
    """

    destination = replica.destination
    revision = replica.revision
    if destination.protocol != "ftp":
        raise DestinationError(
            "RECOVERY_PROTOCOL_UNSUPPORTED",
            "A verified recovery package can currently be prepared only from FTP.",
        )
    if replica.status != ReplicaStatusChoices.SUCCESS:
        raise DestinationError(
            "RECOVERY_REPLICA_NOT_READY",
            "The selected FTP revision copy has not completed successfully.",
        )
    if not replica.remote_available or replica.remote_deleted_at is not None:
        raise DestinationError(
            "RECOVERY_REPLICA_EXPIRED",
            "The selected FTP revision copy has expired and is no longer available.",
        )
    if PurePosixPath(archive_prefix).is_absolute() or any(
        part in {"", ".", ".."} for part in PurePosixPath(archive_prefix).parts
    ):
        raise DestinationError(
            "RECOVERY_ARCHIVE_PATH_INVALID",
            "The recovery archive path could not be generated safely.",
        )

    # Recovery must use the immutable recorded path for the same rename-safety
    # reason as audit, repair, and retention.
    revision_path = str(replica.remote_path or "")
    _validate_recorded_revision_path(
        destination,
        revision,
        revision_path,
        error_code="RECOVERY_REMOTE_PATH_INVALID",
    )
    expected_files = _expected_revision_files(
        revision,
        readable_names=_uses_readable_ftp_layout(revision_path),
        readable_stem=_readable_stem_from_revision_path(revision_path, revision),
    )
    total_expected = sum(item.size for item in expected_files if item.artifact_type != "manifest")
    if max_total_bytes <= 0 or total_expected > max_total_bytes:
        raise DestinationError(
            "RECOVERY_PACKAGE_TOO_LARGE",
            "The FTP revision copy exceeds the configured recovery package size limit.",
        )
    ftp = _connect(destination)
    verified_bytes = 0
    try:
        for expected in expected_files:
            if PurePosixPath(expected.filename).name != expected.filename or expected.filename in {
                "",
                ".",
                "..",
            }:
                raise DestinationError(
                    "RECOVERY_ARCHIVE_PATH_INVALID",
                    "An FTP artifact filename could not be archived safely.",
                )
            remote_path = _join(revision_path, expected.filename)
            member_name = posixpath.join(archive_prefix, expected.filename)
            if expected.artifact_type == "manifest":
                remaining_bytes = max_total_bytes - verified_bytes
                if remaining_bytes <= 0:
                    raise DestinationError(
                        "RECOVERY_PACKAGE_TOO_LARGE",
                        "The FTP revision copy exceeds the configured recovery package size limit.",
                    )
                try:
                    content = _read_remote(
                        ftp,
                        remote_path,
                        min(_MAX_MANIFEST_BYTES, remaining_bytes),
                    )
                except ftplib.error_perm as exc:
                    error_code = (
                        "RECOVERY_FILE_MISSING"
                        if str(exc).lstrip().startswith("550")
                        else "RECOVERY_FILE_UNREADABLE"
                    )
                    raise DestinationError(
                        error_code,
                        "The FTP revision manifest could not be read.",
                    ) from exc
                except (ftplib.error_temp, DestinationError) as exc:
                    raise DestinationError(
                        "RECOVERY_FILE_UNREADABLE",
                        "The FTP revision manifest could not be read safely.",
                    ) from exc
                if not _manifest_matches_revision(
                    content,
                    revision=revision,
                    revision_path=revision_path,
                    expected_files=expected_files,
                ):
                    raise DestinationError(
                        "RECOVERY_HASH_MISMATCH",
                        "The FTP revision manifest does not match its revision metadata.",
                    )
                archive.writestr(member_name, content)
                verified_bytes += len(content)
                continue

            try:
                ftp.voidcmd("TYPE I")
                remote_size = ftp.size(remote_path)
            except ftplib.error_perm as exc:
                error_code = (
                    "RECOVERY_FILE_MISSING"
                    if str(exc).lstrip().startswith("550")
                    else "RECOVERY_FILE_UNREADABLE"
                )
                raise DestinationError(
                    error_code,
                    "An expected file in the FTP revision copy could not be read.",
                ) from exc
            except ftplib.error_temp as exc:
                raise DestinationError(
                    "RECOVERY_FILE_UNREADABLE",
                    "An expected file in the FTP revision copy could not be read.",
                ) from exc

            if remote_size != expected.size:
                raise DestinationError(
                    "RECOVERY_SIZE_MISMATCH",
                    "An FTP revision file no longer matches its recorded size.",
                )

            digest = hashlib.sha256()
            downloaded = 0
            with archive.open(member_name, mode="w", force_zip64=True) as member:

                def write_chunk(
                    chunk: bytes,
                    *,
                    expected_size: int = expected.size,
                    target_digest=digest,
                    archive_member=member,
                ) -> None:
                    nonlocal downloaded
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise DestinationError(
                            "RECOVERY_SIZE_MISMATCH",
                            "An FTP revision file changed while it was being downloaded.",
                        )
                    target_digest.update(chunk)
                    archive_member.write(chunk)

                try:
                    ftp.retrbinary(f"RETR {remote_path}", write_chunk)
                except DestinationError:
                    raise
                except ftplib.error_perm as exc:
                    error_code = (
                        "RECOVERY_FILE_MISSING"
                        if str(exc).lstrip().startswith("550")
                        else "RECOVERY_FILE_UNREADABLE"
                    )
                    raise DestinationError(
                        error_code,
                        "An expected file in the FTP revision copy could not be downloaded.",
                    ) from exc
                except ftplib.error_temp as exc:
                    raise DestinationError(
                        "RECOVERY_FILE_UNREADABLE",
                        "An expected file in the FTP revision copy could not be downloaded.",
                    ) from exc

            if downloaded != expected.size:
                raise DestinationError(
                    "RECOVERY_SIZE_MISMATCH",
                    "An FTP revision file was incomplete when downloaded.",
                )
            if digest.hexdigest() != expected.sha256:
                raise DestinationError(
                    "RECOVERY_HASH_MISMATCH",
                    "An FTP revision file failed SHA256 integrity verification.",
                )
            verified_bytes += downloaded
    except DestinationError:
        raise
    except ftplib.all_errors as exc:
        raise DestinationError(
            "RECOVERY_DOWNLOAD_FAILED",
            "The verified FTP recovery package could not be downloaded.",
        ) from exc
    finally:
        _close(ftp)

    return VerifiedFtpDownloadResult(
        file_count=len(expected_files),
        verified_bytes=verified_bytes,
        remote_path=revision_path,
    )


def _expected_revision_files(
    revision: ConfigRevision,
    *,
    readable_names: bool = False,
    readable_stem: str | None = None,
    manifest_device_directory: str | None = None,
) -> tuple[ExpectedRemoteFile, ...]:
    artifacts = tuple(revision.artifacts.all())
    expected: list[ExpectedRemoteFile] = []
    filenames: set[str] = set()
    for artifact in artifacts:
        filename = (
            _readable_artifact_filename(revision, artifact, stem_override=readable_stem)
            if readable_names
            else _artifact_filename(artifact.storage_key, artifact.artifact_type)
        )
        if filename in filenames:
            raise DestinationError(
                "ARTIFACT_NAME_CONFLICT",
                "Two revision artifacts resolve to the same FTP filename.",
            )
        filenames.add(filename)
        expected.append(
            ExpectedRemoteFile(
                filename=filename,
                size=artifact.size,
                sha256=artifact.raw_hash,
                artifact_type=artifact.artifact_type,
            )
        )

    manifest = _build_manifest(
        revision,
        artifacts,
        readable_names=readable_names,
        readable_stem=readable_stem,
        device_directory_override=manifest_device_directory,
    )
    expected.append(
        ExpectedRemoteFile(
            filename="_netbox_manifest.json",
            size=len(manifest),
            sha256=hashlib.sha256(manifest).hexdigest(),
            artifact_type="manifest",
        )
    )
    return tuple(expected)


def _build_manifest(
    revision: ConfigRevision,
    artifacts,
    *,
    readable_names: bool = False,
    readable_stem: str | None = None,
    device_directory_override: str | None = None,
) -> bytes:
    manifest_artifacts = [
        {
            "artifact_type": artifact.artifact_type,
            "format": artifact.format,
            "filename": (
                _readable_artifact_filename(
                    revision,
                    artifact,
                    stem_override=readable_stem,
                )
                if readable_names
                else _artifact_filename(artifact.storage_key, artifact.artifact_type)
            ),
            "size": artifact.size,
            "sha256": artifact.raw_hash,
            "primary": artifact.is_primary,
        }
        for artifact in artifacts
    ]
    return json.dumps(
        {
            "schema": 2 if readable_names else 1,
            "revision_uuid": str(revision.revision_uuid),
            "device_id": revision.target.device_id,
            "device_name": revision.target.device.name,
            "device_directory": device_directory_override
            or device_directory_name(revision.target.device.name, revision.target.device_id),
            "driver_id": revision.driver_id,
            "created": revision.created.isoformat(),
            "artifacts": manifest_artifacts,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _connect(destination: BackupDestination) -> ftplib.FTP:
    try:
        provider = secret_provider_registry.get(destination.credential_profile.provider_id)
        credentials = provider.resolve(destination.credential_profile.secret_reference)
    except (LookupError, SecretProviderError) as exc:
        raise DestinationError(
            "CREDENTIAL_RESOLUTION_FAILED", "The FTP credential could not be resolved."
        ) from exc
    if credentials.private_key or credentials.password is None:
        raise DestinationError(
            "CREDENTIAL_TYPE_UNSUPPORTED", "FTP requires a username and password."
        )

    ftp = ftplib.FTP(timeout=destination.connect_timeout)
    try:
        ftp.connect(destination.host, destination.port)
        ftp.login(credentials.username, credentials.password)
        ftp.set_pasv(True)
        ftp.voidcmd("TYPE I")
        return ftp
    except ftplib.error_perm as exc:
        _close(ftp)
        raise DestinationError("AUTH_FAILED", "FTP server authentication failed.") from exc
    except ConnectionRefusedError as exc:
        _close(ftp)
        raise DestinationError(
            "CONNECTION_REFUSED", "The FTP server rejected the TCP connection."
        ) from exc
    except TimeoutError as exc:
        _close(ftp)
        raise DestinationError("TIMEOUT", "The FTP connection timed out.") from exc
    except ftplib.all_errors as exc:
        _close(ftp)
        raise DestinationError("CONNECTION_FAILED", "The FTP connection failed.") from exc


def _mkdirs(ftp: ftplib.FTP, path: str) -> None:
    current = "/"
    for part in PurePosixPath(path).parts:
        if part in {"/", "", "."}:
            continue
        current = posixpath.join(current, part)
        try:
            ftp.cwd(current)
        except (ftplib.error_perm, ftplib.error_temp) as exc:
            # Some Windows FTP servers use a temporary 450 response for a
            # missing directory. Never create on the response code alone:
            # first prove from the accessible parent listing that the direct
            # child is actually absent, so permission errors are not hidden.
            if _is_denied_ftp_error(exc) or not _directory_absent_from_parent_listing(ftp, current):
                raise DestinationError(
                    "DESTINATION_PATH_UNREADABLE",
                    "The FTP destination directory could not be inspected safely.",
                ) from exc
            try:
                ftp.mkd(current)
            except ftplib.all_errors:
                # A few Windows servers create the directory but still return
                # a temporary/ambiguous response. The bounded CWD verification
                # below decides whether creation really succeeded.
                pass
            _cwd_after_mkdir(ftp, current)


def _cwd_after_mkdir(ftp: ftplib.FTP, path: str) -> None:
    last_error = None
    for delay in (0.0, 0.1, 0.25, 0.5, 1.0):
        if delay:
            time.sleep(delay)
        try:
            ftp.cwd(path)
            return
        except (ftplib.error_perm, ftplib.error_temp) as exc:
            if _is_denied_ftp_error(exc):
                raise DestinationError(
                    "DESTINATION_PATH_DENIED",
                    "The FTP account cannot enter the destination directory.",
                ) from exc
            last_error = exc
        except ftplib.all_errors as exc:
            raise DestinationError(
                "DESTINATION_PATH_FAILED",
                "The FTP connection failed while entering a new destination directory.",
            ) from exc
    raise DestinationError(
        "DESTINATION_PATH_TEMPORARY",
        "The FTP server did not make a newly created directory available in time.",
    ) from last_error


def _store(ftp: ftplib.FTP, path: str, content: bytes) -> None:
    try:
        ftp.storbinary(f"STOR {path}", io.BytesIO(content))
    except ftplib.error_temp as exc:
        raise DestinationError(
            "DESTINATION_UPLOAD_TEMPORARY",
            "The FTP server temporarily rejected the artifact upload.",
        ) from exc
    except ftplib.error_perm as exc:
        raise DestinationError(
            "DESTINATION_UPLOAD_DENIED",
            "The FTP server rejected the artifact upload.",
        ) from exc
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_UPLOAD_FAILED",
            "The FTP artifact upload failed.",
        ) from exc


def _read_remote(ftp: ftplib.FTP, remote_path: str, max_bytes: int) -> bytes:
    try:
        ftp.voidcmd("TYPE I")
        size = ftp.size(remote_path)
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED",
            "The uploaded FTP object could not be inspected.",
        ) from exc
    if size is None or size < 0 or size > max_bytes:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED", "The uploaded FTP object has an invalid size."
        )
    buffer = io.BytesIO()
    try:
        ftp.retrbinary(f"RETR {remote_path}", buffer.write)
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED",
            "The uploaded FTP object could not be read back for verification.",
        ) from exc
    content = buffer.getvalue()
    if len(content) != size:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED", "The uploaded FTP object is incomplete."
        )
    return content


def _verify_remote_file(
    ftp: ftplib.FTP,
    remote_path: str,
    expected: ExpectedRemoteFile,
    *,
    revision: ConfigRevision | None = None,
    revision_path: str = "",
    expected_files: tuple[ExpectedRemoteFile, ...] = (),
) -> tuple[str, int]:
    """Return an integrity state and byte count using read-only FTP commands."""

    if expected.artifact_type == "manifest" and revision is not None:
        return _verify_remote_manifest(
            ftp,
            remote_path,
            revision=revision,
            revision_path=revision_path,
            expected_files=expected_files,
        )

    ftp.voidcmd("TYPE I")
    try:
        size = ftp.size(remote_path)
    except ftplib.error_perm as exc:
        if str(exc).lstrip().startswith("550"):
            return "missing", 0
        return "unreadable", 0
    except ftplib.error_temp:
        return "unreadable", 0

    if size is None or size < 0:
        return "unreadable", 0
    if size != expected.size:
        return "size_mismatch", size

    digest = hashlib.sha256()
    downloaded = 0

    def update(chunk: bytes) -> None:
        nonlocal downloaded
        downloaded += len(chunk)
        digest.update(chunk)

    try:
        ftp.retrbinary(f"RETR {remote_path}", update)
    except ftplib.error_perm as exc:
        if str(exc).lstrip().startswith("550"):
            return "missing", 0
        return "unreadable", 0
    except ftplib.error_temp:
        return "unreadable", 0

    if downloaded != size:
        return "size_mismatch", downloaded
    if digest.hexdigest() != expected.sha256:
        return "hash_mismatch", downloaded
    return "ok", downloaded


def _verify_remote_manifest(
    ftp: ftplib.FTP,
    remote_path: str,
    *,
    revision: ConfigRevision,
    revision_path: str,
    expected_files: tuple[ExpectedRemoteFile, ...],
) -> tuple[str, int]:
    try:
        content = _read_remote(ftp, remote_path, _MAX_MANIFEST_BYTES)
    except ftplib.error_perm as exc:
        return ("missing", 0) if str(exc).lstrip().startswith("550") else ("unreadable", 0)
    except (ftplib.error_temp, DestinationError):
        return "unreadable", 0
    if not _manifest_matches_revision(
        content,
        revision=revision,
        revision_path=revision_path,
        expected_files=expected_files,
    ):
        return "hash_mismatch", len(content)
    return "ok", len(content)


def _manifest_matches_revision(
    content: bytes,
    *,
    revision: ConfigRevision,
    revision_path: str,
    expected_files: tuple[ExpectedRemoteFile, ...],
) -> bool:
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False

    layout = "backups" if _uses_readable_ftp_layout(revision_path) else "revisions"
    expected_schema = 2 if layout == "backups" else 1
    path_parts = PurePosixPath(revision_path).parts
    if len(path_parts) < 3:
        return False
    if len(path_parts) >= 4 and path_parts[-3] == "backups":
        device_segment = path_parts[-4]
    else:
        device_segment = path_parts[-3]
    if set(payload) != {
        "schema",
        "revision_uuid",
        "device_id",
        "device_name",
        "device_directory",
        "driver_id",
        "created",
        "artifacts",
    }:
        return False
    if not isinstance(payload.get("device_name"), str):
        return False
    if (
        payload.get("schema") != expected_schema
        or payload.get("revision_uuid") != str(revision.revision_uuid)
        or payload.get("device_id") != revision.target.device_id
        or payload.get("device_directory") != device_segment
        or payload.get("driver_id") != revision.driver_id
        or payload.get("created") != revision.created.isoformat()
    ):
        return False

    files_by_type = {
        expected.artifact_type: expected
        for expected in expected_files
        if expected.artifact_type != "manifest"
    }
    expected_artifacts = []
    for artifact in revision.artifacts.all():
        expected = files_by_type.get(artifact.artifact_type)
        if expected is None:
            return False
        expected_artifacts.append(
            {
                "artifact_type": artifact.artifact_type,
                "format": artifact.format,
                "filename": expected.filename,
                "size": artifact.size,
                "sha256": artifact.raw_hash,
                "primary": artifact.is_primary,
            }
        )
    return payload.get("artifacts") == expected_artifacts


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _put_immutable(ftp: ftplib.FTP, remote_path: str, content: bytes, digest: str) -> bool:
    if _remote_exists(ftp, remote_path):
        existing = _read_remote(ftp, remote_path, len(content))
    else:
        existing = None
    if existing is not None:
        if hashlib.sha256(existing).hexdigest() == digest:
            return False
        raise DestinationError(
            "DESTINATION_CONFLICT",
            "A different object already exists at the immutable FTP revision path.",
        )

    # Every revision has a unique immutable directory and the database state
    # permits only one active transfer for a replica. A deterministic short
    # suffix is therefore collision-free while remaining compatible with FTP
    # servers backed by Windows paths with conservative path-length limits.
    temporary = f"{remote_path}.part"
    try:
        _store(ftp, temporary, content)
        uploaded = _read_remote(ftp, temporary, len(content))
        if hashlib.sha256(uploaded).hexdigest() != digest:
            raise DestinationError(
                "DESTINATION_VERIFY_FAILED",
                "The FTP artifact hash did not match after upload.",
            )
        try:
            ftp.rename(temporary, remote_path)
        except ftplib.all_errors as exc:
            raise DestinationError(
                "DESTINATION_FINALIZE_FAILED",
                "The verified FTP artifact could not be finalized atomically.",
            ) from exc
    finally:
        try:
            ftp.delete(temporary)
        except ftplib.all_errors:
            pass
    return True


def _delete_verified(ftp: ftplib.FTP, path: str) -> None:
    ftp.delete(path)
    if not _remote_exists(ftp, path):
        return
    raise DestinationError(
        "DESTINATION_DELETE_FAILED", "The FTP test object still exists after deletion."
    )


def _remote_exists(ftp: ftplib.FTP, path: str) -> bool:
    """Prove whether an exact FTP path exists without hiding transient errors."""

    try:
        return bool(ftp.nlst(path))
    except ftplib.all_errors as exc:
        # FTP servers disagree on 450 versus 550 (and on the response text)
        # for a missing path. A parent listing is acceptable evidence for an
        # ambiguous response, but an explicit permission denial or a path
        # still present in that listing must stop the immutable write.
        if _is_missing_ftp_error(exc) or (
            not _is_denied_ftp_error(exc) and _directory_absent_from_parent_listing(ftp, path)
        ):
            return False
        raise DestinationError(
            "DESTINATION_PATH_UNREADABLE",
            "An existing FTP path could not be inspected safely.",
        ) from exc


def _artifact_filename(storage_key: str, artifact_type: str) -> str:
    filename = PurePosixPath(storage_key).name
    return filename if filename and filename not in {".", ".."} else f"{artifact_type}.bin"


def _readable_artifact_filename(
    revision: ConfigRevision,
    artifact,
    *,
    stem_override: str | None = None,
) -> str:
    original = _artifact_filename(artifact.storage_key, artifact.artifact_type)
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
        revision.target.device.name, revision.target.device_id, revision.created
    )
    if artifact.is_primary:
        return f"{stem}{suffix}"
    safe_type = device_directory_name(artifact.artifact_type, 0)[:48] or "artifact"
    return f"{stem}_{safe_type}{suffix}"


def _uses_readable_ftp_layout(remote_path: str) -> bool:
    parts = PurePosixPath(remote_path).parts
    return len(parts) >= 2 and (
        parts[-2] == "backups" or (len(parts) >= 3 and parts[-3] == "backups")
    )


def _validated_replica_delete_path(replica: RevisionReplica) -> str:
    destination = replica.destination
    revision = replica.revision
    remote_path = str(replica.remote_path or "")
    _validate_recorded_revision_path(
        destination,
        revision,
        remote_path,
        error_code="DELETE_PATH_INVALID",
    )
    return remote_path


def _validate_recorded_revision_path(
    destination: BackupDestination,
    revision: ConfigRevision,
    remote_path: str,
    *,
    error_code: str,
) -> tuple[str, str]:
    """Validate an immutable revision path without using the mutable device name."""

    remote_path = str(remote_path or "")
    base_path = str(destination.base_path or "").strip("/")
    canonical_base = f"/{base_path}" if base_path else "/"
    invalid = (
        not remote_path
        or not remote_path.startswith("/")
        or remote_path != posixpath.normpath(remote_path)
        or canonical_base != posixpath.normpath(canonical_base)
        or "\\" in remote_path
        or "\\" in base_path
        or "\x00" in remote_path
        or "\x00" in base_path
        or any(ord(character) < 32 for character in remote_path + base_path)
    )
    remote_parts = PurePosixPath(remote_path).parts
    base_parts = PurePosixPath(canonical_base).parts[1:]
    prefix = ("/", *base_parts, "devices")
    suffix = remote_parts[len(prefix) :]
    if invalid or remote_parts[: len(prefix)] != prefix or len(suffix) not in {3, 4}:
        raise DestinationError(
            error_code,
            "The recorded FTP revision path is not safe or does not match this revision.",
        )

    device_segment, layout = suffix[:2]
    if device_directory_name(device_segment, revision.target.device_id) != device_segment:
        raise DestinationError(
            error_code,
            "The recorded FTP revision path contains an invalid device directory.",
        )
    if layout == "revisions" and len(suffix) == 3:
        valid_leaf = suffix[2] == str(revision.revision_uuid)
    elif layout == "backups" and len(suffix) == 3:
        timestamp = backup_creation_timestamp(revision.created)
        # Current compact layout plus the historical timestamp-only layout.
        valid_leaf = suffix[2] in {timestamp, f"{timestamp}-r{revision.pk}"}
    elif layout == "backups" and len(suffix) == 4:
        valid_leaf = suffix[2] == backup_creation_timestamp(revision.created) and suffix[3] == str(
            revision.revision_uuid
        )
    else:
        raise DestinationError(
            error_code,
            "The recorded FTP revision path uses an unsupported layout.",
        )
    if not valid_leaf:
        raise DestinationError(
            error_code,
            "The recorded FTP revision path does not match this revision.",
        )
    return layout, device_segment


def _readable_stem_from_revision_path(
    revision_path: str,
    revision: ConfigRevision,
) -> str | None:
    parts = PurePosixPath(revision_path).parts
    if len(parts) >= 4 and parts[-3] == "backups":
        device_segment = parts[-4]
    elif len(parts) >= 3 and parts[-2] == "backups":
        device_segment = parts[-3]
    else:
        return None
    return backup_filename_stem(device_segment, revision.target.device_id, revision.created)


def _validate_direct_filename(filename: str) -> None:
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


def _list_revision_directory(
    ftp: ftplib.FTP,
    remote_path: str,
    *,
    missing_ok: bool,
) -> set[str] | None:
    try:
        ftp.cwd(remote_path)
    except ftplib.error_perm as exc:
        if missing_ok and (
            _is_missing_ftp_error(exc)
            or (
                not _is_denied_ftp_error(exc)
                and _directory_absent_from_parent_listing(ftp, remote_path)
            )
        ):
            return None
        raise DestinationError(
            "DESTINATION_DIRECTORY_UNREADABLE",
            "The FTP revision directory could not be inspected safely.",
        ) from exc

    try:
        listing = ftp.nlst()
    except ftplib.error_perm as exc:
        # Several FTP servers report an empty directory as a 550 response.
        if _is_missing_ftp_error(exc):
            return set()
        raise DestinationError(
            "DESTINATION_DIRECTORY_UNREADABLE",
            "The FTP revision directory could not be inspected safely.",
        ) from exc

    directory = PurePosixPath(remote_path)
    names: set[str] = set()
    for raw_entry in listing:
        entry = str(raw_entry)
        if (
            not entry
            or "\\" in entry
            or "\x00" in entry
            or any(ord(character) < 32 for character in entry)
        ):
            raise DestinationError(
                "DESTINATION_DIRECTORY_UNSAFE",
                "The FTP revision directory returned an unsafe entry.",
            )
        entry_path = PurePosixPath(entry)
        if entry_path.is_absolute():
            if entry_path.parent != directory:
                raise DestinationError(
                    "DESTINATION_DIRECTORY_UNSAFE",
                    "The FTP revision directory returned an unexpected path.",
                )
        elif len(entry_path.parts) != 1:
            raise DestinationError(
                "DESTINATION_DIRECTORY_UNSAFE",
                "The FTP revision directory returned a nested path.",
            )
        name = entry_path.name
        if name in {"", ".", ".."}:
            continue
        _validate_direct_filename(name)
        names.add(name)
    return names


def _directory_absent_from_parent_listing(ftp: ftplib.FTP, remote_path: str) -> bool:
    """Prove a generic FTP 550 means absent without trusting its message.

    Some Windows FTP servers answer only ``550 Requested action not taken``
    for a missing directory. Treating every generic 550 as absence would also
    hide permission failures. Instead, walk up to the nearest accessible
    ancestor and accept absence only when the next direct child is not present
    in that listing. This also handles a failed first upload which recorded the
    immutable revision path before any of its parent directories were created.
    """

    current = remote_path.rstrip("/")
    while current and current != "/":
        parent = posixpath.dirname(current) or "/"
        child = posixpath.basename(current)
        if not child:
            return False
        try:
            ftp.cwd(parent)
            listing = ftp.nlst()
        except ftplib.error_perm as exc:
            if _is_denied_ftp_error(exc):
                return False
            current = parent
            continue
        except ftplib.all_errors:
            return False

        directory = PurePosixPath(parent)
        names: set[str] = set()
        for raw_entry in listing:
            entry = str(raw_entry)
            if (
                not entry
                or "\\" in entry
                or "\x00" in entry
                or any(ord(character) < 32 for character in entry)
            ):
                return False
            entry_path = PurePosixPath(entry)
            if entry_path.is_absolute():
                if entry_path.parent != directory:
                    return False
            elif len(entry_path.parts) != 1:
                return False
            name = entry_path.name
            if name in {"", ".", ".."}:
                continue
            try:
                _validate_direct_filename(name)
            except DestinationError:
                return False
            names.add(name)
        return child not in names
    return False


def _reject_unknown_revision_entries(entries: set[str], expected: set[str]) -> None:
    if entries - expected:
        raise DestinationError(
            "DESTINATION_DELETE_CONFLICT",
            "The FTP revision directory contains an unknown file and was not deleted.",
        )


def _is_missing_ftp_error(exc: BaseException) -> bool:
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


def _is_denied_ftp_error(exc: BaseException) -> bool:
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


def _join(base: str, *parts: str) -> str:
    return posixpath.join(base.rstrip("/"), *parts)


def _absolute(path: str) -> str:
    return "/" + path.lstrip("/")


def _close(ftp: ftplib.FTP) -> None:
    if ftp.sock is None:
        return
    try:
        ftp.quit()
    except ftplib.all_errors:
        ftp.close()
