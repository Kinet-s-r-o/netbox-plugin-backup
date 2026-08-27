from __future__ import annotations


def _datetime(value):
    return value.isoformat() if value else None


def destination_reconciliation_status_payload(job) -> dict[str, object]:
    result = (job.data or {}).get("destination_reconciliation") or {}
    status = job.status
    terminal = status in {"completed", "errored", "failed"}
    payload: dict[str, object] = {
        "job_id": str(job.job_id),
        "status": status,
        "terminal": terminal,
        "created_at": _datetime(job.created),
        "started_at": _datetime(job.started),
        "completed_at": _datetime(job.completed),
        "error_code": "",
        "success": result.get("success"),
        "checked_replicas": result.get("checked_replicas", 0),
        "healthy_replicas": result.get("healthy_replicas", 0),
        "failed_replicas": result.get("failed_replicas", 0),
        "skipped_replicas": result.get("skipped_replicas", 0),
        "checked_files": result.get("checked_files", 0),
        "verified_bytes": result.get("verified_bytes", 0),
        "missing_files": result.get("missing_files", 0),
        "size_mismatches": result.get("size_mismatches", 0),
        "hash_mismatches": result.get("hash_mismatches", 0),
        "unreadable_files": result.get("unreadable_files", 0),
        "issues": result.get("issues") or [],
        "issues_truncated": bool(result.get("issues_truncated")),
    }

    if status in {"pending", "scheduled"}:
        values = (
            "Queued",
            "secondary",
            "mdi-clock-outline",
            "Storage integrity audit is queued",
            "Waiting for an available backup worker.",
        )
    elif status == "running":
        values = (
            "Running",
            "info",
            "mdi-shield-search-outline",
            "Checking remote revision copies",
            "Reading file sizes and hashes. Nothing on the storage is changed.",
        )
    elif status == "completed" and result.get("success") is False:
        values = (
            "Attention needed",
            "warning",
            "mdi-alert-outline",
            "Storage audit found integrity problems",
            result.get("safe_message") or "One or more expected files did not match.",
        )
    elif status == "completed":
        values = (
            "Healthy",
            "success",
            "mdi-check-decagram-outline",
            "Remote revision copies are healthy",
            result.get("safe_message") or "All expected files passed integrity verification.",
        )
    else:
        values = (
            "Failed",
            "danger",
            "mdi-alert-circle-outline",
            "Storage integrity audit could not complete",
            result.get("safe_message") or "The destination could not be audited.",
        )
        payload["error_code"] = result.get("error_code") or "DESTINATION_RECONCILIATION_FAILED"

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
