import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from netbox_config_backup.services.nas_backup_status import get_nas_backup_status


class NasBackupStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def config(self, path, **overrides):
        return {
            "nas_backup_enabled": True,
            "nas_backup_status_path": str(path),
            "nas_backup_stale_hours": 48,
            **overrides,
        }

    @staticmethod
    def write(path: Path, *, status: str, when: datetime, snapshot_id="abc123"):
        path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "status": status,
                    "epoch": int(when.timestamp()),
                    "snapshot_id": snapshot_id,
                }
            ),
            encoding="utf-8",
        )

    def test_disabled_does_not_read_status_file(self):
        status = get_nas_backup_status({"nas_backup_enabled": False}, now=self.now)
        self.assertEqual(status.state, "disabled")

    def test_missing_status_is_never(self):
        with tempfile.TemporaryDirectory() as directory:
            status = get_nas_backup_status(
                self.config(Path(directory) / "last-success.json"), now=self.now
            )
        self.assertEqual(status.state, "never")

    def test_recent_success_is_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "last-success.json"
            self.write(path, status="success", when=self.now - timedelta(hours=2))
            status = get_nas_backup_status(self.config(path), now=self.now)
        self.assertEqual(status.state, "healthy")
        self.assertEqual(status.snapshot_id, "abc123")

    def test_old_success_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "last-success.json"
            self.write(path, status="success", when=self.now - timedelta(hours=49))
            status = get_nas_backup_status(self.config(path), now=self.now)
        self.assertEqual(status.state, "stale")

    def test_newer_failure_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "last-success.json"
            self.write(path, status="success", when=self.now - timedelta(hours=2))
            self.write(
                path.with_name("last-failure.json"),
                status="failed",
                when=self.now - timedelta(hours=1),
            )
            status = get_nas_backup_status(self.config(path), now=self.now)
        self.assertEqual(status.state, "failed")

    def test_invalid_or_oversized_status_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "last-success.json"
            path.write_text("x" * 5000, encoding="utf-8")
            status = get_nas_backup_status(self.config(path), now=self.now)
        self.assertEqual(status.state, "never")
