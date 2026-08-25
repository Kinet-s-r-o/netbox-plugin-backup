import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers.base import (
    ConnectionParameters,
    DriverContext,
    DriverError,
    ReceiverParameters,
)
from netbox_config_backup.transports.ceragon_ceraos import CeragonCeraOSTransport


def native_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("config/readable.txt", "system-name microwave-1\n")
    return output.getvalue()


class FakeChannel:
    def __init__(self, inbox: Path) -> None:
        self.inbox = inbox
        self.responses = [b"root> "]
        self.commands = []
        self.filename = ""

    def recv_ready(self):
        return bool(self.responses)

    def recv(self, _size):
        return self.responses.pop(0)

    def sendall(self, value):
        command = value.decode().strip()
        self.commands.append(command)
        if "channel server set" in command:
            self.filename = re.search(r"filename (\S+)", command).group(1)
        if "configuration-file export" in command:
            (self.inbox / self.filename).write_bytes(native_zip())
        response = "sftp 22\nroot> " if command.endswith("port-show") else "root> "
        self.responses.append(response.encode())


class FakeClient:
    def __init__(self, inbox: Path) -> None:
        self.channel = FakeChannel(inbox)
        self.connect_parameters = None
        self.closed = False

    def set_missing_host_key_policy(self, _policy):
        pass

    def connect(self, **parameters):
        self.connect_parameters = parameters

    def invoke_shell(self, **_kwargs):
        return self.channel

    def close(self):
        self.closed = True


class CeragonCeraOSTransportTests(unittest.TestCase):
    def context(self, inbox: Path, *, options=None):
        return DriverContext(
            device_id=154,
            device_name="ceragon-1",
            address="192.0.2.10",
            credentials=CredentialMaterial(username="device", password="device-pass"),
            connection=ConnectionParameters(verify_host_key=False, command_timeout=2),
            receiver=ReceiverParameters(
                profile_id=1,
                mode="direct",
                advertised_host="192.0.2.20",
                advertised_port=2022,
                bridge_host="receiver",
                bridge_port=2022,
                remote_bind_host="127.0.0.1",
                remote_bind_port=2222,
                upload_directory="incoming",
                inbox_path=str(inbox),
                export_timeout=2,
                credentials=CredentialMaterial(
                    username="ceragon.backup", password="Safe-Value_123"
                ),
            ),
            options=options or {},
        )

    def test_direct_export_uses_safe_commands_and_restores_port(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "incoming"
            inbox.mkdir()
            client = FakeClient(inbox)
            transport = CeragonCeraOSTransport(client_factory=lambda: client)
            content = transport.collect(
                self.context(inbox),
                options={
                    "allow_device_export": True,
                    "restore_point": "restore-point-1",
                    "backup_settle_seconds": 0,
                },
            )
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(content)))
            commands = "\n".join(client.channel.commands)
            self.assertIn("configuration-file add restore-point-1", commands)
            self.assertIn("configuration-file export restore-point-1", commands)
            self.assertIn("protocol sftp port-number 2022", commands)
            self.assertIn("directory /incoming", commands)
            self.assertTrue(commands.endswith("protocol sftp port-number 22"))
            self.assertNotIn(" configuration-file restore ", commands)
            self.assertNotIn(" configuration-file import ", commands)
            self.assertIn("ssh-rsa", client.connect_parameters["disabled_algorithms"]["keys"])
            self.assertEqual(list(inbox.iterdir()), [])
            self.assertTrue(client.closed)

    def test_export_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            with self.assertRaises(DriverError) as raised:
                CeragonCeraOSTransport().collect(self.context(inbox), options={})
            self.assertEqual(raised.exception.error_code, "EXPORT_NOT_CONFIRMED")


if __name__ == "__main__":
    unittest.main()
