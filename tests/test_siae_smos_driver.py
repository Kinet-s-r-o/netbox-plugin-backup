import unittest
from contextlib import contextmanager
from unittest.mock import patch

from netmiko.exceptions import NetmikoAuthenticationException

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import (
    CollectedArtifact,
    ConnectionParameters,
    DriverContext,
    DriverError,
)
from netbox_config_backup.drivers.siae_smos import (
    SIAE_LEGACY_SSH_DISABLED_ALGORITHMS,
    SiaeSmosAutoDriver,
    SiaeSmosCliDriver,
    SiaeSmosSSH,
    SiaeSmosSSHDriver,
    SiaeSmosTelnet,
)

VALID_CONFIG = """#Building configuration...
!
version N60052.01.06.00
hostname SIAE-LINK-A
interface extreme-ethernet 0/1
 description RADIO-UPLINK
 no shutdown
!
end
"""


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


def make_context(*, password="secret", connection=None, options=None):
    return DriverContext(
        device_id=1,
        device_name="siae-radio",
        address="192.0.2.10",
        credentials=CredentialMaterial(username="backup", password=password),
        connection=connection or ConnectionParameters(),
        options=options or {},
    )


def make_artifact(content: bytes):
    return CollectedArtifact(
        artifact_type="running_config",
        filename="siae-smos-running-config.cfg",
        content=content,
        format="siae_smos_running_config",
        is_primary=True,
    )


