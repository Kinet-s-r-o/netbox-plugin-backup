from __future__ import annotations


def _datetime(value):
    return value.isoformat() if value else None


def destination_test_status_payload(job) -> dict[str, object]:
    result = (job.data or {}).get("destination_test") or {}
    protocol = str(result.get("protocol") or "external").upper()
    status = job.status
    payload: dict[str, object] = {
        "job_id": str(job.job_id),
        "status": status,
        "terminal": status in {"completed", "errored", "failed"},
        "created_at": _datetime(job.created),
        "started_at": _datetime(job.started),
        "completed_at": _datetime(job.completed),
        "error_code": "",
        "host_key_candidate": result.get("host_key_candidate"),
    }
    states = {
        "pending": (
            "Queued",
            "secondary",
            "mdi-clock-outline",
            f"{protocol} test is queued",
            "Waiting for an available backup worker.",
        ),
        "scheduled": (
            "Queued",
            "secondary",
            "mdi-clock-outline",
            f"{protocol} test is queued",
            "Waiting for an available backup worker.",
        ),
        "running": (
            "Running",
            "info",
            "mdi-server-network",
            f"Testing the {protocol} destination",
            "Verifying login, upload, integrity, and delete access.",
        ),
        "completed": (
            "Successful",
            "success",
            "mdi-check-circle-outline",
            f"{protocol} destination is ready",
            result.get("safe_message") or "Authentication and write verification succeeded.",
        ),
        "failed": (
            "Failed",
            "danger",
            "mdi-alert-circle-outline",
            f"{protocol} destination test failed",
            result.get("safe_message") or "The destination could not be verified.",
        ),
    }
    values = states.get(
        status,
        (
            "Error",
            "danger",
            "mdi-alert-circle-outline",
            f"{protocol} destination test stopped",
            "The test stopped because of an unexpected internal error.",
        ),
    )
    payload.update(
        {
            "label": values[0],
            "color": values[1],
            "icon": values[2],
            "headline": values[3],
            "message": values[4],
        }
    )
    if status == "failed":
        payload["error_code"] = result.get("error_code") or "DESTINATION_TEST_FAILED"
    elif status not in states:
        payload["error_code"] = "INTERNAL_ERROR"
    return payload
