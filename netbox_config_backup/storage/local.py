from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from .base import ConfigStorage, StorageError, StorageObject


class LocalConfigStorage(ConfigStorage):
    """Filesystem storage with key confinement and atomic file replacement."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("Local storage root could not be created.") from exc

    def _path(self, key: str) -> Path:
        if not key or "\\" in key:
            raise StorageError("Invalid storage key.")
        pure_key = PurePosixPath(key)
        if pure_key.is_absolute() or any(part in {"", ".", ".."} for part in pure_key.parts):
            raise StorageError("Invalid storage key.")
        candidate = self.root.joinpath(*pure_key.parts).resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise StorageError("Storage key escapes the configured root.")
        return candidate

    def put(
        self,
        key: str,
        content: bytes,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObject:
        if not isinstance(content, bytes):
            raise TypeError("Storage content must be bytes.")
        path = self._path(key)
        temp_path: Path | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temp_path = Path(raw_temp_path)
            try:
                os.chmod(temp_path, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                if descriptor >= 0:
                    os.close(descriptor)
                raise
            os.replace(temp_path, path)
            temp_path = None
            os.chmod(path, 0o600)
        except OSError as exc:
            raise StorageError("Local configuration write failed.") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return StorageObject(key=key, size=len(content), metadata=dict(metadata or {}))

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError("Local configuration read failed.") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Local configuration delete failed.") from exc

    def stage_delete(self, key: str, namespace: str) -> str | None:
        """Atomically move an object into an internal deletion quarantine."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", namespace):
            raise StorageError("Invalid storage quarantine namespace.")
        source = self._path(key)
        if not source.is_file():
            return None
        staged_key = f".retention-trash/{namespace}/{key}"
        destination = self._path(staged_key)
        try:
            if destination.exists():
                raise StorageError("Storage quarantine object already exists.")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, destination)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Local configuration quarantine failed.") from exc
        return staged_key

    def restore_staged_delete(self, key: str, staged_key: str) -> None:
        """Restore a quarantined object after a failed database transaction."""
        source = self._path(staged_key)
        if not source.is_file():
            raise StorageError("Quarantined configuration object is missing.")
        destination = self._path(key)
        try:
            if destination.exists():
                raise StorageError("Configuration restore destination already exists.")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, destination)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Local configuration restore failed.") from exc

    def purge_staged_delete(self, staged_key: str) -> None:
        """Permanently remove an object after its database transaction commits."""
        self.delete(staged_key)

    def healthcheck(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.R_OK | os.W_OK)