class SiaeSmosCliDriverTests(unittest.TestCase):
    def make_login_connection(self, reads):
        connection = object.__new__(SiaeSmosTelnet)
        connection.username = "backup-user"
        connection.password = "backup-password"
        connection._legacy_mode = False
        connection.fast_cli = True
        connection.remote_conn = type("Remote", (), {"close": lambda self: None})()
        connection.select_delay_factor = lambda _value: 1
        values = iter(reads)
        connection.read_channel = lambda: next(values, "")
        connection.writes = []
        connection.write_channel = connection.writes.append
        return connection

    def test_driver_is_registered_and_default_transport_uses_minimal_telnet_session(self):
        driver = driver_registry.create("siae_smos_cli")

        self.assertIsInstance(driver, SiaeSmosCliDriver)
        self.assertIs(driver.transport._connector, SiaeSmosTelnet)
        self.assertIn("plaintext_transport", driver.capabilities)
        self.assertFalse(driver.user_selectable)

    def test_ssh_driver_is_registered_with_scoped_legacy_host_key_support(self):
        driver = driver_registry.create("siae_smos_ssh")

        self.assertIsInstance(driver, SiaeSmosSSHDriver)
        self.assertIs(driver.transport._connector, SiaeSmosSSH)
        self.assertEqual(
            driver.transport._disabled_algorithms,
            SIAE_LEGACY_SSH_DISABLED_ALGORITHMS,
        )
        self.assertNotIn("keys", driver.transport._disabled_algorithms)
        self.assertIn("ssh", driver.capabilities)
        self.assertFalse(driver.user_selectable)

    def test_only_unified_siae_driver_is_user_selectable(self):
        selectable = {
            driver.driver_id
            for driver in driver_registry.classes()
            if driver.user_selectable and driver.driver_id.startswith("siae_")
        }

        self.assertEqual(selectable, {"siae_smos_auto"})
        self.assertEqual(
            driver_registry.create("siae_smos_auto").display_name,
            "SIAE SM-OS (automatic backup)",
        )

    def test_auto_driver_selects_ssh_or_telnet_from_connection_protocol(self):
        ssh_transport = RecordingTransport(VALID_CONFIG)
        telnet_transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosAutoDriver(
            ssh_driver=SiaeSmosSSHDriver(ssh_transport),
            telnet_driver=SiaeSmosCliDriver(telnet_transport),
        )

        driver.collect(make_context(connection=ConnectionParameters(protocol="ssh", port=2022)))
        driver.collect(make_context(connection=ConnectionParameters(protocol="telnet", port=2323)))

        self.assertEqual(len(ssh_transport.opens), 1)
        self.assertEqual(ssh_transport.opens[0][0], "siae_smos")
        self.assertEqual(len(telnet_transport.opens), 1)
        self.assertEqual(telnet_transport.opens[0][0], "siae_smos_telnet")

    def test_auto_driver_infers_standard_ports_and_requires_protocol_for_custom_port(self):
        ssh_transport = RecordingTransport(VALID_CONFIG)
        telnet_transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosAutoDriver(
            ssh_driver=SiaeSmosSSHDriver(ssh_transport),
            telnet_driver=SiaeSmosCliDriver(telnet_transport),
        )

        driver.collect(make_context(connection=ConnectionParameters(port=22)))
        driver.collect(make_context(connection=ConnectionParameters(port=23)))

        with self.assertRaises(DriverError) as raised:
            driver.collect(make_context(connection=ConnectionParameters(port=2222)))
        self.assertEqual(raised.exception.error_code, "DRIVER_SETUP_REQUIRED")

    def test_auto_driver_uses_configured_native_fallback_for_unsupported_cli(self):
        ssh_transport = RecordingTransport(
            "show running-config\r\nC interp: unknown symbol name 'running'.\r\n"
        )
        native_artifact = CollectedArtifact(
            artifact_type="native_backup",
            filename="configuration.bku",
            content=b"native backup",
            format="siae_alfoplus2_bku",
        )

        class RecordingNativeDriver:
            def __init__(self):
                self.contexts = []

            def collect(self, context):
                self.contexts.append(context)
                return [native_artifact]

        native_driver = RecordingNativeDriver()
        driver = SiaeSmosAutoDriver(
            ssh_driver=SiaeSmosSSHDriver(ssh_transport),
            telnet_driver=SiaeSmosCliDriver(RecordingTransport(VALID_CONFIG)),
        )

        with patch.object(driver, "_native_driver", return_value=native_driver):
            artifacts = driver.collect(
                make_context(
                    connection=ConnectionParameters(protocol="ssh", port=22),
                    options={
                        "native_model": "alfoplus2",
                        "remote_path": "backup/configuration.bku",
                    },
                )
            )

        self.assertEqual(artifacts, [native_artifact])
        self.assertEqual(
            native_driver.contexts[0].options,
            {"remote_path": "backup/configuration.bku"},
        )

    def test_native_method_requires_an_explicit_safe_recipe(self):
        driver = SiaeSmosAutoDriver(
            ssh_driver=SiaeSmosSSHDriver(RecordingTransport(VALID_CONFIG)),
            telnet_driver=SiaeSmosCliDriver(RecordingTransport(VALID_CONFIG)),
        )

        with self.assertRaises(DriverError) as raised:
            driver.collect(
                make_context(
                    options={"backup_method": "native"},
                )
            )

        self.assertEqual(raised.exception.error_code, "DRIVER_SETUP_REQUIRED")

    def test_explicit_siae_driver_rejects_mismatched_connection_protocol(self):
        with self.assertRaises(DriverError) as raised:
            SiaeSmosCliDriver(RecordingTransport(VALID_CONFIG)).collect(
                make_context(connection=ConnectionParameters(protocol="ssh", port=22))
            )
        self.assertEqual(raised.exception.error_code, "PROTOCOL_MISMATCH")

        with self.assertRaises(DriverError) as raised:
            SiaeSmosSSHDriver(RecordingTransport(VALID_CONFIG)).collect(
                make_context(connection=ConnectionParameters(protocol="telnet", port=23))
            )
        self.assertEqual(raised.exception.error_code, "PROTOCOL_MISMATCH")

    def test_ssh_collect_executes_same_read_only_command(self):
        transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosSSHDriver(transport)
        context = make_context()

        artifact = driver.collect(context)[0]

        self.assertEqual(transport.opens, [("siae_smos", context)])
        self.assertEqual(
            transport.session.commands,
            [("show running-config", {"strip_command": True, "strip_prompt": True})],
        )
        self.assertTrue(driver.validate(artifact).valid)

    def test_ssh_session_preparation_only_discovers_prompt(self):
        connection = object.__new__(SiaeSmosSSH)
        calls = []
        connection.find_prompt = lambda **kwargs: calls.append(kwargs) or "SIAE-NEDOZERY#"

        connection.session_preparation()

        self.assertEqual(calls, [{"pattern": r"[>#]\s*$"}])
        self.assertEqual(connection.base_prompt, "SIAE-NEDOZERY")

    def test_collect_executes_only_read_only_running_config_command(self):
        transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosCliDriver(transport)
        context = make_context()

        artifact = driver.collect(context)[0]

        self.assertEqual(transport.opens, [("siae_smos_telnet", context)])
        self.assertEqual(
            transport.session.commands,
            [("show running-config", {"strip_command": True, "strip_prompt": True})],
        )
        self.assertTrue(driver.validate(artifact).valid)
        self.assertEqual(artifact.format, "siae_smos_running_config")

    def test_smos_telnet_login_uses_lf_and_accepts_device_prompt(self):
        connection = self.make_login_connection(("Login: ", "Password: ", "SM-OS#"))

        with patch("netbox_config_backup.drivers.siae_smos.time.sleep"):
            result = connection.telnet_login()

        self.assertEqual(result, "Login: Password: SM-OS#")
        self.assertEqual(
            connection.writes,
            ["backup-user\n", "backup-password\r"],
        )
        self.assertEqual(connection.base_prompt, "SM-OS")

    def test_session_preparation_reuses_prompt_consumed_during_login(self):
        connection = object.__new__(SiaeSmosTelnet)
        connection.base_prompt = "SM-OS"
        connection._test_channel_read = lambda: self.fail("must not read the prompt again")

        connection.session_preparation()

    def test_cleanup_logs_out_and_releases_limited_smos_session(self):
        connection = object.__new__(SiaeSmosTelnet)
        connection.session_log = None
        connection.writes = []
        connection.write_channel = connection.writes.append

        connection.cleanup()

        self.assertEqual(connection.writes, ["logout\r"])

    def test_send_command_advances_smos_pager_with_spaces(self):
        connection = object.__new__(SiaeSmosTelnet)
        connection.base_prompt = "SM-OS"
        connection.normalize_cmd = lambda command: command + "\r\n"
        connection.writes = []
        connection.write_channel = connection.writes.append
        responses = iter(("show running-config\r\nline 1\r\n--More--", "line 2\r\nSM-OS#"))
        connection.read_channel_timing = lambda **_kwargs: next(responses)
        connection._sanitize_output = lambda output, **_kwargs: output.replace(
            "show running-config\r\n", ""
        ).replace("SM-OS#", "")

        output = connection.send_command("show running-config")

        self.assertEqual(connection.writes, ["show running-config\r\n", " "])
        self.assertNotIn("--More--", output)
        self.assertIn("line 1", output)
        self.assertIn("line 2", output)

    def test_smos_telnet_login_rejects_bad_password_without_retrying_secrets(self):
        connection = self.make_login_connection(("Login: ", "Password: ", "Authentication failed"))

        with (
            patch("netbox_config_backup.drivers.siae_smos.time.sleep"),
            self.assertRaises(NetmikoAuthenticationException),
        ):
            connection.telnet_login()

        self.assertEqual(
            connection.writes,
            ["backup-user\n", "backup-password\r"],
        )

    def test_validation_rejects_banner_or_command_error(self):
        driver = SiaeSmosCliDriver(RecordingTransport(""))

        for content, error_code in (
            (b"Welcome to SIAE SM-OS\n", "INCOMPLETE_CONFIG"),
            (b"% Invalid input detected\n", "COMMAND_REJECTED"),
        ):
            with self.subTest(error_code=error_code):
                result = driver.validate(make_artifact(content))
                self.assertFalse(result.valid)
                self.assertEqual(result.error_code, error_code)

    def test_validation_identifies_legacy_alfoplus_without_running_config(self):
        driver = SiaeSmosCliDriver(RecordingTransport(""))
        artifact = make_artifact(
            b"show running-config\r\nC interp: unknown symbol name 'running'.\r\n"
        )

        result = driver.validate(artifact)

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "COMMAND_UNSUPPORTED")
        self.assertIn("Connection and authentication succeeded", result.safe_message)
        self.assertIn("Legacy ALFOplus", result.safe_message)

    def test_private_key_only_authentication_is_rejected_before_connecting(self):
        transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosCliDriver(transport)
        context = DriverContext(
            device_id=1,
            device_name="siae-radio",
            address="192.0.2.10",
            credentials=CredentialMaterial(username="backup", private_key="private-key"),
        )

        with self.assertRaises(DriverError) as raised:
            driver.collect(context)

        self.assertEqual(raised.exception.error_code, "UNSUPPORTED_AUTH")
        self.assertEqual(transport.opens, [])

    def test_ssh_private_key_only_authentication_is_rejected_before_connecting(self):
        transport = RecordingTransport(VALID_CONFIG)
        driver = SiaeSmosSSHDriver(transport)
        context = DriverContext(
            device_id=1,
            device_name="siae-radio",
            address="192.0.2.10",
            credentials=CredentialMaterial(username="backup", private_key="private-key"),
        )

        with self.assertRaises(DriverError) as raised:
            driver.collect(context)

        self.assertEqual(raised.exception.error_code, "UNSUPPORTED_AUTH")
        self.assertEqual(transport.opens, [])

    def test_normalizer_removes_volatile_build_header(self):
        driver = SiaeSmosCliDriver(RecordingTransport(""))
        first = make_artifact(VALID_CONFIG.encode())
        second = make_artifact(
            VALID_CONFIG.replace("#Building configuration...", "# Building configuration 999 bytes")
            .replace("RADIO-UPLINK", "CUSTOMER-UPLINK")
            .encode()
        )

        self.assertNotIn(b"Building configuration", driver.normalize(first))
        self.assertNotEqual(driver.normalize(first), driver.normalize(second))


if __name__ == "__main__":
    unittest.main()
