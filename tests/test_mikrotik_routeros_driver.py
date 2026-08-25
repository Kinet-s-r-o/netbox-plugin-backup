import unittest
from contextlib import contextmanager

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import CollectedArtifact, DriverContext, DriverError
from netbox_config_backup.drivers.mikrotik_routeros import MikroTikRouterOSDriver


class RecordingSession:
    def __init__(self, output):
        self.output = output
        self.commands = []

    def send_command(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return self.output


class RecordingTransport:
    def __init__(self, output):
        self.session = RecordingSession(output)
        self.opens = []

    @contextmanager
    def open(self, *, device_type, context):
        self.opens.append((device_type, context))
        yield self.session


def make_context(**overrides):
    values = {
        "device_id": 1,
        "device_name": "router-1",
        "address": "192.0.2.10",
        "credentials": CredentialMaterial(username="backup", password="secret"),
    }
    values.update(overrides)
    return DriverContext(**values)


class MikroTikRouterOSDriverTests(unittest.TestCase):
    def test_driver_is_registered(self):
        driver = driver_registry.create("mikrotik_routeros")

        self.assertIsInstance(driver, MikroTikRouterOSDriver)

    def test_collect_uses_read_only_secret_hidden_export(self):
        transport = RecordingTransport(
            "# 2026-08-13 12:00:00 by RouterOS 7.20\n/system identity set name=router-1\n"
        )
        driver = MikroTikRouterOSDriver(transport)
        context = make_context()

        artifacts = driver.collect(context)

        self.assertEqual(transport.opens, [("mikrotik_routeros", context)])
        self.assertEqual(
            transport.session.commands,
            [
                (
                    "/export terse hide-sensitive",
                    {"strip_command": True, "strip_prompt": True},
                )
            ],
        )
        artifact = artifacts[0]
        self.assertEqual(artifact.filename, "running-config.rsc")
        self.assertEqual(artifact.format, "routeros_script")
        self.assertTrue(artifact.is_primary)
        self.assertEqual(artifact.metadata["sensitive"], "hidden")
        self.assertTrue(driver.validate(artifact).valid)

    def test_validation_rejects_empty_comment_only_and_partial_exports(self):
        cases = (
            ("", "EMPTY_CONFIG"),
            ("# RouterOS export header only\n", "EMPTY_CONFIG"),
            (
                (
                    "/system identity set name=router-1\n"
                    '#error exporting "/routing/filter/rule" (timeout)\n'
                ),
                "PARTIAL_CONFIG",
            ),
        )
        for output, error_code in cases:
            with self.subTest(error_code=error_code):
                driver = MikroTikRouterOSDriver(RecordingTransport(output))
                artifact = driver.collect(make_context())[0]

                result = driver.validate(artifact)

                self.assertFalse(result.valid)
                self.assertEqual(result.error_code, error_code)

    def test_collect_rejects_non_text_and_oversized_output(self):
        non_text = MikroTikRouterOSDriver(RecordingTransport(b"binary"))
        with self.assertRaises(DriverError) as raised:
            non_text.collect(make_context())
        self.assertEqual(raised.exception.error_code, "INVALID_OUTPUT")

        oversized = MikroTikRouterOSDriver(
            RecordingTransport("/system identity set name=router-1\n")
        )
        with self.assertRaises(DriverError) as raised:
            oversized.collect(make_context(options={"max_output_bytes": 10}))
        self.assertEqual(raised.exception.error_code, "CONFIG_TOO_LARGE")

    def test_invalid_size_options_are_rejected_before_connecting(self):
        for value in (0, -1, True, "100", 50 * 1024 * 1024 + 1):
            transport = RecordingTransport("/system identity set name=router-1\n")
            with self.subTest(value=value), self.assertRaises(DriverError) as raised:
                MikroTikRouterOSDriver(transport).collect(
                    make_context(options={"max_output_bytes": value})
                )
            self.assertEqual(raised.exception.error_code, "INVALID_DRIVER_OPTIONS")
            self.assertEqual(transport.opens, [])

    def test_normalizer_removes_only_volatile_export_timestamp(self):
        driver = MikroTikRouterOSDriver(RecordingTransport(""))
        first = CollectedArtifact(
            artifact_type="running_config",
            filename="running-config.rsc",
            content=(
                b"# aug/13/2026 12:00:00 by RouterOS 6.49.18\r\n"
                b"# model = RB750Gr3\r\n"
                b"/system identity set name=router-1  \r\n"
            ),
            format="routeros_script",
            is_primary=True,
        )
        second = CollectedArtifact(
            artifact_type="running_config",
            filename="running-config.rsc",
            content=(
                b"# 2026-08-13 13:30:00 by RouterOS 6.49.18\n"
                b"# model = RB750Gr3\n"
                b"/system identity set name=router-1\n"
            ),
            format="routeros_script",
            is_primary=True,
        )

        self.assertEqual(driver.normalize(first), driver.normalize(second))
        self.assertIn(b"# by RouterOS 6.49.18", driver.normalize(first))
        self.assertIn(b"# model = RB750Gr3", driver.normalize(first))

        upgraded = CollectedArtifact(
            artifact_type="running_config",
            filename="running-config.rsc",
            content=second.content.replace(b"RouterOS 6.49.18", b"RouterOS 7.20"),
            format="routeros_script",
            is_primary=True,
        )
        self.assertNotEqual(driver.normalize(first), driver.normalize(upgraded))

    def test_display_redaction_masks_common_secret_assignments(self):
        driver = MikroTikRouterOSDriver(RecordingTransport(""))
        rendered = driver.redact_for_display(
            '/ppp secret add name=user password="very secret"\n'
            "/interface wireguard set private-key=private-value\n"
        )

        self.assertNotIn("very secret", rendered)
        self.assertNotIn("private-value", rendered)
        self.assertEqual(rendered.count("<redacted>"), 2)


if __name__ == "__main__":
    unittest.main()
