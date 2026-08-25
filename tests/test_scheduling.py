import unittest
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

from netbox_config_backup.services.scheduling import (
    calculate_failure_next_run,
    calculate_next_run,
    is_retry_scheduled,
)


def policy(**overrides):
    values = {
        "pk": 7,
        "name": "test-policy",
        "enabled": True,
        "schedule_type": "interval",
        "interval_minutes": 5,
        "time_of_day": None,
        "jitter_minutes": 0,
        "max_retries": 2,
        "retry_backoff_minutes": [1, 3],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class SchedulingTests(unittest.TestCase):
    def test_interval_schedule_skips_missed_slots_without_drift(self):
        start = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        result = calculate_next_run(
            policy(),
            after=start,
            now=start + timedelta(minutes=12),
            target_key=11,
        )
        self.assertEqual(result, datetime(2026, 8, 12, 12, 15, tzinfo=UTC))

    def test_daily_schedule_uses_local_timezone(self):
        result = calculate_next_run(
            policy(schedule_type="daily", interval_minutes=None, time_of_day=time(15, 0)),
            after=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
            target_key=11,
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 12, 13, 0, tzinfo=UTC))

    def test_daily_schedule_rolls_to_next_day(self):
        result = calculate_next_run(
            policy(schedule_type="daily", interval_minutes=None, time_of_day=time(15, 0)),
            after=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            target_key=11,
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(result, datetime(2026, 8, 13, 13, 0, tzinfo=UTC))

    def test_jitter_is_stable_for_target_and_day(self):
        daily = policy(
            schedule_type="daily",
            interval_minutes=None,
            time_of_day=time(15, 0),
            jitter_minutes=20,
        )
        kwargs = {
            "after": datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
            "target_key": 99,
            "timezone_name": "UTC",
        }
        first = calculate_next_run(daily, **kwargs)
        second = calculate_next_run(daily, **kwargs)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first.minute, 0)
        self.assertLessEqual(first.minute, 20)

    def test_retry_backoff_then_regular_schedule(self):
        failed_at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        configured = policy()
        self.assertEqual(
            calculate_failure_next_run(
                configured,
                failed_at=failed_at,
                consecutive_failures=1,
                target_key=11,
            ),
            failed_at + timedelta(minutes=1),
        )
        self.assertEqual(
            calculate_failure_next_run(
                configured,
                failed_at=failed_at,
                consecutive_failures=2,
                target_key=11,
            ),
            failed_at + timedelta(minutes=3),
        )
        self.assertEqual(
            calculate_failure_next_run(
                configured,
                failed_at=failed_at,
                consecutive_failures=3,
                target_key=11,
            ),
            failed_at + timedelta(minutes=5),
        )
        self.assertTrue(is_retry_scheduled(configured, 2))
        self.assertFalse(is_retry_scheduled(configured, 3))


if __name__ == "__main__":
    unittest.main()
