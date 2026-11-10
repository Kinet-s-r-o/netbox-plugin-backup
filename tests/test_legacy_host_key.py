import asyncio
import threading
import unittest
from unittest.mock import patch

import asyncssh

import netbox_config_backup.drivers  # noqa: F401  (import order: drivers before transports)
from netbox_config_backup.drivers.base import DriverError
from netbox_config_backup.transports import legacy_host_key
from netbox_config_backup.transports.legacy_host_key import scan_legacy_host_key


class LegacyApplianceServer:
    """In-process SSH server that offers only what an OpenSSH 5.1 appliance does."""

    def __init__(self) -> None:
        self.key = asyncssh.generate_private_key("ssh-rsa", key_size=2048)
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._server = None
        self._startup_error = None

    def __enter__(self):
        self._thread.start()
        self._ready.wait(10)
        if self._startup_error is not None:
            raise self._startup_error
        return self

    def __exit__(self, *_exc):
        async def shutdown():
            self._server.close()
            await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)
        self._loop.close()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def start():
            self._server = await asyncssh.create_server(
                asyncssh.SSHServer,
                "127.0.0.1",
                0,
                server_host_keys=[self.key],
                signature_algs=["ssh-rsa"],
                encryption_algs=["3des-cbc"],
                mac_algs=["hmac-sha1"],
            )
            self.port = self._server.sockets[0].getsockname()[1]

        try:
            self._loop.run_until_complete(start())
        except Exception as exc:  # noqa: BLE001 - surfaced by __enter__
            self._startup_error = exc
        self._ready.set()
        if self._startup_error is None:
            self._loop.run_forever()


class LegacyHostKeyScanTests(unittest.TestCase):
    def test_reads_ssh_rsa_key_from_a_3des_hmac_sha1_only_appliance(self):
        with LegacyApplianceServer() as server:
            key_type, public_key = scan_legacy_host_key("127.0.0.1", server.port, 15)

        expected = server.key.export_public_key("openssh").decode().split()
        self.assertEqual(key_type, "ssh-rsa")
        self.assertEqual(public_key, expected[1])

    def test_default_asyncssh_policy_cannot_read_that_appliance(self):
        # Guards the reason for the explicit legacy options: the stock defaults do not
        # offer 3des-cbc, so the identity scan would silently find nothing.
        with LegacyApplianceServer() as server:

            async def default_scan():
                return await asyncssh.get_server_host_key(
                    "127.0.0.1", server.port, config=None, server_host_key_algs="+ssh-rsa"
                )

            with self.assertRaises(asyncssh.KeyExchangeFailed):
                asyncio.run(default_scan())

    def test_negotiation_failure_has_a_distinct_error_code(self):
        with (
            patch.object(
                legacy_host_key.asyncssh,
                "get_server_host_key",
                side_effect=asyncssh.KeyExchangeFailed("No matching encryption algorithm found"),
            ),
            self.assertRaises(DriverError) as raised,
        ):
            scan_legacy_host_key("192.0.2.10", 22, 5)

        self.assertEqual(raised.exception.error_code, "SSH_NEGOTIATION_FAILED")
        self.assertNotIn("encryption algorithm", raised.exception.safe_message)

    def test_unreachable_device_is_reported_without_details(self):
        with (
            patch.object(
                legacy_host_key.asyncssh,
                "get_server_host_key",
                side_effect=ConnectionRefusedError("refused by 192.0.2.10"),
            ),
            self.assertRaises(DriverError) as raised,
        ):
            scan_legacy_host_key("192.0.2.10", 22, 5)

        self.assertEqual(raised.exception.error_code, "HOST_KEY_SCAN_FAILED")
        self.assertNotIn("192.0.2.10", raised.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
