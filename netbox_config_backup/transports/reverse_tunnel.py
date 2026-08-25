from __future__ import annotations

import logging
import select
import socket
import threading
from contextlib import contextmanager

from netbox_config_backup.drivers.base import DriverError

logger = logging.getLogger("netbox_config_backup.reverse_tunnel")


class ReverseSshTunnel:
    """Bridge a device-loopback SSH remote forward to the receiver service."""

    def __init__(
        self,
        transport,
        *,
        remote_host: str,
        remote_port: int,
        bridge_host: str,
        bridge_port: int,
        connect_timeout: int,
    ) -> None:
        self.transport = transport
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self.connect_timeout = connect_timeout
        self._stop = threading.Event()
        self._endpoints: list[object] = []
        self._lock = threading.Lock()
        self.connection_count = 0
        self.bridge_error: OSError | None = None
        self.bytes_from_device = 0
        self.bytes_from_receiver = 0
        self.device_banner = b""
        self.receiver_banner = b""

    def open(self) -> None:
        try:
            self.transport.request_port_forward(
                self.remote_host, self.remote_port, handler=self._accept
            )
        except Exception as exc:
            raise DriverError(
                "REVERSE_TUNNEL_FAILED",
                "The device rejected the temporary loopback reverse SSH tunnel.",
            ) from exc

    def close(self) -> None:
        self._stop.set()
        try:
            self.transport.cancel_port_forward(self.remote_host, self.remote_port)
        except Exception:  # noqa: BLE001, S110 - cleanup must not mask the result
            pass
        with self._lock:
            endpoints = list(self._endpoints)
        for endpoint in endpoints:
            try:
                endpoint.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def _accept(self, channel, _origin, _server) -> None:
        # Paramiko invokes this callback on its packet-processing thread.
        with self._lock:
            self.connection_count += 1
        logger.info("Device opened a reverse SFTP tunnel channel.")
        threading.Thread(
            target=self._bridge,
            args=(channel,),
            name="config-backup-reverse-sftp",
            daemon=True,
        ).start()

    def _bridge(self, channel) -> None:
        peer = None
        try:
            peer = socket.create_connection(
                (self.bridge_host, self.bridge_port), timeout=self.connect_timeout
            )
            peer.settimeout(None)
            with self._lock:
                self._endpoints.extend((channel, peer))
            while not self._stop.is_set():
                readable, _, _ = select.select((channel, peer), (), (), 1.0)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    self.bytes_from_device += len(data)
                    if not self.device_banner:
                        self.device_banner = data.split(b"\n", 1)[0][:200]
                    peer.sendall(data)
                if peer in readable:
                    data = peer.recv(65536)
                    if not data:
                        break
                    self.bytes_from_receiver += len(data)
                    if not self.receiver_banner:
                        self.receiver_banner = data.split(b"\n", 1)[0][:200]
                    channel.sendall(data)
        except OSError as exc:
            self.bridge_error = exc
            logger.warning("Reverse SFTP bridge could not reach the receiver service.")
        except EOFError:
            logger.info("Reverse SFTP tunnel channel closed.")
        finally:
            for endpoint in (channel, peer):
                if endpoint is not None:
                    try:
                        endpoint.close()
                    except Exception:  # noqa: BLE001, S110
                        pass
            with self._lock:
                self._endpoints = [item for item in self._endpoints if item not in (channel, peer)]


@contextmanager
def reverse_ssh_tunnel(transport, **kwargs):
    tunnel = ReverseSshTunnel(transport, **kwargs)
    tunnel.open()
    try:
        yield tunnel
    finally:
        tunnel.close()
