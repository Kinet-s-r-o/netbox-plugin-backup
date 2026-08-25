from __future__ import annotations

import base64
import hashlib
import io
import json
import posixpath
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

import paramiko
from paramiko import RejectPolicy, SSHClient
from paramiko.ssh_exception import AuthenticationException, BadHostKeyException, SSHException

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.drivers.base import DriverError
from netbox_config_backup.models import BackupDestination, ConfigRevision
from netbox_config_backup.storage import build_config_storage
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.transports.netmiko import (
    SSH_DISABLED_ALGORITHMS,
    _parse_private_key,
)

from .destination_paths import device_directory_name, revision_destination_path
from .destination_types import DestinationError, ReplicationResult


@dataclass(frozen=True, slots=True)
class DestinationHostKeyCandidate:
    host: str
    port: int
    key_type: str
    public_key: str
    fingerprint_sha256: str
    fingerprint_md5: str

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "key_type": self.key_type,
            "public_key": self.public_key,
            "fingerprint_sha256": self.fingerprint_sha256,
            "fingerprint_md5": self.fingerprint_md5,
        }


def scan_destination_host_key(destination: BackupDestination) -> DestinationHostKeyCandidate:
    sock = None
    transport = None
    try:
        sock = socket.create_connection(
            (destination.host, destination.port), timeout=destination.connect_timeout
        )
        sock.settimeout(destination.connect_timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=destination.connect_timeout)
        key = transport.get_remote_server_key()
        if key is None:
            raise DestinationError(
                "HOST_KEY_SCAN_FAILED", "The SFTP server did not present an SSH host key."
            )
        public_key = key.get_base64()
        raw = base64.b64decode(public_key, validate=True)
        sha256 = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
        md5 = ":".join(f"{byte:02x}" for byte in hashlib.md5(raw, usedforsecurity=False).digest())
        return DestinationHostKeyCandidate(
            host=destination.host,
            port=destination.port,
            key_type=key.get_name(),
            public_key=public_key,
            fingerprint_sha256=f"SHA256:{sha256}",
            fingerprint_md5=f"MD5:{md5}",
        )
    except DestinationError:
        raise
    except ConnectionRefusedError as exc:
        raise DestinationError(
            "CONNECTION_REFUSED", "The SFTP server rejected the TCP connection."
        ) from exc
    except TimeoutError as exc:
        raise DestinationError(
            "TIMEOUT", "The SFTP server did not respond before the connection timeout."
        ) from exc
    except (OSError, SSHException, ValueError) as exc:
        raise DestinationError(
            "HOST_KEY_SCAN_FAILED", "The SFTP server identity could not be scanned safely."
        ) from exc
    finally:
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()


def test_destination(destination: BackupDestination) -> dict[str, object]:
    candidate = scan_destination_host_key(destination)
    if not destination.host_key_is_trusted:
        raise DestinationError(
            "HOST_KEY_UNKNOWN",
            "Verify and approve the SFTP server fingerprint before the first connection.",
        )
    if candidate.fingerprint_sha256 != destination.host_key_fingerprint_sha256:
        raise DestinationError(
            "HOST_KEY_MISMATCH",
            "The SFTP server host key changed and no connection was attempted.",
        )

    client = _connect(destination)
    test_name = f".netbox-config-backup-test-{uuid.uuid4().hex}"
    remote_path = _join(destination.base_path, test_name)
    payload = b"netbox-config-backup-sftp-test\n"
    try:
        with client.open_sftp() as sftp:
            _mkdirs(sftp, destination.base_path)
            sftp.putfo(io.BytesIO(payload), remote_path, confirm=True)
            downloaded = _read_remote(sftp, remote_path, len(payload))
            if downloaded != payload:
                raise DestinationError(
                    "DESTINATION_VERIFY_FAILED",
                    "The SFTP test object could not be verified after upload.",
                )
            try:
                sftp.remove(remote_path)
                sftp.stat(remote_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise DestinationError(
                    "DESTINATION_DELETE_FAILED",
                    "The SFTP account cannot remove test objects from the destination path.",
                ) from exc
            else:
                raise DestinationError(
                    "DESTINATION_DELETE_FAILED",
                    "The SFTP test object still exists after deletion.",
                )
            return {
                "success": True,
                "safe_message": "SFTP authentication, upload, verification, and delete succeeded.",
                "host_key_candidate": candidate.as_dict(),
            }
    except DestinationError:
        raise
    except PermissionError as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The SFTP account cannot write to the configured destination path.",
        ) from exc
    except OSError as exc:
        raise DestinationError(
            "DESTINATION_TEST_FAILED", "The SFTP write verification failed."
        ) from exc
    finally:
        try:
            with client.open_sftp() as cleanup_sftp:
                cleanup_sftp.remove(remote_path)
        except (OSError, SSHException):
            pass
        client.close()


