import unittest

from netbox_config_backup.metrics import normalize_error_code


class MetricsTests(unittest.TestCase):
    def test_known_error_code_is_preserved(self):
        self.assertEqual(normalize_error_code("AUTH_FAILED"), "AUTH_FAILED")

    def test_unknown_error_code_is_bounded(self):
        self.assertEqual(normalize_error_code("VENDOR_DEFINED_123"), "other")
