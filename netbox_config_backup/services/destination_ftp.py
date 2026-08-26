from __future__ import annotations

import ftplib
import hashlib
import io
import json
import posixpath
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
    backup_filename_stem,
    device_directory_name,
    ftp_revision_destination_path,
    revision_destination_path,
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


def reconcile_ftp_destination(
    destination: BackupDestination,
    *,
    replicas: Iterable[RevisionReplica] | None = None,
    issue_limit: int = 100,
) -> dict[str, object]:
    """Read and hash successful FTP replicas without changing remote state."""

    if replicas is None:
        replica_queryset = (
            destination.replicas.filter(status=ReplicaStatusChoices.SUCCESS)
            .select_related("revision__target__device")
            .prefetch_related("revision__artifacts")
            .order_by("pk")
        )
        replica_iterable = replica_queryset.iterator(chunk_size=100)
        skipped_replicas = destination.replicas.exclude(status=ReplicaStatusChoices.SUCCESS).count()
    else:
        replica_iterable = iter(replicas)
        skipped_replicas = 0

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

    ftp = _connect(destination)
    try:
        for replica in replica_iterable:
            revision = replica.revision
            revision_path = replica.remote_path or revision_destination_path(
                destination.base_path,
                device_name=revision.target.device.name,
                device_id=revision.target.device_id,
                revision_uuid=revision.revision_uuid,
            )
            expected_files = _expected_revision_files(
                revision,
                readable_names=_uses_readable_ftp_layout(revision_path),
            )
            summary["checked_replicas"] += 1
            replica_failed = False

            for expected in expected_files:
                summary["checked_files"] += 1
                remote_path = _join(revision_path, expected.filename)
                result, actual_size = _verify_remote_file(ftp, remote_path, expected)
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
) -> ReplicationResult:
    if not destination.enabled:
        raise DestinationError("DESTINATION_DISABLED", "The FTP destination is disabled.")

    storage = build_config_storage()
    artifacts = tuple(revision.artifacts.all())
    if not artifacts:
        raise DestinationError("NO_ARTIFACTS", "The revision contains no backup artifacts.")

    revision_path = ftp_revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        created_at=revision.created,
    )
    ftp = _connect(destination)
    transferred = 0
    expected_files = _expected_revision_files(revision, readable_names=True)
    expected_by_type = {
        item.artifact_type: item for item in expected_files if item.artifact_type != "manifest"
    }
    try:
        _mkdirs(ftp, revision_path)
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

        manifest = _build_manifest(revision, artifacts, readable_names=True)
        manifest_path = _join(revision_path, "_netbox_manifest.json")
        if _put_immutable(ftp, manifest_path, manifest, hashlib.sha256(manifest).hexdigest()):
            transferred += len(manifest)
    except DestinationError:
        raise
    except ftplib.error_perm as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The FTP account cannot write to the configured destination path.",
        ) from exc
    except ftplib.all_errors as exc:
        raise DestinationError(
            "DESTINATION_UPLOAD_FAILED", "The revision upload to FTP failed."
        ) from exc
    finally:
        _close(ftp)
    return ReplicationResult(
        remote_path=revision_path,
        bytes_transferred=transferred,
        artifact_count=len(artifacts),
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
    artifact and the generated NetBox manifest must match the database-recorded
    size and SHA256 before the caller may publish the archive.
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
    if PurePosixPath(archive_prefix).is_absolute() or any(
        part in {"", ".", ".."} for part in PurePosixPath(archive_prefix).parts
    ):
        raise DestinationError(
            "RECOVERY_ARCHIVE_PATH_INVALID",
            "The recovery archive path could not be generated safely.",
        )

    revision_path = replica.remote_path or revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        revision_uuid=revision.revision_uuid,
    )
    expected_files = _expected_revision_files(
        revision,
        readable_names=_uses_readable_ftp_layout(revision_path),
    )
    total_expected = sum(item.size for item in expected_files)
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
            member_name = posixpath.join(archive_prefix, expected.filename)
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
) -> tuple[ExpectedRemoteFile, ...]:
    artifacts = tuple(revision.artifacts.all())
    expected: list[ExpectedRemoteFile] = []
    filenames: set[str] = set()
    for artifact in artifacts:
        filename = (
            _readable_artifact_filename(revision, artifact)
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

    manifest = _build_manifest(revision, artifacts, readable_names=readable_names)
    expected.append(
        ExpectedRemoteFile(
            filename="_netbox_manifest.json",
            size=len(manifest),
            sha256=hashlib.sha256(manifest).hexdigest(),
            artifact_type="manifest",
        )
    )
    return tuple(expected)


def _build_manifest(revision: ConfigRevision, artifacts, *, readable_names: bool = False) -> bytes:
    manifest_artifacts = [
        {
            "artifact_type": artifact.artifact_type,
            "format": artifact.format,
            "filename": (
                _readable_artifact_filename(revision, artifact)
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
            "device_directory": device_directory_name(
                revision.target.device.name, revision.target.device_id
            ),
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
        except ftplib.error_perm:
            ftp.mkd(current)
            ftp.cwd(current)


def _store(ftp: ftplib.FTP, path: str, content: bytes) -> None:
    ftp.storbinary(f"STOR {path}", io.BytesIO(content))


def _read_remote(ftp: ftplib.FTP, remote_path: str, max_bytes: int) -> bytes:
    ftp.voidcmd("TYPE I")
    size = ftp.size(remote_path)
    if size is None or size < 0 or size > max_bytes:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED", "The uploaded FTP object has an invalid size."
        )
    buffer = io.BytesIO()
    ftp.retrbinary(f"RETR {remote_path}", buffer.write)
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
) -> tuple[str, int]:
    """Return an integrity state and byte count using read-only FTP commands."""

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

    temporary = f"{remote_path}.part-{uuid.uuid4().hex}"
    try:
        _store(ftp, temporary, content)
        uploaded = _read_remote(ftp, temporary, len(content))
        if hashlib.sha256(uploaded).hexdigest() != digest:
            raise DestinationError(
                "DESTINATION_VERIFY_FAILED",
                "The FTP artifact hash did not match after upload.",
            )
        ftp.rename(temporary, remote_path)
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
    """Use NLST because FTP servers disagree on 450 versus 550 for missing SIZE."""

    try:
        return bool(ftp.nlst(path))
    except (ftplib.error_perm, ftplib.error_temp):
        return False


def _artifact_filename(storage_key: str, artifact_type: str) -> str:
    filename = PurePosixPath(storage_key).name
    return filename if filename and filename not in {".", ".."} else f"{artifact_type}.bin"


def _readable_artifact_filename(revision: ConfigRevision, artifact) -> str:
    original = _artifact_filename(artifact.storage_key, artifact.artifact_type)
    suffix = "".join(PurePosixPath(original).suffixes)
    if not suffix or len(suffix) > 24 or any(
        character not in ".abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        for character in suffix
    ):
        suffix = ".bin"
    stem = backup_filename_stem(
        revision.target.device.name,
        revision.target.device_id,
        revision.created,
    )
    if artifact.is_primary:
        return f"{stem}{suffix}"
    safe_type = device_directory_name(artifact.artifact_type, 0)[:48] or "artifact"
    return f"{stem}_{safe_type}{suffix}"


def _uses_readable_ftp_layout(remote_path: str) -> bool:
    parts = PurePosixPath(remote_path).parts
    return len(parts) >= 2 and parts[-2] == "backups"


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
