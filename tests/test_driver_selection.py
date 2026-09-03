import unittest
from collections import Counter
from unittest.mock import patch

from django.core.exceptions import ValidationError

from netbox_config_backup.drivers.base import DriverError
from netbox_config_backup.drivers.fake import FakeDriver
from netbox_config_backup.drivers.registry import DriverRegistry
from netbox_config_backup.services import driver_selection as selection


class DriverSelectionTests(unittest.TestCase):
    def setUp(self):
        translator = patch.object(selection, "_", side_effect=lambda message: message)
        translator.start()
        self.addCleanup(translator.stop)

    def test_legacy_siae_ids_share_one_selection(self):
        for driver_id in selection.SIAE_COMPATIBILITY_IDS | {"siae_smos_auto"}:
            self.assertFalse(selection.driver_is_enabled(driver_id, {"siae_smos_auto"}))
            self.assertFalse(selection.driver_is_enabled(driver_id, {"siae_alfoplus"}))
        self.assertTrue(selection.driver_is_enabled("cisco_ios", {"siae_smos_auto"}))

    def test_all_enabled_is_backwards_compatible(self):
        self.assertTrue(selection.driver_is_enabled("external_driver", []))
        selection.validate_disabled_drivers([])

    def test_in_use_and_legacy_assignments_cannot_be_disabled(self):
        with patch.object(selection, "driver_usage_counts", return_value=Counter(siae_smos_auto=2)), (
            patch.object(selection, "selectable_drivers", return_value=[])
        ):
            for values in (["siae_smos_auto"], ["siae_alfoplus"]):
                with self.assertRaises(ValidationError):
                    selection.validate_disabled_drivers(values)
            selection.validate_disabled_drivers(["cisco_ios"])

    def test_invalid_setting_is_rejected(self):
        for value in (None, "fake", {"fake": True}, [123]):
            with self.assertRaises(ValidationError):
                selection.validate_disabled_drivers(value)

    def test_assignment_gate(self):
        selection.validate_driver_assignment("", field="driver_id", disabled=["fake"])
        with self.assertRaises(ValidationError) as failure:
            selection.validate_driver_assignment("fake", field="driver_id", disabled=["fake"])
        self.assertIn("driver_id", failure.exception.message_dict)

    def test_execution_gate_does_not_unload_history_drivers(self):
        registry = DriverRegistry()
        registry.register(FakeDriver)
        execution = selection.EnabledDriverRegistry(registry)
        with patch.object(selection, "disabled_driver_ids", return_value={"fake"}):
            with self.assertRaises(DriverError) as failure:
                execution.create("fake")
            self.assertEqual(failure.exception.error_code, "DRIVER_DISABLED")
            self.assertIsInstance(registry.create("fake"), FakeDriver)
        with patch.object(selection, "disabled_driver_ids", return_value=set()):
            self.assertIsInstance(execution.create("fake"), FakeDriver)
