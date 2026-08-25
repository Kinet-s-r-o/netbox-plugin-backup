from __future__ import annotations

import io
import posixpath
import tarfile
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchiveCheck:
    valid: bool
    message: str = ""


class ArchiveExtractionError(ValueError):
    """A requested archive member cannot be extracted safely."""


def validate_archive(
    content: bytes,
    *,
    kind: str,
    max_entries: int = 2000,
    max_uncompressed_bytes: int = 250 * 1024 * 1024,
) -> ArchiveCheck:
    try:
        entries = _entries(content, kind=kind)
        if not entries:
            return ArchiveCheck(False, "The device backup archive contains no files.")
        if len(entries) > max_entries:
            return ArchiveCheck(False, "The device backup archive contains too many files.")
        total = 0
        for name, size, is_link in entries:
            normalized = posixpath.normpath(name.replace("\\", "/"))
            if (
                not name
                or normalized == ".."
                or normalized.startswith(("../", "/"))
                or ":" in normalized.split("/", 1)[0]
                or is_link
            ):
                return ArchiveCheck(False, "The device backup archive contains an unsafe path.")
            total += size
            if size < 0 or total > max_uncompressed_bytes:
                return ArchiveCheck(False, "The expanded device backup archive is too large.")
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, ValueError):
        return ArchiveCheck(False, "The device backup archive is invalid or corrupted.")
    return ArchiveCheck(True)


def extract_zip_member(
    content: bytes,
    *,
    member_name: str,
    max_bytes: int = 25 * 1024 * 1024,
) -> bytes:
    """Return one exact ZIP member after validating the complete archive.

    Exact-name lookup, duplicate rejection, expanded-size limits, and the full
    archive path checks prevent traversal, ambiguous members, and ZIP bombs.
    Reading the member also makes ``zipfile`` verify its CRC.
    """

    if (
        not isinstance(member_name, str)
        or not member_name
        or posixpath.normpath(member_name) != member_name
        or member_name.startswith("/")
        or "\\" in member_name
    ):
        raise ArchiveExtractionError("The requested backup member name is invalid.")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ArchiveExtractionError("The backup member size limit is invalid.")

    check = validate_archive(content, kind="zip")
    if not check.valid:
        raise ArchiveExtractionError(check.message)

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            matches = [item for item in archive.infolist() if item.filename == member_name]
            if len(matches) != 1 or matches[0].is_dir():
                raise ArchiveExtractionError(
                    "The device backup does not contain one expected configuration file."
                )
            member = matches[0]
            if member.file_size <= 0:
                raise ArchiveExtractionError("The device configuration file is empty.")
            if member.file_size > max_bytes:
                raise ArchiveExtractionError(
                    "The expanded device configuration exceeds the safety limit."
                )
            with archive.open(member, "r") as handle:
                extracted = handle.read(max_bytes + 1)
    except ArchiveExtractionError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, ValueError) as exc:
        raise ArchiveExtractionError(
            "The device configuration file could not be extracted safely."
        ) from exc

    if len(extracted) != member.file_size or len(extracted) > max_bytes:
        raise ArchiveExtractionError("The extracted device configuration failed its size check.")
    return extracted


def extract_tgz_members(
    content: bytes,
    *,
    member_names: tuple[str, ...],
    max_member_bytes: int = 25 * 1024 * 1024,
) -> dict[str, bytes]:
    """Return exact regular-file members from a validated TGZ archive."""

    if (
        not member_names
        or len(set(member_names)) != len(member_names)
        or isinstance(max_member_bytes, bool)
        or not isinstance(max_member_bytes, int)
        or max_member_bytes <= 0
    ):
        raise ArchiveExtractionError("The requested backup members are invalid.")
    for member_name in member_names:
        if (
            not isinstance(member_name, str)
            or not member_name
            or posixpath.normpath(member_name) != member_name
            or member_name.startswith("/")
            or "\\" in member_name
        ):
            raise ArchiveExtractionError("A requested backup member name is invalid.")

    check = validate_archive(content, kind="tgz")
    if not check.valid:
        raise ArchiveExtractionError(check.message)

    extracted: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            for member_name in member_names:
                matches = [item for item in archive.getmembers() if item.name == member_name]
                if len(matches) != 1 or not matches[0].isfile():
                    raise ArchiveExtractionError(
                        "The device backup does not contain every expected configuration file."
                    )
                member = matches[0]
                if member.size <= 0:
                    raise ArchiveExtractionError("A device configuration file is empty.")
                if member.size > max_member_bytes:
                    raise ArchiveExtractionError(
                        "An expanded device configuration exceeds the safety limit."
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    raise ArchiveExtractionError("A device configuration file could not be read.")
                value = handle.read(max_member_bytes + 1)
                if len(value) != member.size or len(value) > max_member_bytes:
                    raise ArchiveExtractionError(
                        "An extracted device configuration failed its size check."
                    )
                extracted[member_name] = value
    except ArchiveExtractionError:
        raise
    except (OSError, EOFError, tarfile.TarError, ValueError) as exc:
        raise ArchiveExtractionError(
            "The device configuration files could not be extracted safely."
        ) from exc
    return extracted


def _entries(content: bytes, *, kind: str) -> list[tuple[str, int, bool]]:
    stream = io.BytesIO(content)
    if kind == "zip":
        with zipfile.ZipFile(stream) as archive:
            return [
                (item.filename, item.file_size, item.is_dir() or False)
                for item in archive.infolist()
                if not item.is_dir()
            ]
    if kind == "tgz":
        with tarfile.open(fileobj=stream, mode="r:gz") as archive:
            return [
                (item.name, item.size, item.issym() or item.islnk())
                for item in archive.getmembers()
                if item.isfile() or item.issym() or item.islnk()
            ]
    raise ValueError("Unsupported archive kind")
