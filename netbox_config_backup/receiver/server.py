from __future__ import annotations

import asyncio
import hmac
import logging
import os
import posixpath
import re
from pathlib import Path

import asyncssh

from netbox_config_backup.credentials.base import CredentialMaterial

SAFE_RECEIVER_USERNAME = re.compile(r"^[A-Za-z0-9._@%-]{1,64}$")
SAFE_RECEIVER_PASSWORD = re.compile(r"^[A-Za-z0-9._@%+,:=-]{8,128}$")
logger = logging.getLogger("netbox_config_backup.receiver")


class PasswordOnlySSHServer(asyncssh.SSHServer):
    def __init__(self, credentials: CredentialMaterial) -> None:
        self._credentials = credentials

    def connection_made(self, conn) -> None:
        peer = conn.get_extra_info("peername")
        logger.info("Receiver SSH connection opened from %s.", peer[0] if peer else "unknown")

    def connection_lost(self, exc) -> None:
        if exc:
            logger.debug("Receiver SSH connection closed with %s.", exc)

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return False

    def validate_password(self, username: str, password: str) -> bool:
        expected_password = self._credentials.password or ""
        valid = hmac.compare_digest(username, self._credentials.username) and hmac.compare_digest(
            password, expected_password
        )
        logger.info("Receiver password authentication %s.", "succeeded" if valid else "failed")
        return valid


class UploadOnlySFTPServer(asyncssh.SFTPServer):
    """Chrooted SFTP service which permits regular file uploads but no links."""

    def open(self, path: bytes, pflags: int, attrs):
        if not pflags & asyncssh.FXF_WRITE or pflags & asyncssh.FXF_READ:
            raise asyncssh.SFTPPermissionDenied("This endpoint accepts uploads only.")
        logger.info("Receiver accepted an SFTP file-open request.")
        return super().open(path, pflags, attrs)

    def map_path(self, path: bytes) -> bytes:
        # CeraOS/libssh2 can prefix an absolute upload path with two slashes.
        # POSIX intentionally preserves exactly two leading slashes, which would
        # otherwise make AsyncSSH drop the configured chroot while joining paths.
        # Collapse all leading separators before delegating to the safe chroot map.
        normalized = b"/" + path.lstrip(b"/")
        normalized = posixpath.normpath(normalized)
        return super().map_path(normalized)

    def symlink(self, oldpath: bytes, newpath: bytes):
        raise asyncssh.SFTPPermissionDenied("Symbolic links are disabled.")

    def link(self, oldpath: bytes, newpath: bytes):
        raise asyncssh.SFTPPermissionDenied("Hard links are disabled.")


def validate_receiver_credentials(credentials: CredentialMaterial) -> None:
    if not SAFE_RECEIVER_USERNAME.fullmatch(credentials.username):
        raise ValueError(
            "Receiver username must contain only letters, numbers, dot, underscore, @, %, or -."
        )
    if not credentials.password or not SAFE_RECEIVER_PASSWORD.fullmatch(credentials.password):
        raise ValueError(
            "Receiver password must be 8-128 characters and use the documented CeraOS-safe set."
        )
    if credentials.private_key:
        raise ValueError("The built-in receiver supports password authentication only.")


def ensure_host_key(path: str | Path, *, algorithm: str = "ssh-ed25519") -> Path:
    key_path = Path(path).expanduser()
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not key_path.exists():
        key = (
            asyncssh.generate_private_key(algorithm, key_size=3072)
            if algorithm == "ssh-rsa"
            else asyncssh.generate_private_key(algorithm)
        )
        temporary = key_path.with_name(f".{key_path.name}.tmp")
        temporary.write_bytes(key.export_private_key())
        os.chmod(temporary, 0o600)
        temporary.replace(key_path)
    if key_path.is_symlink() or not key_path.is_file():
        raise ValueError("Receiver host key must be a regular file, not a symbolic link.")
    os.chmod(key_path, 0o600)
    return key_path


def prepare_receiver_root(profile_root: Path, upload_directory: str) -> Path:
    profile_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if profile_root.is_symlink():
        raise ValueError("Receiver profile root must not be a symbolic link.")
    inbox = profile_root / upload_directory
    inbox.mkdir(parents=False, exist_ok=True, mode=0o700)
    if inbox.is_symlink() or not inbox.is_dir():
        raise ValueError("Receiver inbox must be a regular directory without symbolic links.")
    for entry in profile_root.iterdir():
        if entry.is_symlink():
            raise ValueError("Receiver profile root must not contain symbolic links.")
    return inbox


async def serve_sftp_receiver(
    *,
    listen_host: str,
    listen_port: int,
    profile_root: Path,
    upload_directory: str,
    host_key_paths: tuple[str | Path, ...],
    credentials: CredentialMaterial,
) -> None:
    validate_receiver_credentials(credentials)
    prepare_receiver_root(profile_root, upload_directory)
    host_keys = [
        ensure_host_key(path, algorithm="ssh-rsa" if "rsa" in str(path).lower() else "ssh-ed25519")
        for path in host_key_paths
    ]
    listener = await asyncssh.listen(
        listen_host,
        listen_port,
        server_factory=lambda: PasswordOnlySSHServer(credentials),
        server_host_keys=[str(path) for path in host_keys],
        error_handler=lambda _conn, exc: (
            logger.debug("Receiver SSH protocol closed: %r", exc) if exc else None
        ),
        sftp_factory=lambda channel: UploadOnlySFTPServer(
            channel,
            chroot=os.fsencode(profile_root),
        ),
    )
    await listener.wait_closed()


def run_sftp_receiver(**kwargs) -> None:
    asyncio.run(serve_sftp_receiver(**kwargs))
