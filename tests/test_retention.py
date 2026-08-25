import unittest
from datetime import UTC, datetime, timedelta

from netbox_config_backup.services.retention import (
    RetentionSettings,
    RevisionCandidate,
    RunCandidate,
    build_retention_plan,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def settings(**overrides):
    values = {
        "keep_all_days": 7,
        "daily_days": 30,
        "weekly_weeks": 12,
        "monthly_months": 12,
        "minimum_changed_revisions": 2,
        "unchanged_run_days": 30,
        "changed_run_days": 90,
        "failed_run_days": 60,
        "max_runs_per_target": 500,
    }
    values.update(overrides)
    return RetentionSettings(**values)


def revision(object_id, *, days_ago, hour=10, protected=False, changed=True):
    timestamp = (NOW - timedelta(days=days_ago)).replace(hour=hour)
    return RevisionCandidate(object_id, timestamp, protected, changed)


def run(object_id, *, days_ago, status):
    return RunCandidate(object_id, NOW - timedelta(days=days_ago), status)


class RetentionRevisionTests(unittest.TestCase):
    def test_latest_revision_is_always_kept_when_all_rules_are_disabled(self):
        plan = build_retention_plan(
            settings(
                keep_all_days=0,
                daily_days=0,
                weekly_weeks=0,
                monthly_months=0,
                minimum_changed_revisions=0,
            ),
            revisions=[revision(1, days_ago=100), revision(2, days_ago=200)],
            runs=[],
            now=NOW,
        )
        decisions = {item.object_id: item for item in plan.revision_decisions}
        self.assertTrue(decisions[1].keep)
        self.assertIn("Latest revision", decisions[1].reasons)
        self.assertFalse(decisions[2].keep)

    def test_protected_and_minimum_changed_revisions_are_kept(self):
        candidates = [
            revision(1, days_ago=100, changed=False),
            revision(2, days_ago=110, changed=True),
            revision(3, days_ago=120, changed=False),
            revision(4, days_ago=130, changed=True),
            revision(5, days_ago=500, changed=False, protected=True),
            revision(6, days_ago=600, changed=True),
        ]
        plan = build_retention_plan(
            settings(
                keep_all_days=0,
                daily_days=0,
                weekly_weeks=0,
                monthly_months=0,
                minimum_changed_revisions=2,
            ),
            revisions=candidates,
            runs=[],
            now=NOW,
        )
        decisions = {item.object_id: item for item in plan.revision_decisions}
        self.assertTrue(decisions[2].keep)
        self.assertTrue(decisions[4].keep)
        self.assertTrue(decisions[5].keep)
        self.assertFalse(decisions[6].keep)

    def test_recent_revisions_and_one_latest_sample_per_bucket_are_kept(self):
        candidates = [
            revision(1, days_ago=1, hour=12),
            revision(2, days_ago=1, hour=8),
            revision(3, days_ago=10, hour=12),
            revision(4, days_ago=10, hour=8),
            revision(5, days_ago=45, hour=12),
            revision(6, days_ago=45, hour=8),
            revision(7, days_ago=150, hour=12),
            revision(8, days_ago=150, hour=8),
            revision(9, days_ago=500),
        ]
        plan = build_retention_plan(
            settings(minimum_changed_revisions=0),
            revisions=candidates,
            runs=[],
            now=NOW,
        )
        decisions = {item.object_id: item for item in plan.revision_decisions}
        self.assertTrue(decisions[1].keep)
        self.assertTrue(decisions[2].keep)  # keep-all window keeps both
        self.assertTrue(decisions[3].keep)
        self.assertFalse(decisions[4].keep)  # only newest daily sample
        self.assertTrue(decisions[5].keep)
        self.assertFalse(decisions[6].keep)  # only newest weekly sample
        self.assertTrue(decisions[7].keep)
        self.assertFalse(decisions[8].keep)  # only newest monthly sample
        self.assertFalse(decisions[9].keep)

    def test_naive_datetimes_are_rejected(self):
        with self.assertRaises(ValueError):
            build_retention_plan(
                settings(),
                revisions=[RevisionCandidate(1, datetime(2026, 1, 1), False, True)],
                runs=[],
                now=NOW,
            )


class RetentionRunTests(unittest.TestCase):
    def test_run_windows_are_applied_by_status(self):
        plan = build_retention_plan(
            settings(),
            revisions=[],
            runs=[
                run(1, days_ago=29, status="success_unchanged"),
                run(2, days_ago=31, status="success_unchanged"),
                run(3, days_ago=89, status="success_changed"),
                run(4, days_ago=91, status="success_changed"),
                run(5, days_ago=59, status="failed"),
                run(6, days_ago=61, status="errored"),
            ],
            now=NOW,
        )
        decisions = {item.object_id: item for item in plan.run_decisions}
        self.assertTrue(decisions[1].keep)
        self.assertFalse(decisions[2].keep)
        self.assertTrue(decisions[3].keep)
        self.assertFalse(decisions[4].keep)
        self.assertTrue(decisions[5].keep)
        self.assertFalse(decisions[6].keep)

    def test_active_and_unknown_runs_are_kept_safely(self):
        plan = build_retention_plan(
            settings(unchanged_run_days=0, changed_run_days=0, failed_run_days=0),
            revisions=[],
            runs=[
                run(1, days_ago=500, status="queued"),
                run(2, days_ago=500, status="running"),
                run(3, days_ago=500, status="future_status"),
            ],
            now=NOW,
        )
        self.assertTrue(all(item.keep for item in plan.run_decisions))

    def test_completed_runs_are_capped_per_target_newest_first(self):
        plan = build_retention_plan(
            settings(max_runs_per_target=3),
            revisions=[],
            runs=[
                run(1, days_ago=1, status="success_unchanged"),
                run(2, days_ago=2, status="success_changed"),
                run(3, days_ago=3, status="failed"),
                run(4, days_ago=4, status="success_unchanged"),
                run(5, days_ago=5, status="failed"),
            ],
            now=NOW,
        )

        decisions = {item.object_id: item for item in plan.run_decisions}
        self.assertTrue(decisions[1].keep)
        self.assertTrue(decisions[2].keep)
        self.assertTrue(decisions[3].keep)
        self.assertFalse(decisions[4].keep)
        self.assertFalse(decisions[5].keep)
        self.assertEqual(decisions[4].reasons, ("Per-target run limit exceeded",))

    def test_active_and_unknown_runs_do_not_consume_the_completed_run_limit(self):
        plan = build_retention_plan(
            settings(max_runs_per_target=1),
            revisions=[],
            runs=[
                run(1, days_ago=0, status="running"),
                run(2, days_ago=0, status="future_status"),
                run(3, days_ago=1, status="success_changed"),
                run(4, days_ago=2, status="success_changed"),
            ],
            now=NOW,
        )

        decisions = {item.object_id: item for item in plan.run_decisions}
        self.assertTrue(decisions[1].keep)
        self.assertTrue(decisions[2].keep)
        self.assertTrue(decisions[3].keep)
        self.assertFalse(decisions[4].keep)


if __name__ == "__main__":
    unittest.main()
