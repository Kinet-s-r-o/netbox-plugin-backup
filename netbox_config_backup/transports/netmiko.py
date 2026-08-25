from __future__ import annotations

import socket
from contextlib import contextmanager
from errno import EHOSTUNREACH, ENETUNREACH
from io import StringIO
from typing import Any, Protocol

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
from paramiko import ECDSAKey, Ed25519Key, RSAKey
from paramiko.ssh_exception import (
    BadHostKeyException,
    PasswordRequiredException,
    SSHException,
)

from netbox_config_backup.drivers.base import DriverContext, DriverError
from netbox_config_backup.transports.known_hosts import materialized_known_hosts

SSH_DISABLED_ALGORITHMS = {
    # Paramiko 4.0 can otherwise use the legacy RSA/SHA-1 signature algorithm.
    # RSA keys remain usable through rsa-sha2-256/512.
    "keys": ["ssh-rsa"],
    "pubkeys": ["ssh-rsa"],
}

# Some supported legacy appliances offer only an ssh-rsa (RSA/SHA-1) server
# host key. Drivers may opt in to this narrower policy after an independently
# verified known_hosts entry is provisioned. Legacy ssh-rsa *user-key*
# authentication remains disabled.
LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS = {"pubkeys": ["ssh-rsa"]}


class NetmikoConnection(Protocol):
    def send_command(self, command_string: str, **kwargs: Any) -> str: ...

    def enable(self) -> str: ...

    def disconnect(self) -> None: ...


class NetmikoSession:
    """Small, driver-facing wrapper which keeps Netmiko errors stable and safe."""

    def __init__(self, connection: NetmikoConnection, *, command_timeout: int) -> None:
        self._connection = connection
        self._command_timeout = command_timeout

    def send_command(self, command: str, **kwargs: Any) -> str:
        kwargs.setdefault("read_timeout", self._command_timeout)
        try:
            return self._connection.send_command(command, **kwargs)
        except Exception as exc:
            raise _translate_exception(exc, phase="command") from exc

    def enable(self) -> str:
        try:
            return self._connection.enable()
        except Exception as exc:
            raise _translate_exception(exc, phase="command") from exc


class NetmikoTransport:
    """Open read-only SSH sessions for vendor drivers through Netmiko."""

    def __init__(
        self,
        connector=ConnectHandler,
        *,
        disabled_algorithms: dict[str, list[str]] | None = None,
    ) -> None:
        self._connector = connector
        self._disabled_algorithms = (
            SSH_DISABLED_ALGORITHMS if disabled_algorithms is None else disabled_algorithms
        )

    @contextmanager
    def open(self, *, device_type: str, context: DriverContext):
        if not context.address:
            raise DriverError("NO_ADDRESS", "The device has no usable management address.")
        if context.credentials is None:
            raise DriverError("NO_CREDENTIALS", "No credential profile is configured.")
        if not device_type:
            raise DriverError("CONNECTION_FAILED", "Network driver type is not configured.")

        with materialized_known_hosts(context.connection) as known_hosts_path:
            parameters = self._connection_parameters(
                device_type,
                context,
                disabled_algorithms=self._disabled_algorithms,
                known_hosts_path=known_hosts_path,
            )
            try:
                connection = self._connector(**parameters)
            except Exception as exc:
                raise _translate_exception(exc, phase="connect") from exc

            try:
                yield NetmikoSession(
                    connection,
                    command_timeout=context.connection.command_timeout,
                )
            finally:
                try:
                    connection.disconnect()
                except Exception:  # noqa: BLE001, S110 - teardown must not mask result
                    pass

    @staticmethod
    def _connection_parameters(
        device_type: str,
        context: DriverContext,
        *,
        disabled_algorithms: dict[str, list[str]],
        known_hosts_path: str = "",
    ) -> dict[str, Any]:
        credentials = context.credentials
        if credentials is None:  # Kept explicit for type checkers; open() validates this.
            raise DriverError("NO_CREDENTIALS", "No credential profile is configured.")

        connection = context.connection
        parameters: dict[str, Any] = {
            "device_type": device_type,
            "host": context.address,
            "port": connection.port,
            "username": credentials.username,
            "password": credentials.password or "",
            "secret": credentials.enable_secret or "",
            "conn_timeout": connection.connect_timeout,
            "auth_timeout": connection.connect_timeout,
            "banner_timeout": connection.connect_timeout,
            "blocking_timeout": connection.command_timeout,
            # Netmiko otherwise uses shorter method defaults while detecting the
            # initial prompt. Slow RouterOS sessions can time out before the
            # configured command timeout is ever applied to send_command().
            "read_timeout_override": connection.command_timeout,
            "keepalive": connection.keepalive,
            "allow_agent": False,
            "ssh_strict": connection.verify_host_key,
            "system_host_keys": connection.verify_host_key and not known_hosts_path,
            "alt_host_keys": connection.verify_host_key and bool(known_hosts_path),
            "alt_key_file": known_hosts_path,
            "disabled_algorithms": disabled_algorithms,
        }
        if credentials.private_key:
            parameters.update(
                {
                    "password": "",
                    "use_keys": True,
                    "pkey": _parse_private_key(credentials.private_key),
                }
            )
        return parameters


