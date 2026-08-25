from __future__ import annotations

import re
import time
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from paramiko import AutoAddPolicy, RejectPolicy, SSHClient
from paramiko.ssh_exception import AuthenticationException, SSHException

from netbox_config_backup.drivers.base import DriverContext, DriverError
from netbox_config_backup.receiver.server import SAFE_RECEIVER_PASSWORD, SAFE_RECEIVER_USERNAME
from netbox_config_backup.transports.known_hosts import materialized_known_hosts
from netbox_config_backup.transports.netmiko import (
    SSH_DISABLED_ALGORITHMS,
    _parse_private_key,
    _translate_exception,
)
from netbox_config_backup.transports.reverse_tunnel import reverse_ssh_tunnel

_PROMPT = re.compile(r"(?:^|[\r\n])[^\r\n]{0,200}[>#]\s*$")
_CONFIRM = re.compile(r"(?i)(?:are you sure|continue).*?(?:yes/no|y/n).*?[:?]?\s*$")
_PORT_ROW = re.compile(r"(?im)^\s*sftp\s+(\d{1,5})\s*$")
_COMMAND_ERROR = re.compile(
    r"(?i)(unknown command|syntax error|invalid input|operation failed|error:)"
)


class CeraOSSession:
    def __init__(self, channel, *, timeout: int) -> None:
        self.channel = channel
        self.timeout = timeout
        self._read_until_prompt(allow_confirmation=False)

    def command(self, command: str, *, confirm: bool = False) -> str:
        if not command or any(value in command for value in ("\r", "\n", "\x00")):
            raise DriverError("INVALID_DRIVER_OPTIONS", "A CeraOS command is invalid.")
        self.channel.sendall((command + "\n").encode())
        output = self._read_until_prompt(allow_confirmation=confirm)
        if _COMMAND_ERROR.search(output):
            raise DriverError("COMMAND_REJECTED", "CeraOS rejected a backup command.")
        return output

    def _read_until_prompt(self, *, allow_confirmation: bool) -> str:
        deadline = time.monotonic() + self.timeout
        chunks: list[bytes] = []
        confirmed = False
        while time.monotonic() < deadline:
            if self.channel.recv_ready():
                chunk = self.channel.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                text = b"".join(chunks).decode(errors="replace")
                if _CONFIRM.search(text):
                    if not allow_confirmation or confirmed:
                        raise DriverError(
                            "COMMAND_CONFIRMATION_REQUIRED",
                            "CeraOS requested an unexpected command confirmation.",
                        )
                    self.channel.sendall(b"yes\n")
                    confirmed = True
                    chunks.clear()
                    continue
                if _PROMPT.search(text):
                    return text
            else:
                time.sleep(0.05)
        raise DriverError("TIMEOUT", "The CeraOS command did not return a prompt in time.")


