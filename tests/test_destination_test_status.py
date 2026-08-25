import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from netbox_config_backup.services.destination_reconciliation_status import (
    destination_reconciliation_status_payload,
)
from netbox_config_backup.services.destination_test_status import (
    destination_test_status_payload,
)


def job(status, data=None):
    now = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    return SimpleNamespace(
        job_id=uuid4(),
        status=status,
        data=data or {},
        created=now,
        started=now if status not in {"pending", "scheduled"} else None,
        completed=now if status in {"completed", "failed", "errored"} else None,
    )


class DestinationTestStatusTests(unittest.TestCase):
    def test_running_state_is_not_terminal(self):
        payload = destination_test_status_payload(job("running"))

        self.assertFalse(payload["terminal"])
        self.assertEqual(payload["color"], "info")
        self.assertEqual(payload["error_code"], "")

    def test_success_uses_safe_job_message(self):
        payload = destination_test_status_payload(
            job(
                "completed",
                {"destination_test": {"safe_message": "Verified safely."}},
            )
        )

        self.assertTrue(payload["terminal"])
        self.assertEqual(payload["message"], "Verified safely.")

    def test_failure_exposes_only_structured_safe_fields(self):
        payload = destination_test_status_payload(
            job(
                "failed",
                {
                    "destination_test": {
                        "error_code": "AUTH_FAILED",
                        "safe_message": "SFTP server authentication failed.",
                        "host_key_candidate": {"fingerprint_sha256": "SHA256:test"},
                    }
                },
            )
        )

        self.assertEqual(payload["error_code"], "AUTH_FAILED")
        self.assertEqual(payload["message"], "SFTP server authentication failed.")
        self.assertEqual(payload["host_key_candidate"]["fingerprint_sha256"], "SHA256:test")


class DestinationReconciliationStatusTests(unittest.TestCase):
    def test_completed_audit_with_integrity_issues_is_warning_not_job_failure(self):
        payload = destination_reconciliation_status_payload(
            job(
                "completed",
                {
                    "destination_reconciliation": {
                        "success": False,
                        "safe_message": "The FTP audit found 1 problem.",
                        "checked_replicas": 2,
                        "healthy_replicas": 1,
                        "failed_replicas": 1,
                        "checked_files": 5,
                        "missing_files": 1,
                    }
                },
            )
        )

        self.assertTrue(payload["terminal"])
        self.assertEqual(payload["color"], "warning")
        self.assertEqual(payload["failed_replicas"], 1)
        self.assertEqual(payload["error_code"], "")

    def test_failed_audit_exposes_safe_error(self):
        payload = destination_reconciliation_status_payload(
            job(
                "failed",
                {
                    "destination_reconciliation": {
                        "success": False,
                        "error_code": "AUTH_FAILED",
                        "safe_message": "FTP server authentication failed.",
                    }
                },
            )
        )

        self.assertEqual(payload["color"], "danger")
        self.assertEqual(payload["error_code"], "AUTH_FAILED")
        self.assertEqual(payload["message"], "FTP server authentication failed.")


if __name__ == "__main__":
    unittest.main()
