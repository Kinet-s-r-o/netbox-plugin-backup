import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from netbox_config_backup.services.health import (
    RUN_ERRORED,
    RUN_QUEUED,
    RUN_RUNNING,
    TARGET_DISABLED,
    TARGET_FAILED,
    TARGET_HEALTHY,
    TARGET_NEVER,
    TARGET_STALE,
    evaluate_target_health,
    is_run_stuck,
)


@dataclass
class Policy:
    enabled: bool = True
    schedule_type: str = "interval"
    interval_minutes: int | None = 60
    time_of_day: object | None = None
    timezone_mode: str = "site"
    jitter_minutes: int = 0
    pk: int = 7


@dataclass
class Site:
    time_zone: str = "UTC"


@dataclass
class Device:
    site: Site


@dataclass
class Target:
    enabled: bool
    status: str
    consecutive_failures: int
    last_success_at: datetime | None
    created: datetime
    policy_override: Policy | None
    device: Device
    pk: int = 11
    device_id: int = 22


@dataclass
class Run:
    status: str
    queued_at: datetime
    started_at: datetime | None = None


class TargetHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        self.target = Target(
            enabled=True,
            status=TARGET_HEALTHY,
            consecutive_failures=0,
            last_success_at=self.now - timedelta(minutes=30),
            created=self.now - timedelta(days=10),
            policy_override=Policy(),
            device=Device(site=Site()),
        )

    def test_healthy_target_has_expected_success_deadline(self):
        result = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(result.status, TARGET_HEALTHY)
        self.assertEqual(
            result.expected_success_by,
            self.target.last_success_at + timedelta(minutes=75),
        )
        self.assertTrue(result.monitored)

    def test_target_becomes_stale_after_schedule_and_grace(self):
        self.target.last_success_at = self.now - timedelta(minutes=76)
        result = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(result.status, TARGET_STALE)
        self.assertEqual(
            result.expected_success_by,
            self.target.last_success_at + timedelta(minutes=75),
        )

    def test_new_target_is_never_then_stale_after_first_deadline(self):
        self.target.status = TARGET_NEVER
        self.target.last_success_at = None
        self.target.created = self.now - timedelta(minutes=30)
        fresh = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(fresh.status, TARGET_NEVER)

        self.target.created = self.now - timedelta(minutes=76)
        stale = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(stale.status, TARGET_STALE)

    def test_failed_and_disabled_states_take_precedence(self):
        self.target.last_success_at = self.now - timedelta(days=5)
        self.target.status = TARGET_FAILED
        self.target.consecutive_failures = 2
        failed = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(failed.status, TARGET_FAILED)
        self.assertIsNone(failed.expected_success_by)

        self.target.enabled = False
        disabled = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(disabled.status, TARGET_DISABLED)

    def test_target_without_policy_is_not_classified_as_stale(self):
        self.target.policy_override = None
        self.target.last_success_at = None
        result = evaluate_target_health(
            self.target,
            now=self.now,
            grace_minutes=15,
        )
        self.assertEqual(result.status, TARGET_NEVER)
        self.assertFalse(result.monitored)

    def test_health_inputs_must_be_safe(self):
        with self.assertRaises(ValueError):
            evaluate_target_health(
                self.target,
                now=self.now.replace(tzinfo=None),
                grace_minutes=15,
            )
        with self.assertRaises(ValueError):
            evaluate_target_health(
                self.target,
                now=self.now,
                grace_minutes=-1,
            )


class StuckRunTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

    def test_old_queued_and_running_runs_are_stuck(self):
        queued = Run(
            status=RUN_QUEUED,
            queued_at=self.now - timedelta(minutes=121),
        )
        running = Run(
            status=RUN_RUNNING,
            queued_at=self.now - timedelta(minutes=180),
            started_at=self.now - timedelta(minutes=121),
        )
        self.assertTrue(is_run_stuck(queued, now=self.now, timeout_minutes=120))
        self.assertTrue(is_run_stuck(running, now=self.now, timeout_minutes=120))

    def test_active_recent_and_completed_runs_are_not_stuck(self):
        recent = Run(
            status=RUN_RUNNING,
            queued_at=self.now - timedelta(minutes=10),
            started_at=self.now - timedelta(minutes=5),
        )
        completed = Run(
            status=RUN_ERRORED,
            queued_at=self.now - timedelta(days=1),
            started_at=self.now - timedelta(days=1),
        )
        self.assertFalse(is_run_stuck(recent, now=self.now, timeout_minutes=120))
        self.assertFalse(is_run_stuck(completed, now=self.now, timeout_minutes=120))

    def test_running_without_started_time_uses_queued_time(self):
        run = Run(
            status=RUN_RUNNING,
            queued_at=self.now - timedelta(minutes=121),
        )
        self.assertTrue(is_run_stuck(run, now=self.now, timeout_minutes=120))


if __name__ == "__main__":
    unittest.main()
