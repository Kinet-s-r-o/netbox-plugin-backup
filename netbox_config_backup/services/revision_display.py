from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from typing import Any

from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import CollectedArtifact
from netbox_config_backup.drivers.registry import DriverRegistry, DriverRegistryError
from netbox_config_backup.storage.base import ConfigStorage, StorageError


class RevisionDisplayError(RuntimeError):
    """A revision cannot be displayed; the message is safe for the UI."""


@dataclass(frozen=True, slots=True)
class DisplayLine:
    number: int
    text: str


@dataclass(frozen=True, slots=True)
class DisplayContent:
    artifact: Any
    text: str
    lines: tuple[DisplayLine, ...]
    size: int
    displayed_size: int
    truncated: bool
    raw_hash: str


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class DisplayDiff:
    lines: tuple[DiffLine, ...]
    truncated: bool


def prepare_display_content(
    content: bytes,
    *,
    artifact: Any = None,
    driver_id: str,
    expected_size: int,
    expected_hash: str,
    max_bytes: int,
    allow_truncate: bool = False,
    drivers: DriverRegistry = driver_registry,
) -> DisplayContent:
    if not isinstance(content, bytes):
        raise RevisionDisplayError("Stored configuration has an invalid format.")
    if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
        raise RevisionDisplayError("Stored configuration failed its integrity check.")
    if len(content) > max_bytes and not allow_truncate:
        raise RevisionDisplayError("Stored configuration is too large for the browser preview.")
    try:
        text = content.decode("utf-8")
        driver = drivers.create(driver_id)
        redacted = driver.redact_for_display(text)
    except UnicodeError as exc:
        raise RevisionDisplayError("Stored configuration is not valid UTF-8 text.") from exc
    except DriverRegistryError as exc:
        raise RevisionDisplayError(
            "The revision driver is unavailable for safe display redaction."
        ) from exc
    except Exception as exc:
        raise RevisionDisplayError("Configuration display redaction failed safely.") from exc

    redacted_bytes = redacted.encode("utf-8")
    truncated = len(redacted_bytes) > max_bytes
    if truncated:
        preview_bytes = redacted_bytes[:max_bytes]
        preview_text = preview_bytes.decode("utf-8", errors="ignore")
        newline = max(preview_text.rfind("\n"), preview_text.rfind("\r"))
        if newline > 0:
            preview_text = preview_text[:newline]
        redacted = preview_text
        displayed_size = len(redacted.encode("utf-8"))
    else:
        displayed_size = len(redacted_bytes)

    lines = tuple(
        DisplayLine(number=number, text=line)
        for number, line in enumerate(redacted.splitlines(), start=1)
    )
    return DisplayContent(
        artifact=artifact,
        text=redacted,
        lines=lines,
        size=len(content),
        displayed_size=displayed_size,
        truncated=truncated,
        raw_hash=expected_hash,
    )


def load_artifact_content(
    artifact,
    *,
    storage: ConfigStorage | None = None,
) -> bytes:
    """Read one stored artifact and fail closed if its integrity metadata differs."""

    from django.conf import settings

    from netbox_config_backup.storage.factory import build_config_storage

    target_storage = storage or build_config_storage(
        settings.PLUGINS_CONFIG["netbox_config_backup"]
    )
    try:
        content = target_storage.get(artifact.storage_key)
    except StorageError as exc:
        raise RevisionDisplayError("Stored configuration could not be read.") from exc
    if len(content) != artifact.size or hashlib.sha256(content).hexdigest() != artifact.raw_hash:
        raise RevisionDisplayError("Stored configuration failed its integrity check.")
    return content


