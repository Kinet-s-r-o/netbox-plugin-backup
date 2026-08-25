import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers.base import ConnectionParameters, DriverContext, DriverError
from netbox_config_backup.transports.http_json import HttpJsonTransport
from netbox_config_backup.transports.netmiko import (
    LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
)
from netbox_config_backup.transports.ssh_artifact import (
    RacomRaySshArtifactTransport,
    SshArtifactTransport,
)


class FakeResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.content[:limit]


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class FakeChannel:
    def __init__(self, status=0):
        self.status = status
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def recv_exit_status(self):
        return self.status


class FakeStream:
    def __init__(self, content=b"", status=0):
        self.content = content
        self.channel = FakeChannel(status)

    def read(self, _limit):
        return self.content


class FakeSftp:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stat(self, _path):
        return SimpleNamespace(st_size=len(self.content))

    def getfo(self, _path, file_object):
        file_object.write(self.content)


class FakeSshClient:
    def __init__(self, content=b"native-backup", status=0):
        self.content = content
        self.status = status
        self.connect_kwargs = None
        self.commands = []
        self.closed = False
        self.policy = None

    def load_host_keys(self, path):
        self.host_keys_path = path

    def load_system_host_keys(self):
        self.system_host_keys = True

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs

    def exec_command(self, command, timeout):
        self.commands.append((command, timeout))
        return None, FakeStream(b"done", self.status), FakeStream()

    def open_sftp(self):
        return FakeSftp(self.content)

    def get_transport(self):
        return "fake-paramiko-transport"

    def close(self):
        self.closed = True


class FakeScpClient:
    def __init__(self, content):
        self.content = content
        self.transport = None
        self.socket_timeout = None
        self.progress4 = None
        self.calls = []

    def factory(self, transport, *, socket_timeout, progress4):
        self.transport = transport
        self.socket_timeout = socket_timeout
        self.progress4 = progress4
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, remote_path, local_path):
        self.calls.append((remote_path, local_path))
        self.progress4(remote_path, len(self.content), 0, ("192.0.2.10", 2222))
        Path(local_path).write_bytes(self.content)
        self.progress4(
            remote_path,
            len(self.content),
            len(self.content),
            ("192.0.2.10", 2222),
        )


class FakeAsyncConnection:
    async def run(self, command, **kwargs):
        self.command = (command, kwargs)
        return SimpleNamespace(stdout="done", stderr="", exit_status=0)


class FakeAsyncConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class FakeAsyncConnector:
    def __init__(self):
        self.connection = FakeAsyncConnection()
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return FakeAsyncConnectionContext(self.connection)


def driver_context(**overrides):
    values = {
        "device_id": 1,
        "device_name": "radio-1",
        "address": "192.0.2.10",
        "credentials": CredentialMaterial(username="backup", password="secret"),
        "connection": ConnectionParameters(
            port=2222,
            connect_timeout=12,
            command_timeout=45,
            verify_host_key=True,
            known_hosts_path="/etc/ssh/known_hosts",
        ),
    }
    values.update(overrides)
    return DriverContext(**values)


class HttpJsonTransportTests(unittest.TestCase):
    def test_posts_json_with_headers_timeout_and_bound(self):
        opener = FakeOpener(FakeResponse(b'{"result":{"ok":true}}'))
        with patch(
            "netbox_config_backup.transports.http_json.build_opener",
            return_value=opener,
        ):
            result = HttpJsonTransport().post_json(
                "https://192.0.2.1/rpc.cgi",
                {"method": "settings_get"},
                headers={"apikey": "session-token"},
                timeout=17,
                verify_tls=False,
                max_response_bytes=1024,
            )

        self.assertEqual(result, {"result": {"ok": True}})
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 17)
        self.assertEqual(json.loads(request.data), {"method": "settings_get"})
        self.assertEqual(request.get_header("Apikey"), "session-token")

    def test_rejects_oversized_and_authentication_responses_safely(self):
        opener = FakeOpener(FakeResponse(b"12345", {"Content-Length": "5"}))
        with (
            patch(
                "netbox_config_backup.transports.http_json.build_opener",
                return_value=opener,
            ),
            self.assertRaises(DriverError) as raised,
        ):
            HttpJsonTransport().post_json(
                "https://192.0.2.1/rpc.cgi",
                {},
                verify_tls=False,
                max_response_bytes=4,
            )
        self.assertEqual(raised.exception.error_code, "CONFIG_TOO_LARGE")

        error = HTTPError("https://device/", 401, "secret detail", {}, None)
        opener = FakeOpener(error=error)
        with (
            patch(
                "netbox_config_backup.transports.http_json.build_opener",
                return_value=opener,
            ),
            self.assertRaises(DriverError) as raised,
        ):
            HttpJsonTransport().post_json("https://device/", {}, verify_tls=False)
        self.assertEqual(raised.exception.error_code, "AUTH_FAILED")
        self.assertNotIn("secret detail", raised.exception.safe_message)


