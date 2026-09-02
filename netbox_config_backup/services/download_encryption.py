from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePath
from shutil import copyfileobj
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

import pyzipper

from netbox_config_backup.credentials.encrypted_database import (
    DatabaseCredentialCipher,
    MasterKeyConfigurationError,
)


class DownloadEncryptionError(RuntimeError):
    """A protected download cannot be produced without exposing secret details."""


def download_zip_encryption_enabled() -> bool:
    from netbox_config_backup.models import OperationalSettings

    return bool(
        OperationalSettings.objects.filter(singleton=True).values_list(
            "download_zip_encryption_enabled", flat=True
        ).first()
    )


def resolve_download_zip_password() -> str | None:
    """Return the decrypted ZIP password, or None when protection is disabled.

    Protection fails closed: an enabled setting without usable secret material
    never falls back to returning a plaintext backup.
    """

    if not download_zip_encryption_enabled():
        return None

    from netbox_config_backup.models import DownloadEncryptionSecret

    secret = DownloadEncryptionSecret.objects.filter(singleton=True).first()
    if secret is None:
        raise DownloadEncryptionError("Protected downloads are temporarily unavailable.")
    try:
        return DatabaseCredentialCipher().decrypt(
            reference=secret.reference,
            ciphertext=bytes(secret.ciphertext),
            nonce=bytes(secret.nonce),
            key_version=secret.key_version,
        )
    except MasterKeyConfigurationError as exc:
        raise DownloadEncryptionError("Protected downloads are temporarily unavailable.") from exc


def build_password_protected_zip(
    *,
    content: bytes,
    member_filename: str,
    password: str,
) -> bytes:
    """Wrap one stored artifact in a WinZip AES-256 encrypted ZIP archive."""

    archive = build_password_protected_zip_stream(
        source=io.BytesIO(content),
        member_filename=member_filename,
        password=password,
    )
    try:
        return archive.read()
    finally:
        archive.close()


def build_password_protected_zip_stream(
    *,
    source: BinaryIO,
    member_filename: str,
    password: str,
) -> BinaryIO:
    """Stream one source into an AES ZIP backed by bounded memory and a temp file."""

    member_filename = _safe_member_filename(member_filename)
    if not password:
        raise DownloadEncryptionError("A protected download password is required.")
    # Ownership transfers to FileResponse; callers close the returned stream.
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115
    try:
        with pyzipper.AESZipFile(
            output,
            mode="w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(password.encode("utf-8"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            with archive.open(member_filename, mode="w") as encrypted_member:
                copyfileobj(source, encrypted_member, length=1024 * 1024)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise


def protected_zip_filename(member_filename: str) -> str:
    member_filename = _safe_member_filename(member_filename)
    path = PurePath(member_filename)
    if path.suffix.lower() == ".zip":
        return f"{path.stem}_protected.zip"
    return f"{path.stem}.zip"


def encrypt_zip_package_stream(
    *, source: BinaryIO, password: str, max_total_bytes: int
) -> BinaryIO:
    """Encrypt a verified plugin-generated ZIP without adding another ZIP layer.

    Copy entry bytes and paths, not recursively unpack native device archives.
    The caller must validate the source package's recorded hash before calling.
    The source remains open and unchanged; the caller owns the returned stream.
    """

    if not password:
        raise DownloadEncryptionError("A protected download password is required.")
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115
    try:
        source.seek(0)
        with zipfile.ZipFile(source) as original:
            members = original.infolist()
            if (
                not members
                or max_total_bytes <= 0
                or sum(member.file_size for member in members) > max_total_bytes
            ):
                raise DownloadEncryptionError("The recovery ZIP size is invalid.")
            names = set()
            for member in members:
                _validate_package_member(member)
                name = member.filename.rstrip("/").casefold()
                if name in names:
                    raise DownloadEncryptionError("The recovery ZIP contains duplicate paths.")
                names.add(name)

            with pyzipper.AESZipFile(
                output,
                mode="w",
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as encrypted:
                encrypted.setpassword(password.encode("utf-8"))
                encrypted.setencryption(pyzipper.WZ_AES, nbits=256)
                for member in members:
                    # Rebuild ZIP metadata: do not carry old compression/encryption
                    # extra fields into the new archive.
                    info = encrypted.zipinfo_cls(member.filename, date_time=member.date_time)
                    info.compress_type = pyzipper.ZIP_DEFLATED
                    info.file_size = member.file_size
                    info.create_system = member.create_system
                    info.external_attr = member.external_attr
                    with original.open(member) as plain, encrypted.open(info, "w") as protected:
                        # Reading to EOF also validates the original entry's CRC.
                        copyfileobj(plain, protected, length=1024 * 1024)
        output.seek(0)
        return output
    except Exception as exc:
        output.close()
        if isinstance(exc, DownloadEncryptionError):
            raise
        raise DownloadEncryptionError("The protected recovery ZIP could not be created.") from exc


def _validate_package_member(member: zipfile.ZipInfo) -> None:
    filename = member.filename
    parts = filename.rstrip("/").split("/")
    if (
        member.orig_filename != filename
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in filename
        or ":" in filename
        or any(ord(character) < 32 for character in filename)
        or member.flag_bits & 1
        or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
        or (member.is_dir() and member.file_size)
    ):
        raise DownloadEncryptionError("The recovery ZIP contains an invalid entry.")


def _safe_member_filename(filename: str) -> str:
    if (
        not filename
        or filename in {".", ".."}
        or PurePath(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise DownloadEncryptionError("The download filename is invalid.")
    return filename
