import unittest
from contextlib import contextmanager
from pathlib import Path

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import CollectedArtifact, DriverContext, DriverError
from netbox_config_backup.drivers.cisco_ios import CiscoIOSDriver, CiscoIOSXEDriver
from netbox_config_backup.transports import LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS
from netbox_config_backup.transports.netmiko import SSH_DISABLED_ALGORITHMS

VALID_CONFIG = (Path(__file__).parent / "fixtures" / "cisco_ios_xe_running_config.txt").read_text(
    encoding="utf-8"
)


class RecordingSession:
    def __init__(self, output):
        self.output = output
        self.commands = []
        self.enable_calls = 0

    def enable(self):
        self.enable_calls += 1
        return ""

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


def make_context(*, enable_secret=None, options=None):
    return DriverContext(
        device_id=1,
        device_name="edge-1",
        address="192.0.2.10",
        credentials=CredentialMaterial(
            username="backup",
            password="secret",
            enable_secret=enable_secret,
        ),
        options=options or {},
    )


def make_artifact(content):
    return CollectedArtifact(
        artifact_type="running_config",
        filename="running-config.cfg",
        content=content,
        format="cisco_ios_config",
        is_primary=True,
    )


class CiscoIOSDriverTests(unittest.TestCase):
    def test_ios_and_ios_xe_drivers_are_registered(self):
        cases = (
            ("cisco_ios", CiscoIOSDriver),
            ("cisco_xe", CiscoIOSXEDriver),
        )
        for driver_id, driver_class in cases:
            with self.subTest(driver_id=driver_id):
                self.assertIsInstance(driver_registry.create(driver_id), driver_class)

    def test_legacy_rsa_host_key_exception_is_scoped_to_ios(self):
        ios = CiscoIOSDriver()
        ios_xe = CiscoIOSXEDriver()

        self.assertEqual(
            ios.transport._disabled_algorithms,
            LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
        )
        self.assertNotIn("keys", ios.transport._disabled_algorithms)
        self.assertEqual(ios_xe.transport._disabled_algorithms, SSH_DISABLED_ALGORITHMS)

    def test_collect_uses_correct_netmiko_platform_and_read_only_command(self):
        cases = (
            (CiscoIOSDriver, "cisco_ios"),
            (CiscoIOSXEDriver, "cisco_xe"),
        )
        for driver_class, device_type in cases:
            transport = RecordingTransport(VALID_CONFIG)
            driver = driver_class(transport)
            context = make_context()

            artifact = driver.collect(context)[0]

            with self.subTest(device_type=device_type):
                self.assertEqual(transport.opens, [(device_type, context)])
                self.assertEqual(
                    transport.session.commands,
                    [
                        (
                            "show running-config",
                            {"strip_command": True, "strip_prompt": True},
                        )
                    ],
                )
                self.assertEqual(transport.session.enable_calls, 0)
                self.assertEqual(artifact.filename, "running-config.cfg")
                self.assertEqual(artifact.format, "cisco_ios_config")
                self.assertTrue(artifact.is_primary)
                self.assertTrue(driver.validate(artifact).valid)

    def test_enable_mode_is_requested_only_when_secret_is_available(self):
        transport = RecordingTransport(VALID_CONFIG)

        CiscoIOSDriver(transport).collect(make_context(enable_secret="enable-secret"))

        self.assertEqual(transport.session.enable_calls, 1)

    def test_validation_rejects_empty_rejected_incomplete_and_invalid_output(self):
        cases = (
            (b"", "EMPTY_CONFIG"),
            (b"% Authorization failed.\n", "COMMAND_REJECTED"),
            (b"hostname edge-1\nend\n", "INCOMPLETE_CONFIG"),
            (b"version \xff\n", "INVALID_OUTPUT"),
        )
        driver = CiscoIOSDriver(RecordingTransport(""))
        for content, error_code in cases:
            with self.subTest(error_code=error_code):
                result = driver.validate(make_artifact(content))
                self.assertFalse(result.valid)
                self.assertEqual(result.error_code, error_code)

    def test_collect_rejects_non_text_and_oversized_output(self):
        with self.assertRaises(DriverError) as raised:
            CiscoIOSDriver(RecordingTransport(b"binary")).collect(make_context())
        self.assertEqual(raised.exception.error_code, "INVALID_OUTPUT")

        with self.assertRaises(DriverError) as raised:
            CiscoIOSDriver(RecordingTransport(VALID_CONFIG)).collect(
                make_context(options={"max_output_bytes": 10})
            )
        self.assertEqual(raised.exception.error_code, "CONFIG_TOO_LARGE")

    def test_invalid_size_options_are_rejected_before_connecting(self):
        for value in (0, -1, True, "100", 50 * 1024 * 1024 + 1):
            transport = RecordingTransport(VALID_CONFIG)
            with self.subTest(value=value), self.assertRaises(DriverError) as raised:
                CiscoIOSDriver(transport).collect(make_context(options={"max_output_bytes": value}))
            self.assertEqual(raised.exception.error_code, "INVALID_DRIVER_OPTIONS")
            self.assertEqual(transport.opens, [])

        transport = RecordingTransport(VALID_CONFIG)
        with self.assertRaises(DriverError) as raised:
            CiscoIOSDriver(transport).collect(
                make_context(options={"command": "show startup-config"})
            )
        self.assertEqual(raised.exception.error_code, "INVALID_DRIVER_OPTIONS")
        self.assertEqual(transport.opens, [])

    def test_normalizer_removes_only_volatile_show_headers(self):
        first = make_artifact(VALID_CONFIG.replace("\n", "\r\n").encode())
        second = make_artifact(
            VALID_CONFIG.replace("412 bytes", "999 bytes")
            .replace("12:00:00 UTC Sun Aug 16 2026", "13:00:00 UTC Mon Aug 17 2026")
            .encode()
        )
        driver = CiscoIOSDriver(RecordingTransport(""))

        self.assertEqual(driver.normalize(first), driver.normalize(second))
        self.assertNotIn(b"Current configuration", driver.normalize(first))
        self.assertNotIn(b"Last configuration change", driver.normalize(first))

        changed = make_artifact(
            second.content.replace(b"description WAN uplink", b"description LAN uplink")
        )
        self.assertNotEqual(driver.normalize(first), driver.normalize(changed))

    def test_display_redaction_masks_common_ios_secrets(self):
        config = """username admin privilege 15 secret 9 username-hash
enable secret 5 enable-hash
snmp-server community public RO
neighbor 192.0.2.1 password 7 bgp-secret
crypto isakmp key vpn-secret address 198.51.100.1
tacacs-server key 7 tacacs-secret
pre-shared-key address 203.0.113.1 key tunnel-secret
"""

        rendered = CiscoIOSDriver(RecordingTransport("")).redact_for_display(config)

        for secret in (
            "username-hash",
            "enable-hash",
            "public",
            "bgp-secret",
            "vpn-secret",
            "tacacs-secret",
            "tunnel-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(rendered.count("<redacted>"), 7)


if __name__ == "__main__":
    unittest.main()
