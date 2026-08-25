from __future__ import annotations


def _display_datetime(value):
    if value is None:
        return None
    return value.isoformat()


def connection_test_status_payload(job) -> dict:
    """Return the safe, structured connection-test state exposed to the plugin UI."""
    result = (job.data or {}).get("connection_test") or {}
    status = job.status
    payload = {
        "job_id": str(job.job_id),
        "status": status,
        "terminal": status in {"completed", "errored", "failed"},
        "created_at": _display_datetime(job.created),
        "started_at": _display_datetime(job.started),
        "completed_at": _display_datetime(job.completed),
        "driver_id": result.get("driver_id") or "",
        "artifact_count": result.get("artifact_count") or 0,
        "total_bytes": result.get("total_bytes") or 0,
        "error_code": "",
        "host_key_candidate": result.get("host_key_candidate"),
        "host_key_scan_error": result.get("host_key_scan_error"),
    }

    if status in {"pending", "scheduled"}:
        payload.update(
            {
                "label": "Queued",
                "color": "secondary",
                "icon": "mdi-clock-outline",
                "headline": "Connection test is queued",
                "message": "Waiting for an available backup worker.",
            }
        )
    elif status == "running":
        payload.update(
            {
                "label": "Running",
                "color": "info",
                "icon": "mdi-lan-connect",
                "headline": "Testing the connection",
                "message": "Connecting to the device and validating the collected configuration.",
            }
        )
    elif status == "completed":
        payload.update(
            {
                "label": "Successful",
                "color": "success",
                "icon": "mdi-check-circle-outline",
                "headline": "Connection test succeeded",
                "message": result.get("safe_message")
                or (
                    "Connection test completed successfully. Detailed collection results "
                    "were not recorded by this plugin version."
                ),
            }
        )
    elif status == "failed":
        payload.update(
            {
                "label": "Failed",
                "color": "danger",
                "icon": "mdi-alert-circle-outline",
                "headline": "Connection test failed",
                "message": result.get("safe_message")
                or "The device connection or configuration validation failed.",
                "error_code": result.get("error_code") or "CONNECTION_TEST_FAILED",
            }
        )
    else:
        payload.update(
            {
                "label": "Error",
                "color": "danger",
                "icon": "mdi-alert-circle-outline",
                "headline": "Connection test stopped",
                "message": (
                    "The test stopped because of an unexpected internal error. "
                    "Check the NetBox worker logs for more information."
                ),
                "error_code": "INTERNAL_ERROR",
            }
        )
    return payload