def replicate_revision(
    destination: BackupDestination,
    revision: ConfigRevision,
) -> ReplicationResult:
    if not destination.enabled:
        raise DestinationError("DESTINATION_DISABLED", "The SFTP destination is disabled.")
    if not destination.host_key_is_trusted:
        raise DestinationError(
            "HOST_KEY_UNKNOWN", "The SFTP server fingerprint has not been approved."
        )

    storage = build_config_storage()
    artifacts = tuple(revision.artifacts.all())
    if not artifacts:
        raise DestinationError("NO_ARTIFACTS", "The revision contains no backup artifacts.")

    revision_path = revision_destination_path(
        destination.base_path,
        device_name=revision.target.device.name,
        device_id=revision.target.device_id,
        revision_uuid=revision.revision_uuid,
    )
    client = _connect(destination)
    transferred = 0
    manifest_artifacts: list[dict[str, object]] = []
    filenames: set[str] = set()
    try:
        with client.open_sftp() as sftp:
            _mkdirs(sftp, revision_path)
            for artifact in artifacts:
                if artifact.size > destination.max_artifact_size:
                    raise DestinationError(
                        "ARTIFACT_TOO_LARGE",
                        "An artifact exceeds the configured SFTP destination size limit.",
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
                filename = _artifact_filename(artifact.storage_key, artifact.artifact_type)
                if filename in filenames:
                    raise DestinationError(
                        "ARTIFACT_NAME_CONFLICT",
                        "Two revision artifacts resolve to the same SFTP filename.",
                    )
                filenames.add(filename)
                remote_file = _join(revision_path, filename)
                if _put_immutable(sftp, remote_file, content, digest):
                    transferred += len(content)
                manifest_artifacts.append(
                    {
                        "artifact_type": artifact.artifact_type,
                        "format": artifact.format,
                        "filename": filename,
                        "size": artifact.size,
                        "sha256": artifact.raw_hash,
                        "primary": artifact.is_primary,
                    }
                )

            manifest = json.dumps(
                {
                    "schema": 1,
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
            manifest_path = _join(revision_path, "_netbox_manifest.json")
            if _put_immutable(sftp, manifest_path, manifest, hashlib.sha256(manifest).hexdigest()):
                transferred += len(manifest)
    except DestinationError:
        raise
    except PermissionError as exc:
        raise DestinationError(
            "DESTINATION_PATH_DENIED",
            "The SFTP account cannot write to the configured destination path.",
        ) from exc
    except OSError as exc:
        raise DestinationError(
            "DESTINATION_UPLOAD_FAILED", "The revision upload to SFTP failed."
        ) from exc
    finally:
        client.close()
    return ReplicationResult(
        remote_path=revision_path,
        bytes_transferred=transferred,
        artifact_count=len(artifacts),
    )


def _connect(destination: BackupDestination) -> SSHClient:
    try:
        provider = secret_provider_registry.get(destination.credential_profile.provider_id)
        credentials = provider.resolve(destination.credential_profile.secret_reference)
    except (LookupError, SecretProviderError) as exc:
        raise DestinationError(
            "CREDENTIAL_RESOLUTION_FAILED", "The SFTP credential could not be resolved."
        ) from exc

    client = SSHClient()
    known_hosts_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", prefix="ncb-sftp-known-hosts-", delete=False
        ) as handle:
            known_hosts_path = handle.name
            handle.write(destination.known_hosts_line + "\n")
        client.load_host_keys(known_hosts_path)
        client.set_missing_host_key_policy(RejectPolicy())
        parameters = {
            "hostname": destination.host,
            "port": destination.port,
            "username": credentials.username,
            "timeout": destination.connect_timeout,
            "auth_timeout": destination.connect_timeout,
            "banner_timeout": destination.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
            "disabled_algorithms": SSH_DISABLED_ALGORITHMS,
        }
        if credentials.private_key:
            parameters["pkey"] = _parse_private_key(credentials.private_key)
        else:
            parameters["password"] = credentials.password or ""
        client.connect(**parameters)
        return client
    except AuthenticationException as exc:
        client.close()
        raise DestinationError("AUTH_FAILED", "SFTP server authentication failed.") from exc
    except BadHostKeyException as exc:
        client.close()
        raise DestinationError(
            "HOST_KEY_MISMATCH", "The SFTP server host key does not match the approved key."
        ) from exc
    except TimeoutError as exc:
        client.close()
        raise DestinationError("TIMEOUT", "The SFTP connection timed out.") from exc
    except DriverError as exc:
        client.close()
        raise DestinationError(exc.error_code, exc.safe_message) from exc
    except (OSError, SSHException) as exc:
        client.close()
        raise DestinationError("CONNECTION_FAILED", "The SFTP server connection failed.") from exc
    finally:
        if known_hosts_path:
            try:
                import os

                os.unlink(known_hosts_path)
            except OSError:
                pass


def _join(base: str, *parts: str) -> str:
    return posixpath.join(base.rstrip("/"), *parts)


def _mkdirs(sftp, path: str) -> None:
    pure = PurePosixPath(path)
    current = "/" if pure.is_absolute() else ""
    for part in pure.parts:
        if part in {"/", "", "."}:
            continue
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def _artifact_filename(storage_key: str, artifact_type: str) -> str:
    filename = PurePosixPath(storage_key).name
    if not filename or filename in {".", ".."}:
        filename = f"{artifact_type}.bin"
    return filename


def _read_remote(sftp, remote_path: str, max_bytes: int) -> bytes:
    size = sftp.stat(remote_path).st_size
    if size < 0 or size > max_bytes:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED", "The uploaded SFTP object has an invalid size."
        )
    buffer = io.BytesIO()
    sftp.getfo(remote_path, buffer)
    content = buffer.getvalue()
    if len(content) != size:
        raise DestinationError(
            "DESTINATION_VERIFY_FAILED", "The uploaded SFTP object is incomplete."
        )
    return content


def _put_immutable(sftp, remote_path: str, content: bytes, digest: str) -> bool:
    try:
        existing = _read_remote(sftp, remote_path, len(content))
    except OSError:
        existing = None
    if existing is not None:
        if hashlib.sha256(existing).hexdigest() == digest:
            return False
        raise DestinationError(
            "DESTINATION_CONFLICT",
            "A different object already exists at the immutable SFTP revision path.",
        )

    temporary = f"{remote_path}.part-{uuid.uuid4().hex}"
    try:
        sftp.putfo(io.BytesIO(content), temporary, confirm=True)
        uploaded = _read_remote(sftp, temporary, len(content))
        if hashlib.sha256(uploaded).hexdigest() != digest:
            raise DestinationError(
                "DESTINATION_VERIFY_FAILED",
                "The SFTP artifact hash did not match after upload.",
            )
        sftp.rename(temporary, remote_path)
    finally:
        try:
            sftp.remove(temporary)
        except OSError:
            pass
    return True