class CeragonCeraOSTransport:
    """Create a CeraOS restore point and receive its native ZIP over SFTP."""

    def __init__(self, client_factory=SSHClient, *, clock=time.monotonic) -> None:
        self._client_factory = client_factory
        self._clock = clock

    def collect(self, context: DriverContext, *, options: dict) -> bytes:
        self._validate(context, options)
        receiver = context.receiver
        if receiver is None or receiver.credentials is None:
            raise DriverError("NO_RECEIVER_PROFILE", "Configure an enabled SFTP receiver profile.")

        restore_point = options.get("restore_point", "restore-point-1")
        restore_port = options.get("restore_sftp_port", True)
        filename = f"ncb-{context.device_id}-{uuid4().hex[:16]}.zip"
        inbox = Path(receiver.inbox_path)
        artifact_path = inbox / filename
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        if inbox.is_symlink() or artifact_path.is_symlink():
            raise DriverError("RECEIVER_PATH_UNSAFE", "The receiver inbox path is unsafe.")
        artifact_path.unlink(missing_ok=True)

        client = self._client_factory()
        original_port = None
        session = None
        server_port = (
            receiver.remote_bind_port
            if receiver.mode == "reverse_tunnel"
            else receiver.advertised_port
        )
        try:
            self._connect(client, context)
            session = CeraOSSession(
                client.invoke_shell(width=160, height=48),
                timeout=context.connection.command_timeout,
            )
            match = _PORT_ROW.search(session.command("platform management file-transfer port-show"))
            if match:
                original_port = int(match.group(1))

            if receiver.mode == "reverse_tunnel":
                server_host = receiver.remote_bind_host
                tunnel_context = reverse_ssh_tunnel(
                    client.get_transport(),
                    remote_host=receiver.remote_bind_host,
                    remote_port=receiver.remote_bind_port,
                    bridge_host=receiver.bridge_host,
                    bridge_port=receiver.bridge_port,
                    connect_timeout=context.connection.connect_timeout,
                )
            else:
                server_host = receiver.advertised_host
                tunnel_context = nullcontext()

            with tunnel_context as tunnel:
                if original_port != server_port:
                    session.command(
                        "platform management file-transfer port-config "
                        f"protocol sftp port-number {server_port}"
                    )
                session.command("platform configuration channel set protocol sftp")
                credentials = receiver.credentials
                directory = f"/{receiver.upload_directory}"
                session.command(
                    "platform configuration channel server set "
                    f"ip-address {server_host} directory {directory} filename {filename} "
                    f"username {credentials.username} password {credentials.password}"
                )
                session.command(
                    f"platform configuration configuration-file add {restore_point}",
                    confirm=True,
                )
                # Restore-point creation is asynchronous on CeraOS, but the CLI
                # does not expose a portable completion command across releases.
                time.sleep(options.get("backup_settle_seconds", 15))
                session.command(
                    f"platform configuration configuration-file export {restore_point}",
                    confirm=True,
                )
                return self._wait_and_read(
                    artifact_path,
                    timeout=receiver.export_timeout,
                    max_bytes=receiver.max_upload_bytes,
                    tunnel=tunnel,
                )
        except DriverError:
            raise
        except AuthenticationException as exc:
            raise DriverError("AUTH_FAILED", "Device authentication failed.") from exc
        except (SSHException, OSError, TimeoutError) as exc:
            raise _translate_exception(exc, phase="connect") from exc
        finally:
            if (
                restore_port
                and session is not None
                and original_port is not None
                and original_port != server_port
            ):
                try:
                    session.command(
                        "platform management file-transfer port-config "
                        f"protocol sftp port-number {original_port}"
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
            client.close()
            artifact_path.unlink(missing_ok=True)

    def _connect(self, client, context: DriverContext) -> None:
        credentials = context.credentials
        with materialized_known_hosts(context.connection) as known_hosts_path:
            if context.connection.verify_host_key:
                if known_hosts_path:
                    client.load_host_keys(known_hosts_path)
                else:
                    client.load_system_host_keys()
                client.set_missing_host_key_policy(RejectPolicy())
            else:
                client.set_missing_host_key_policy(AutoAddPolicy())
            parameters = {
                "hostname": context.address,
                "port": context.connection.port,
                "username": credentials.username,
                "timeout": context.connection.connect_timeout,
                "auth_timeout": context.connection.connect_timeout,
                "banner_timeout": context.connection.connect_timeout,
                "allow_agent": False,
                "look_for_keys": False,
                "disabled_algorithms": SSH_DISABLED_ALGORITHMS,
            }
            if credentials.private_key:
                parameters["pkey"] = _parse_private_key(credentials.private_key)
            else:
                parameters["password"] = credentials.password or ""
            client.connect(**parameters)

    def _wait_and_read(self, path: Path, *, timeout: int, max_bytes: int, tunnel=None) -> bytes:
        deadline = self._clock() + timeout
        previous_size = -1
        stable_checks = 0
        while self._clock() < deadline:
            if tunnel is not None and tunnel.bridge_error is not None:
                raise DriverError(
                    "RECEIVER_BRIDGE_FAILED",
                    "The reverse tunnel opened, but the worker could not reach the SFTP receiver.",
                )
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                time.sleep(0.25)
                continue
            if size > max_bytes:
                raise DriverError("CONFIG_TOO_LARGE", "The native backup file is too large.")
            if size > 0 and size == previous_size:
                stable_checks += 1
                if stable_checks >= 2:
                    content = path.read_bytes()
                    if len(content) == size:
                        return content
                    stable_checks = 0
            else:
                previous_size = size
                stable_checks = 0
            time.sleep(0.25)
        if tunnel is not None and tunnel.connection_count == 0:
            raise DriverError(
                "DEVICE_DID_NOT_CONNECT_RECEIVER",
                "CeraOS did not open an SFTP connection through the reverse tunnel. Check its export status and SFTP client settings.",
            )
        raise DriverError(
            "RECEIVER_UPLOAD_TIMEOUT",
            "CeraOS connected to the receiver but did not finish the expected file upload.",
        )

    @staticmethod
    def _validate(context: DriverContext, options: dict) -> None:
        if not context.address:
            raise DriverError("NO_ADDRESS", "The device has no usable management address.")
        if context.credentials is None:
            raise DriverError("NO_CREDENTIALS", "No device credential profile is configured.")
        receiver = context.receiver
        if receiver is None or receiver.credentials is None:
            raise DriverError("NO_RECEIVER_PROFILE", "Configure an enabled SFTP receiver profile.")
        if receiver.mode not in {"direct", "reverse_tunnel"}:
            raise DriverError("INVALID_RECEIVER_PROFILE", "The receiver mode is invalid.")
        if receiver.mode == "direct" and not receiver.advertised_host:
            raise DriverError(
                "INVALID_RECEIVER_PROFILE", "The direct receiver address is not configured."
            )
        receiver_credentials = receiver.credentials
        if not SAFE_RECEIVER_USERNAME.fullmatch(receiver_credentials.username) or not (
            receiver_credentials.password
            and SAFE_RECEIVER_PASSWORD.fullmatch(receiver_credentials.password)
        ):
            raise DriverError(
                "INVALID_RECEIVER_CREDENTIALS",
                "Receiver credentials contain characters unsupported by the CeraOS CLI.",
            )
        allowed = {
            "allow_device_export",
            "restore_point",
            "restore_sftp_port",
            "backup_settle_seconds",
        }
        if set(options) - allowed:
            raise DriverError("INVALID_DRIVER_OPTIONS", "Unsupported CeraOS driver option.")
        if options.get("allow_device_export") is not True:
            raise DriverError(
                "EXPORT_NOT_CONFIRMED",
                "Set allow_device_export to true after approving restore-point creation and export.",
            )
        if options.get("restore_point", "restore-point-1") not in {
            "restore-point-1",
            "restore-point-2",
            "restore-point-3",
        }:
            raise DriverError("INVALID_DRIVER_OPTIONS", "The CeraOS restore point is invalid.")
        if not isinstance(options.get("restore_sftp_port", True), bool):
            raise DriverError("INVALID_DRIVER_OPTIONS", "restore_sftp_port must be boolean.")
        settle_seconds = options.get("backup_settle_seconds", 15)
        if (
            isinstance(settle_seconds, bool)
            or not isinstance(settle_seconds, int)
            or not 0 <= settle_seconds <= 300
        ):
            raise DriverError("INVALID_DRIVER_OPTIONS", "backup_settle_seconds must be 0-300.")
