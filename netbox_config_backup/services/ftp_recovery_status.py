from __future__ import annotations

from .ftp_recovery import recovery_package_is_expired


def _datetime(value):
    return value.isoformat() if value else None


def ftp_recovery_status_payload(job) -> dict[str, object]:
    result = (job.data or {}).get("ftp_recovery_package") or {}
    status = job.status
    terminal = status in {"completed", "errored", "failed"}
    expired = bool(
        status == "completed"
        and result.get("expires_at")
        and recovery_package_is_expired(result["expires_at"])
    )
    payload: dict[str, object] = {
        "job_id": str(job.job_id),
        "status": status,
        "terminal": terminal,
        "created_at": _datetime(job.created),
        "started_at": _datetime(job.started),
        "completed_at": _datetime(job.completed),
        "ready": bool(result.get("ready")) and status == "completed" and not expired,
        "expired": expired,
        "filename": result.get("filename") or "",
        "size": result.get("size") or 0,
        "sha256": result.get("sha256") or "",
        "file_count": result.get("file_count") or 0,
        "verified_bytes": result.get("verified_bytes") or 0,
        "expires_at": result.get("expires_at"),
        "destination_name": result.get("destination_name") or "",
        "download_count": result.get("download_count") or 0,
        "error_code": "",
    }

    if status in {"pending", "scheduled"}:
        values = (
            "Queued",
            "secondary",
            "mdi-clock-outline",
            "Recovery package is queued",
            "Waiting for an available backup worker.",
        )
    elif status == "running":
        values = (
            "Verifying",
            "info",
            "mdi-shield-search-outline",
            "Downloading and verifying the FTP copy",
            "The worker is reading the selected FTP revision and checking every SHA256.",
        )
    elif status == "completed" and expired:
        values = (
            "Expired",
            "secondary",
            "mdi-timer-off-outline",
            "The temporary package has expired",
            "Prepare a new package to download this FTP revision again.",
        )
    elif status == "completed" and result.get("ready"):
        values = (
            "Ready",
            "success",
            "mdi-package-down",
            "Verified recovery package is ready",
            "All FTP files passed size and SHA256 verification.",
        )
    else:
        values = (
            "Failed",
            "danger",
            "mdi-alert-circle-outline",
            "Recovery package could not be prepared",
            result.get("safe_message")
            or "The FTP copy could not be downloaded and verified safely.",
        )
        payload["error_code"] = result.get("error_code") or "RECOVERY_PACKAGE_FAILED"

    payload.update(
        {
            "label": values[0],
            "color": values[1],
            "icon": values[2],
            "headline": values[3],
            "message": values[4],
        }
    )
    return payload