class SshArtifactTransportTests(unittest.TestCase):
    def test_executes_command_and_downloads_bounded_file(self):
        client = FakeSshClient()
        result = SshArtifactTransport(lambda: client).collect(
            driver_context(),
            remote_path="exports/configuration.bku",
            export_command="vendor backup command",
            max_bytes=1024,
        )

        self.assertEqual(result.content, b"native-backup")
        self.assertEqual(client.commands, [("vendor backup command", 45)])
        self.assertEqual(client.connect_kwargs["hostname"], "192.0.2.10")
        self.assertEqual(client.connect_kwargs["port"], 2222)
        self.assertEqual(client.connect_kwargs["password"], "secret")
        self.assertIn("ssh-rsa", client.connect_kwargs["disabled_algorithms"]["keys"])
        self.assertEqual(client.host_keys_path, "/etc/ssh/known_hosts")
        self.assertTrue(client.closed)

    def test_rejects_unsafe_path_before_opening_client(self):
        calls = []

        def factory():
            calls.append(True)
            return FakeSshClient()

        with self.assertRaises(DriverError) as raised:
            SshArtifactTransport(factory).collect(
                driver_context(),
                remote_path="../configuration.bku",
            )
        self.assertEqual(raised.exception.error_code, "INVALID_DRIVER_OPTIONS")
        self.assertEqual(calls, [])

    def test_accepts_scoped_legacy_rsa_host_key_override(self):
        client = FakeSshClient()

        SshArtifactTransport(
            lambda: client,
            disabled_algorithms=LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
        ).collect(driver_context(), remote_path="configuration.tgz")

        algorithms = client.connect_kwargs["disabled_algorithms"]
        self.assertNotIn("keys", algorithms)
        self.assertEqual(algorithms["pubkeys"], ["ssh-rsa"])
        self.assertEqual(client.host_keys_path, "/etc/ssh/known_hosts")
        self.assertFalse(client.connect_kwargs["allow_agent"])
        self.assertFalse(client.connect_kwargs["look_for_keys"])

    def test_downloads_bounded_artifact_over_scp_without_sftp(self):
        client = FakeSshClient()
        scp = FakeScpClient(b"racom-native-backup")

        result = SshArtifactTransport(
            lambda: client,
            transfer_mode="scp",
            scp_factory=scp.factory,
        ).collect(
            driver_context(),
            remote_path="cnf_backup.tgz",
            export_command="backup command",
            max_bytes=1024,
        )

        self.assertEqual(result.content, b"racom-native-backup")
        self.assertEqual(scp.transport, "fake-paramiko-transport")
        self.assertEqual(scp.socket_timeout, 45)
        self.assertEqual(scp.calls[0][0], "cnf_backup.tgz")
        self.assertTrue(client.closed)

    def test_rejects_oversized_scp_artifact_before_writing_it(self):
        client = FakeSshClient()
        scp = FakeScpClient(b"too-large")

        with self.assertRaises(DriverError) as raised:
            SshArtifactTransport(
                lambda: client,
                transfer_mode="scp",
                scp_factory=scp.factory,
            ).collect(
                driver_context(),
                remote_path="cnf_backup.tgz",
                max_bytes=4,
            )

        self.assertEqual(raised.exception.error_code, "CONFIG_TOO_LARGE")
        self.assertTrue(client.closed)


class RacomRaySshArtifactTransportTests(unittest.TestCase):
    def test_adds_dsa_compatibility_with_strict_known_hosts_and_downloads_artifact(self):
        connector = FakeAsyncConnector()

        async def scp(_source, destination, **kwargs):
            kwargs["progress_handler"](b"source", b"destination", 0, 13)
            Path(destination).write_bytes(b"legacy-backup")

        context = driver_context(
            connection=ConnectionParameters(
                port=22,
                connect_timeout=12,
                command_timeout=45,
                keepalive=30,
                verify_host_key=True,
                known_hosts_path="/etc/ssh/known_hosts",
            )
        )
        result = RacomRaySshArtifactTransport(connector, scp).collect(
            context,
            remote_path="cnf_backup.tgz",
            export_command="cli_cnf_backup_get",
            max_bytes=1024,
        )

        self.assertEqual(result.content, b"legacy-backup")
        self.assertEqual(connector.kwargs["server_host_key_algs"], "+ssh-dss")
        self.assertEqual(connector.kwargs["known_hosts"], "/etc/ssh/known_hosts")
        self.assertEqual(connector.kwargs["client_keys"], [])
        self.assertEqual(connector.kwargs["password"], "secret")

    def test_respects_generic_disabled_host_key_verification(self):
        connector = FakeAsyncConnector()

        async def scp(_source, destination, **_kwargs):
            Path(destination).write_bytes(b"legacy-backup")

        context = driver_context(
            connection=ConnectionParameters(
                verify_host_key=False,
                known_hosts_path="",
            )
        )
        RacomRaySshArtifactTransport(connector, scp).collect(
            context,
            remote_path="cnf_backup.tgz",
        )

        self.assertIsNone(connector.kwargs["known_hosts"])


if __name__ == "__main__":
    unittest.main()
