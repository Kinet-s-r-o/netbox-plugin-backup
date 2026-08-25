import unittest
from datetime import UTC, datetime, timedelta

from netbox_config_backup.services.reporting_period import resolve_reporting_period


class ReportingPeriodTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    def test_defaults_to_last_30_days(self):
        period = resolve_reporting_period({}, now=self.now)

        self.assertEqual(period.key, "30d")
        self.assertEqual(period.start, self.now - timedelta(days=30))
        self.assertGreater(period.end, self.now)

    def test_preset_and_all_time_periods(self):
        day = resolve_reporting_period({"period": "24h"}, now=self.now)
        all_time = resolve_reporting_period({"period": "all"}, now=self.now)

        self.assertEqual(day.start, self.now - timedelta(hours=24))
        self.assertIsNone(all_time.start)
        self.assertIsNone(all_time.end)

    def test_custom_dates_include_the_whole_end_day(self):
        period = resolve_reporting_period(
            {
                "period": "custom",
                "date_from": "2026-08-01",
                "date_to": "2026-08-03",
            },
            now=self.now,
        )

        self.assertEqual(period.key, "custom")
        self.assertEqual(period.start.date().isoformat(), "2026-08-01")
        self.assertEqual(period.end.date().isoformat(), "2026-08-04")
        self.assertEqual(
            period.query_string,
            "period=custom&date_from=2026-08-01&date_to=2026-08-03",
        )

    def test_invalid_custom_dates_fail_safely_to_30_days(self):
        period = resolve_reporting_period(
            {
                "period": "custom",
                "date_from": "2026-08-10",
                "date_to": "2026-08-01",
            },
            now=self.now,
        )

        self.assertEqual(period.key, "30d")
        self.assertTrue(period.error)


if __name__ == "__main__":
    unittest.main()