def load_revision_content(
    revision,
    *,
    storage: ConfigStorage | None = None,
    max_bytes: int | None = None,
    allow_truncate: bool = False,
    normalize_for_comparison: bool = False,
    drivers: DriverRegistry = driver_registry,
) -> DisplayContent:
    from django.conf import settings

    from netbox_config_backup.storage.factory import build_config_storage

    artifact = revision.artifacts.filter(is_primary=True).first()
    if artifact is None:
        raise RevisionDisplayError("This revision has no primary configuration artifact.")

    plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
    target_storage = storage or build_config_storage(plugin_settings)
    preview_limit = max_bytes or plugin_settings["content_preview_max_bytes"]
    content = load_artifact_content(artifact, storage=target_storage)

    # Compatibility for Ceragon revisions created before configuration_dump became
    # the primary artifact. The native ZIP remains authoritative and is verified
    # before its text member is extracted for display and comparison.
    if revision.driver_id == "ceragon_ip50" and artifact.artifact_type == "backup_manifest":
        native_artifact = revision.artifacts.filter(artifact_type="native_backup").first()
        if native_artifact is None:
            raise RevisionDisplayError("This Ceragon revision has no native backup artifact.")
        native_content = load_artifact_content(native_artifact, storage=target_storage)
        try:
            driver = drivers.create(revision.driver_id)
            content = driver.extract_configuration(native_content)
        except Exception as exc:
            raise RevisionDisplayError(
                "The Ceragon configuration could not be extracted safely."
            ) from exc
        artifact = DisplayArtifact(
            artifact_type="configuration_dump",
            format="ceragon_ceraos_config_dump",
            size=len(content),
        )

    # Compatibility for RACOM revisions created before the extracted link
    # configuration became the primary artifact. The verified TGZ remains the
    # authoritative native backup and is never modified.
    if (
        revision.driver_id in {"racom_ray2", "racom_ray3"}
        and artifact.artifact_type == "backup_manifest"
    ):
        native_artifact = revision.artifacts.filter(artifact_type="native_backup").first()
        if native_artifact is None:
            raise RevisionDisplayError("This RACOM revision has no native backup artifact.")
        native_content = load_artifact_content(native_artifact, storage=target_storage)
        try:
            driver = drivers.create(revision.driver_id)
            content = driver.extract_configuration(native_content)
        except Exception as exc:
            raise RevisionDisplayError(
                "The RACOM configuration could not be extracted safely."
            ) from exc
        artifact = DisplayArtifact(
            artifact_type="configuration_dump",
            format="racom_ray_json",
            size=len(content),
        )

    if normalize_for_comparison:
        try:
            driver = drivers.create(revision.driver_id)
            content = driver.normalize(
                CollectedArtifact(
                    artifact_type=artifact.artifact_type,
                    filename="configuration.txt",
                    content=content,
                    format=artifact.format,
                    is_primary=True,
                )
            )
        except Exception as exc:
            raise RevisionDisplayError("Configuration normalization failed safely.") from exc

    return prepare_display_content(
        content,
        artifact=artifact,
        driver_id=revision.driver_id,
        expected_size=len(content),
        expected_hash=hashlib.sha256(content).hexdigest(),
        max_bytes=preview_limit,
        allow_truncate=allow_truncate,
        drivers=drivers,
    )


@dataclass(frozen=True, slots=True)
class DisplayArtifact:
    artifact_type: str
    format: str
    size: int


def build_display_diff(
    before: DisplayContent,
    after: DisplayContent,
    *,
    before_label: str,
    after_label: str,
    max_lines: int,
) -> DisplayDiff:
    generated = difflib.unified_diff(
        before.text.splitlines(),
        after.text.splitlines(),
        fromfile=before_label,
        tofile=after_label,
        lineterm="",
    )
    lines: list[DiffLine] = []
    truncated = False
    for index, line in enumerate(generated):
        if index >= max_lines:
            truncated = True
            break
        if line.startswith(("---", "+++")):
            kind = "file"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "added"
        elif line.startswith("-"):
            kind = "removed"
        else:
            kind = "context"
        lines.append(DiffLine(kind=kind, text=line))
    return DisplayDiff(lines=tuple(lines), truncated=truncated)
