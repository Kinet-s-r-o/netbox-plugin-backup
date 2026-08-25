import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from netbox_config_backup.services.connection_test_status import (
    connection_test_status_payload,
)


def make_job(*, status, data=None, error=""):
    now = datetime.now(UTC)
    return SimpleNamespace(
        job_id=uuid4(),
        status=status,
        data=data,
        error=error,
        created=now,
        started=now if status != "pending" else None,
        completed=now if status in {"completed", "failed", "errored"} else None,
    )


class ConnectionTestStatusPayloadTests(unittest.TestCase):
    def test_pending_job_is_active_and_safe(self):
        payload = connection_test_status_payload(make_job(status="pending"))

        self.assertFalse(payload["terminal"])
        self.assertEqual(payload["label"], "Queued")
        self.assertEqual(payload["error_code"], "")

    def test_completed_job_exposes_structured_result(self):
        payload = connection_test_status_payload(
            make_job(
                status="completed",
                data={
                    "connection_test": {
                        "success": True,
                        "driver_id": "fake",
                        "artifact_count": 2,
                        "total_bytes": 4096,
                        "safe_message": "Connection test succeeded.",
                    }
                },
            )
        )

        self.assertTrue(payload["terminal"])
        self.assertEqual(payload["driver_id"], "fake")
        self.assertEqual(payload["artifact_count"], 2)
        self.assertEqual(payload["total_bytes"], 4096)

    def test_legacy_completed_job_is_still_reported_as_successful(self):
        payload = connection_test_status_payload(make_job(status="completed"))

        self.assertTrue(payload["terminal"])
        self.assertEqual(payload["label"], "Successful")
        self.assertEqual(payload["driver_id"], "")

    def test_unexpected_error_does_not_expose_job_error(self):
        payload = connection_test_status_payload(
            make_job(status="errored", error="password=do-not-display")
        )

        self.assertEqual(payload["error_code"], "INTERNAL_ERROR")
        self.assertNotIn("do-not-display", payload["message"])

    def test_host_key_candidate_is_exposed_as_structured_safe_data(self):
        candidate = {
            "id": 17,
            "address": "192.0.2.10",
            "port": 22,
            "key_type": "ssh-ed25519",
            "fingerprint_sha256": "SHA256:example",
            "fingerprint_md5": "MD5:00:11",
            "status": "pending",
        }
        payload = connection_test_status_payload(
            make_job(
                status="failed",
                data={
                    "connection_test": {
                        "error_code": "HOST_KEY_UNKNOWN",
                        "safe_message": "Approval required.",
                        "host_key_candidate": candidate,
                    }
                },
            )
        )

        self.assertEqual(payload["host_key_candidate"], candidate)


if __name__ == "__main__":
    unittest.main()
