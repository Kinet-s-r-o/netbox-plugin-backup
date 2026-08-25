from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class NasBackupStatus:
    enabled: bool
    state: str
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    snapshot_id: str = ""


def get_nas_backup_status(
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> NasBackupStatus:
    if not config.get("nas_backup_enabled", False):
        return NasBackupStatus(enabled=False, state="disabled")

    current_time = now or datetime.now(UTC)
    status_path = Path(
        str(
            config.get(
                "nas_backup_status_path",
                "/var/lib/netbox-config-backup/nas-backup/last-success.json",
            )
        )
    )
    success = _load_status(status_path, expected_status="success")
    failure = _load_status(status_path.with_name("last-failure.json"), expected_status="failed")
    last_success = _timestamp(success)
    last_failure = _timestamp(failure)

    if last_failure and (not last_success or last_failure > last_success):
        return NasBackupStatus(
            enabled=True,
            state="failed",
            last_success_at=last_success,
            last_failure_at=last_failure,
            snapshot_id=_snapshot_id(success),
        )
    if last_success is None:
        return NasBackupStatus(enabled=True, state="never")

    try:
        stale_hours = max(1, int(config.get("nas_backup_stale_hours", 48)))
    except (TypeError, ValueError):
        stale_hours = 48
    state = "healthy" if current_time - last_success <= timedelta(hours=stale_hours) else "stale"
    return NasBackupStatus(
        enabled=True,
        state=state,
        last_success_at=last_success,
        last_failure_at=last_failure,
        snapshot_id=_snapshot_id(success),
    )


def _load_status(path: Path, *, expected_status: str) -> dict[str, Any]:
    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_size > 4096:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema") != 1:
        return {}
    if value.get("status") != expected_status:
        return {}
    return value


def _timestamp(value: Mapping[str, Any]) -> datetime | None:
    epoch = value.get("epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _snapshot_id(value: Mapping[str, Any]) -> str:
    snapshot_id = value.get("snapshot_id", "")
    if isinstance(snapshot_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", snapshot_id):
        return snapshot_id
    return ""
