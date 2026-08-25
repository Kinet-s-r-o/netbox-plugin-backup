import socket
import unittest
from errno import ENETUNREACH
from io import StringIO

from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
from paramiko import RSAKey
from paramiko.ssh_exception import SSHException

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers.base import ConnectionParameters, DriverContext, DriverError
from netbox_config_backup.transports.netmiko import NetmikoTransport


class RecordingConnection:
    def __init__(
        self,
        *,
        output="configuration",
        command_error=None,
        enable_error=None,
        disconnect_error=None,
    ):
        self.output = output
        self.command_error = command_error
        self.enable_error = enable_error
        self.disconnect_error = disconnect_error
        self.commands = []
        self.enable_calls = 0
        self.disconnected = False

    def send_command(self, command_string, **kwargs):
        self.commands.append((command_string, kwargs))
        if self.command_error:
            raise self.command_error
        return self.output

    def enable(self):
        self.enable_calls += 1
        if self.enable_error:
            raise self.enable_error
        return ""

    def disconnect(self):
        self.disconnected = True
        if self.disconnect_error:
            raise self.disconnect_error


class RecordingConnector:
    def __init__(self, connection=None, error=None):
        self.connection = connection or RecordingConnection()
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.connection


def make_context(**overrides):
    values = {
        "device_id": 1,
        "device_name": "router-1",
        "address": "192.0.2.10",
        "credentials": CredentialMaterial(username="backup", password="very-secret"),
        "connection": ConnectionParameters(
            port=2222,
            connect_timeout=12,
            command_timeout=45,
            keepalive=20,
            verify_host_key=True,
            known_hosts_path="/etc/ssh/known_hosts",
        ),
    }
    values.update(overrides)
    return DriverContext(**values)


