import io
import json
import tempfile
import unittest
from pathlib import Path

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers.base import (
    ConnectionParameters,
    DriverContext,
    DriverError,
    ReceiverParameters,
)
from netbox_config_backup.transports.siae_alfoplus import SiaeAlfoplusWebLctTransport


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeWebLctOpener:
    def __init__(self, inbox: Path, *, profile=1):
        self.inbox = inbox
        self.profile = profile
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, request.data, timeout))
        path = request.full_url.split(":80", 1)[-1]
        if path == "/Snmp.Login":
            return self._response({"Status": "0"})
        if path == "/Snmp.LogInfo":
            return self._response(
                {
                    "Status": "2",
                    "UserProfile": str(self.profile),
                    "UserName": "SYSTEM",
                    "UserIp": "192.0.2.10",
                }
            )
        if path == "/Snmp.Logout":
            return self._response({"Status": "0"})
        if path == "/Snmp.Get":
            oid = json.loads(request.data)[0]
            values = {
                "1.4.1.3373.206.7.0": "1",
                ".5.2.1.8.192.0.2.10.83.89.83.84.69.77": "1",
                ".30.3.0": "1",
                ".30.4.0": "0",
            }
            return self._response({"Err": "0", "Indx": "0", oid: values[oid]})
        if path == "/Snmp.Set":
            body = request.data.decode()
            if '".30.1.0"' in body:
                filename = body.split("incoming\\", 1)[1].split('"', 1)[0]
                self.inbox.mkdir(parents=True, exist_ok=True)
                (self.inbox / filename).write_bytes(b"native alfoplus configuration")
            return self._response({"Err": "0", "Indx": "0"})
        raise AssertionError(f"Unexpected request: {request.full_url}")

    @staticmethod
    def _response(payload):
        return FakeResponse(json.dumps(payload).encode())


class SiaeAlfoplusWebLctTransportTests(unittest.TestCase):
    def make_context(self, root: str, *, profile_protocol="ftp"):
        return DriverContext(
            device_id=10,
            device_name="legacy-alfoplus",
            address="192.0.2.80",
            credentials=CredentialMaterial(username="system", password="siaeMICR"),
            connection=ConnectionParameters(connect_timeout=3),
            receiver=ReceiverParameters(
                profile_id=2,
                protocol=profile_protocol,
                mode="direct",
                advertised_host="192.0.2.10",
                advertised_port=21,
                bridge_host="receiver",
                bridge_port=21,
                remote_bind_host="127.0.0.1",
                remote_bind_port=2222,
                upload_directory="incoming",
                inbox_path=str(Path(root) / "incoming"),
                export_timeout=10,
                credentials=CredentialMaterial(username="NCBFTP", password="BACKUP1"),
            ),
            options={
                "allow_device_export": True,
                "allow_legacy_ftp_setup": True,
                "sync_receiver_credentials": True,
            },
        )

    def test_collects_native_backup_and_never_issues_restore(self):
        with tempfile.TemporaryDirectory() as root:
            inbox = Path(root) / "incoming"
            opener = FakeWebLctOpener(inbox)
            transport = SiaeAlfoplusWebLctTransport(opener=opener, sleep=lambda _value: None)

            content = transport.collect(
                self.make_context(root), options=self.make_context(root).options
            )

        self.assertEqual(content, b"native alfoplus configuration")
        set_bodies = [
            data.decode() for url, data, _timeout in opener.requests if "/Snmp.Set" in url
        ]
        self.assertTrue(any('".30.2.0"' in body and ',v:["1"],t:' in body for body in set_bodies))
        self.assertFalse(any('".30.2.0"' in body and ',v:["2"],t:' in body for body in set_bodies))
        self.assertFalse(any('".30.2.0"' in body and ',v:["3"],t:' in body for body in set_bodies))
        self.assertFalse(inbox.exists() and any(inbox.iterdir()))

    def test_rejects_non_privileged_weblct_user(self):
        with tempfile.TemporaryDirectory() as root:
            opener = FakeWebLctOpener(Path(root) / "incoming", profile=3)
            transport = SiaeAlfoplusWebLctTransport(opener=opener)
            with self.assertRaises(DriverError) as raised:
                transport.collect(self.make_context(root), options=self.make_context(root).options)
        self.assertEqual(raised.exception.error_code, "INSUFFICIENT_PRIVILEGES")

    def test_requires_explicit_legacy_ftp_receiver(self):
        with tempfile.TemporaryDirectory() as root:
            context = self.make_context(root, profile_protocol="sftp")
            transport = SiaeAlfoplusWebLctTransport(opener=FakeWebLctOpener(Path(root)))
            with self.assertRaises(DriverError) as raised:
                transport.collect(context, options=context.options)
        self.assertEqual(raised.exception.error_code, "INVALID_RECEIVER_PROFILE")

    def test_accepts_weblct_windows_path_echo(self):
        result = SiaeAlfoplusWebLctTransport._decode_set_response(
            rb'{"Err":"0","Indx":"0",".30.1.0":"C:\incoming\backup.bak"}'
        )
        self.assertEqual(result["Err"], "0")


if __name__ == "__main__":
    unittest.main()
