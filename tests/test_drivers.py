import unittest

from netbox_config_backup.drivers.base import DriverContext, DriverError
from netbox_config_backup.drivers.fake import FakeDriver
from netbox_config_backup.drivers.registry import DriverRegistry, DriverRegistryError


class ExternalFakeDriver(FakeDriver):
    driver_id = "external_fake"


class FutureExternalFakeDriver(ExternalFakeDriver):
    driver_id = "future_fake"
    driver_api_version = 999


class EntryPoint:
    def __init__(self, name, value=None, error=None):
        self.name = name
        self.value = value
        self.error = error

    def load(self):
        if self.error:
            raise self.error
        return self.value


class DriverTests(unittest.TestCase):
    def test_registry_creates_fake_driver(self):
        registry = DriverRegistry()
        registry.register(FakeDriver)

        driver = registry.create("fake")

        self.assertIsInstance(driver, FakeDriver)
        self.assertEqual(registry.ids(), ("fake",))

    def test_registry_rejects_duplicates_and_unknown_ids(self):
        registry = DriverRegistry()
        registry.register(FakeDriver)
        with self.assertRaises(ValueError):
            registry.register(FakeDriver)
        with self.assertRaises(DriverRegistryError):
            registry.create("missing")

    def test_registry_loads_compatible_external_entry_point(self):
        registry = DriverRegistry()

        loaded = registry.load_entry_points([EntryPoint("external_fake", ExternalFakeDriver)])

        self.assertEqual(loaded, ("external_fake",))
        self.assertIsInstance(registry.create("external_fake"), ExternalFakeDriver)

    def test_registry_rejects_invalid_external_entry_points(self):
        cases = (
            EntryPoint("wrong_name", ExternalFakeDriver),
            EntryPoint("future_fake", FutureExternalFakeDriver),
            EntryPoint("not_a_driver", object),
            EntryPoint("load_error", error=ImportError("private module detail")),
        )
        for entry in cases:
            with self.subTest(entry=entry.name), self.assertRaises(DriverRegistryError):
                DriverRegistry().load_entry_points([entry])

    def test_fake_driver_is_deterministic_and_normalizes_volatile_line(self):
        driver = FakeDriver()
        first = driver.collect(
            DriverContext(
                device_id=1,
                device_name="router-1",
                options={
                    "config": ("! Last configuration change at 10:00\r\nhostname router-1  \r\n")
                },
            )
        )[0]
        second = driver.collect(
            DriverContext(
                device_id=1,
                device_name="router-1",
                options={"config": ("! Last configuration change at 11:00\nhostname router-1\n")},
            )
        )[0]

        self.assertEqual(driver.normalize(first), driver.normalize(second))

    def test_fake_driver_can_simulate_expected_failure(self):
        driver = FakeDriver()
        with self.assertRaises(DriverError) as raised:
            driver.collect(
                DriverContext(
                    device_id=1,
                    device_name="router-1",
                    options={"failure_code": "CONNECTION_TIMEOUT"},
                )
            )
        self.assertEqual(raised.exception.error_code, "CONNECTION_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