class NetmikoTransportTests(unittest.TestCase):
    def test_password_session_uses_connection_profile_and_disconnects(self):
        connector = RecordingConnector()
        transport = NetmikoTransport(connector)

        with transport.open(device_type="vendor_os", context=make_context()) as session:
            output = session.send_command("show configuration")

        self.assertEqual(output, "configuration")
        self.assertTrue(connector.connection.disconnected)
        self.assertEqual(
            connector.connection.commands,
            [("show configuration", {"read_timeout": 45})],
        )
        call = connector.calls[0]
        self.assertEqual(call["host"], "192.0.2.10")
        self.assertEqual(call["port"], 2222)
        self.assertEqual(call["username"], "backup")
        self.assertEqual(call["password"], "very-secret")
        self.assertEqual(call["conn_timeout"], 12)
        self.assertEqual(call["auth_timeout"], 12)
        self.assertEqual(call["banner_timeout"], 12)
        self.assertEqual(call["blocking_timeout"], 45)
        self.assertEqual(call["read_timeout_override"], 45)
        self.assertEqual(call["keepalive"], 20)
        self.assertTrue(call["ssh_strict"])
        self.assertTrue(call["alt_host_keys"])
        self.assertEqual(call["alt_key_file"], "/etc/ssh/known_hosts")
        self.assertFalse(call["system_host_keys"])
        self.assertFalse(call["allow_agent"])
        self.assertEqual(
            call["disabled_algorithms"],
            {"keys": ["ssh-rsa"], "pubkeys": ["ssh-rsa"]},
        )

    def test_driver_can_narrowly_override_disabled_algorithms(self):
        connector = RecordingConnector()
        algorithms = {"pubkeys": ["ssh-rsa"]}

        with NetmikoTransport(
            connector,
            disabled_algorithms=algorithms,
        ).open(device_type="legacy_vendor", context=make_context()):
            pass

        self.assertIs(connector.calls[0]["disabled_algorithms"], algorithms)
        self.assertFalse(connector.calls[0]["allow_agent"])
        self.assertTrue(connector.calls[0]["ssh_strict"])

    def test_system_host_keys_are_used_without_an_explicit_file(self):
        connector = RecordingConnector()
        context = make_context(
            connection=ConnectionParameters(known_hosts_path="", verify_host_key=True)
        )

        with NetmikoTransport(connector).open(device_type="vendor_os", context=context):
            pass

        self.assertTrue(connector.calls[0]["system_host_keys"])
        self.assertFalse(connector.calls[0]["alt_host_keys"])

    def test_host_key_checks_can_be_explicitly_disabled(self):
        connector = RecordingConnector()
        context = make_context(
            connection=ConnectionParameters(
                verify_host_key=False,
                known_hosts_path="/should/not/be/loaded",
            )
        )

        with NetmikoTransport(connector).open(device_type="vendor_os", context=context):
            pass

        self.assertFalse(connector.calls[0]["ssh_strict"])
        self.assertFalse(connector.calls[0]["system_host_keys"])
        self.assertFalse(connector.calls[0]["alt_host_keys"])

    def test_private_key_material_is_parsed_in_memory(self):
        private_key = RSAKey.generate(1024)
        buffer = StringIO()
        private_key.write_private_key(buffer)
        connector = RecordingConnector()
        context = make_context(
            credentials=CredentialMaterial(username="backup", private_key=buffer.getvalue())
        )

        with NetmikoTransport(connector).open(device_type="vendor_os", context=context):
            pass

        call = connector.calls[0]
        self.assertTrue(call["use_keys"])
        self.assertIsInstance(call["pkey"], RSAKey)
        self.assertEqual(call["password"], "")
        self.assertNotIn(buffer.getvalue(), repr(call))

    def test_invalid_private_key_is_a_stable_failure(self):
        context = make_context(
            credentials=CredentialMaterial(username="backup", private_key="not-a-key")
        )

        with (
            self.assertRaises(DriverError) as raised,
            NetmikoTransport(RecordingConnector()).open(device_type="vendor_os", context=context),
        ):
            pass

        self.assertEqual(raised.exception.error_code, "INVALID_PRIVATE_KEY")
        self.assertNotIn("not-a-key", str(raised.exception))

    def test_missing_address_or_credentials_fails_before_connecting(self):
        cases = (
            (make_context(address=None), "NO_ADDRESS"),
            (make_context(credentials=None), "NO_CREDENTIALS"),
        )
        for context, error_code in cases:
            connector = RecordingConnector()
            with self.subTest(error_code=error_code):
                with (
                    self.assertRaises(DriverError) as raised,
                    NetmikoTransport(connector).open(device_type="vendor_os", context=context),
                ):
                    pass
                self.assertEqual(raised.exception.error_code, error_code)
                self.assertEqual(connector.calls, [])

    def test_connect_errors_are_translated_without_exposing_details(self):
        cases = (
            (
                NetmikoAuthenticationException("password very-secret"),
                "AUTH_FAILED",
                "authentication",
            ),
            (NetmikoTimeoutException("192.0.2.10 timed out"), "TIMEOUT", "before"),
            (TimeoutError("socket detail"), "TIMEOUT", "before"),
            (
                ConnectionRefusedError("refused"),
                "CONNECTION_REFUSED",
                "management service",
            ),
            (OSError(ENETUNREACH, "unreachable"), "NETWORK_UNREACHABLE", "routing"),
            (socket.gaierror("dns detail"), "DNS_FAILED", "resolved"),
            (
                SSHException("Server not found in known_hosts"),
                "HOST_KEY_UNKNOWN",
                "not present",
            ),
            (SSHException("host key verification detail"), "HOST_KEY_FAILED", "verification"),
            (SSHException("protocol detail"), "CONNECTION_FAILED", "connection failed"),
        )
        for error, error_code, safe_detail in cases:
            with self.subTest(error_code=error_code):
                with (
                    self.assertRaises(DriverError) as raised,
                    NetmikoTransport(RecordingConnector(error=error)).open(
                        device_type="vendor_os", context=make_context()
                    ),
                ):
                    pass
                self.assertEqual(raised.exception.error_code, error_code)
                self.assertIn(safe_detail, raised.exception.safe_message)
                self.assertNotIn(str(error), str(raised.exception))

    def test_command_error_is_translated_and_connection_is_closed(self):
        connection = RecordingConnection(
            command_error=NetmikoTimeoutException("command and secret detail")
        )
        connector = RecordingConnector(connection=connection)

        with (
            self.assertRaises(DriverError) as raised,
            NetmikoTransport(connector).open(
                device_type="vendor_os", context=make_context()
            ) as session,
        ):
            session.send_command("show configuration")

        self.assertEqual(raised.exception.error_code, "TIMEOUT")
        self.assertIn("configuration command", raised.exception.safe_message)
        self.assertTrue(connection.disconnected)
        self.assertNotIn("secret detail", str(raised.exception))

    def test_enable_error_is_translated_and_connection_is_closed(self):
        connection = RecordingConnection(
            enable_error=NetmikoAuthenticationException("enable secret detail")
        )
        connector = RecordingConnector(connection=connection)

        with (
            self.assertRaises(DriverError) as raised,
            NetmikoTransport(connector).open(
                device_type="vendor_os",
                context=make_context(),
            ) as session,
        ):
            session.enable()

        self.assertEqual(raised.exception.error_code, "AUTH_FAILED")
        self.assertTrue(connection.disconnected)
        self.assertNotIn("secret detail", str(raised.exception))

    def test_wrapped_unknown_host_key_is_not_misclassified_as_timeout(self):
        cause = SSHException("Server 192.0.2.10 not found in known_hosts")
        error = NetmikoTimeoutException("wrapped connection detail")
        error.__cause__ = cause

        with (
            self.assertRaises(DriverError) as raised,
            NetmikoTransport(RecordingConnector(error=error)).open(
                device_type="vendor_os", context=make_context()
            ),
        ):
            pass

        self.assertEqual(raised.exception.error_code, "HOST_KEY_UNKNOWN")
        self.assertNotIn("192.0.2.10", raised.exception.safe_message)

    def test_disconnect_failure_does_not_hide_successful_command(self):
        connection = RecordingConnection(disconnect_error=RuntimeError("teardown failed"))

        with NetmikoTransport(RecordingConnector(connection=connection)).open(
            device_type="vendor_os", context=make_context()
        ) as session:
            self.assertEqual(session.send_command("show configuration"), "configuration")

        self.assertTrue(connection.disconnected)

    def test_unexpected_driver_body_error_is_not_misclassified(self):
        connection = RecordingConnection()

        with (
            self.assertRaisesRegex(ValueError, "driver bug"),
            NetmikoTransport(RecordingConnector(connection=connection)).open(
                device_type="vendor_os", context=make_context()
            ),
        ):
            raise ValueError("driver bug")

        self.assertTrue(connection.disconnected)


if __name__ == "__main__":
    unittest.main()