def _parse_private_key(value: str):
    for key_type in (Ed25519Key, ECDSAKey, RSAKey):
        try:
            return key_type.from_private_key(StringIO(value))
        except PasswordRequiredException as exc:
            raise DriverError(
                "INVALID_PRIVATE_KEY",
                "Encrypted SSH private keys are not supported by this credential provider.",
            ) from exc
        except (SSHException, ValueError):
            continue
    raise DriverError("INVALID_PRIVATE_KEY", "The SSH private key could not be loaded.")


def _translate_exception(exc: Exception, *, phase: str) -> DriverError:
    exception_chain = tuple(_walk_exception_chain(exc))
    if driver_error := next(
        (item for item in exception_chain if isinstance(item, DriverError)), None
    ):
        return driver_error
    if any(isinstance(item, NetmikoAuthenticationException) for item in exception_chain):
        return DriverError("AUTH_FAILED", "Device authentication failed.")
    if any(isinstance(item, BadHostKeyException) for item in exception_chain):
        return DriverError(
            "HOST_KEY_MISMATCH",
            "The device SSH host key does not match the trusted known_hosts entry.",
        )
    if _looks_like_unknown_host_key(exc):
        return DriverError(
            "HOST_KEY_UNKNOWN",
            "The device SSH host key is not present in the configured known_hosts file.",
        )
    if _looks_like_host_key_error(exc):
        return DriverError("HOST_KEY_FAILED", "SSH host key verification failed.")
    if any(isinstance(item, socket.gaierror) for item in exception_chain):
        return DriverError(
            "DNS_FAILED",
            "The management hostname could not be resolved by the backup worker.",
        )
    if any(isinstance(item, ConnectionRefusedError) for item in exception_chain):
        return DriverError(
            "CONNECTION_REFUSED",
            (
                "The device rejected the TCP connection. "
                "Check the configured management service and port."
            ),
        )
    if any(
        isinstance(item, OSError) and item.errno in {ENETUNREACH, EHOSTUNREACH}
        for item in exception_chain
    ):
        return DriverError(
            "NETWORK_UNREACHABLE",
            "The device network is unreachable from the backup worker. Check routing or VPN.",
        )
    if any(
        isinstance(item, (NetmikoTimeoutException, TimeoutError, socket.timeout))
        for item in exception_chain
    ):
        if phase == "command":
            return DriverError(
                "TIMEOUT",
                "The device session was established, but the configuration command timed out.",
            )
        return DriverError(
            "TIMEOUT",
            (
                "The TCP connection timed out before a session was established. "
                "Check the management address, route or VPN, firewall, and configured port."
            ),
        )
    return DriverError("CONNECTION_FAILED", "The device connection failed.")


def _looks_like_host_key_error(exc: Exception) -> bool:
    return any(
        isinstance(item, SSHException)
        and ("host key" in str(item).lower() or "not found in known_hosts" in str(item).lower())
        for item in _walk_exception_chain(exc)
    )


def _looks_like_unknown_host_key(exc: Exception) -> bool:
    return any(
        isinstance(item, SSHException)
        and (
            "not found in known_hosts" in str(item).lower()
            or "not found in known hosts" in str(item).lower()
        )
        for item in _walk_exception_chain(exc)
    )


def _walk_exception_chain(exc: Exception):
    seen: set[int] = set()
    current: BaseException | None = exc
    while isinstance(current, Exception) and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
