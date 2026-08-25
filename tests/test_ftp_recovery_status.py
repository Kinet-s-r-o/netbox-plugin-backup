import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from netbox_config_backup.services.ftp_recovery_status import (
    ftp_recovery_status_payload,
)


def job(status, data=None):
    return SimpleNamespace(
        job_id=uuid4(),
        status=status,
        data=data or {},
        created=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        started=None,
        completed=None,
    )


class FtpRecoveryStatusTests(unittest.TestCase):
    def test_completed_package_is_ready_for_download(self):
        state = job(
            "completed",
            {
                "ftp_recovery_package": {
                    "ready": True,
                    "filename": "router.zip",
                    "size": 123,
                    "sha256": "a" * 64,
                    "file_count": 2,
                    "verified_bytes": 100,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "destination_name": "Internal FTP",
                }
            },
        )

        payload = ftp_recovery_status_payload(state)

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["label"], "Ready")
        self.assertEqual(payload["destination_name"], "Internal FTP")

    def test_failed_job_exposes_only_safe_structured_error(self):
        state = job(
            "failed",
            {
                "ftp_recovery_package": {
                    "ready": False,
                    "error_code": "RECOVERY_HASH_MISMATCH",
                    "safe_message": "An FTP revision file failed SHA256 verification.",
                }
            },
        )

        payload = ftp_recovery_status_payload(state)

        self.assertFalse(payload["ready"])
        self.assertEqual(payload["error_code"], "RECOVERY_HASH_MISMATCH")
        self.assertNotIn("traceback", payload)


if __name__ == "__main__":
    unittest.main()
