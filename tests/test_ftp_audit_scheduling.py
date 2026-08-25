import unittest
from datetime import UTC, datetime, time
from types import SimpleNamespace

from netbox_config_backup.services.ftp_audit_scheduling import (
    calculate_destination_next_ftp_audit,
    calculate_next_ftp_audit,
)


def destination(**overrides):
    values = {
        "enabled": True,
        "protocol": "ftp",
        "integrity_audit_enabled": True,
        "integrity_audit_frequency": "daily",
        "integrity_audit_time": time(4, 0),
        "integrity_audit_weekday": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FtpAuditSchedulingTests(unittest.TestCase):
    def test_daily_schedule_uses_netbox_timezone(self):
        result = calculate_next_ftp_audit(
            destination(),
            after=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 24, 2, 0, tzinfo=UTC))

    def test_daily_schedule_rolls_to_next_day(self):
        result = calculate_next_ftp_audit(
            destination(),
            after=datetime(2026, 8, 24, 3, 0, tzinfo=UTC),
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 25, 2, 0, tzinfo=UTC))

    def test_weekly_schedule_uses_configured_weekday(self):
        result = calculate_next_ftp_audit(
            destination(
                integrity_audit_frequency="weekly",
                integrity_audit_weekday=2,
                integrity_audit_time=time(6, 30),
            ),
            after=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 26, 4, 30, tzinfo=UTC))

    def test_weekly_schedule_rolls_seven_days_after_same_day_time(self):
        result = calculate_next_ftp_audit(
            destination(
                integrity_audit_frequency="weekly",
                integrity_audit_weekday=0,
                integrity_audit_time=time(6, 30),
            ),
            after=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 31, 4, 30, tzinfo=UTC))

    def test_inactive_destination_has_no_next_audit(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        for configured in (
            destination(enabled=False),
            destination(integrity_audit_enabled=False),
            destination(protocol="sftp"),
        ):
            with self.subTest(configured=configured):
                self.assertIsNone(calculate_destination_next_ftp_audit(configured, now=now))

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_next_ftp_audit(
                destination(),
                after=datetime(2026, 8, 24, 12, 0),  # noqa: DTZ001 - rejection case
            )


if __name__ == "__main__":
    unittest.main()
