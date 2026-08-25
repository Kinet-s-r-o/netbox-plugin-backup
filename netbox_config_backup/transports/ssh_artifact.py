from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncssh
from paramiko import AutoAddPolicy, RejectPolicy, SSHClient
from paramiko.ssh_exception import AuthenticationException, SSHException
from scp import SCPClient, SCPException

from netbox_config_backup.drivers.base import DriverContext, DriverError
from netbox_config_backup.transports.known_hosts import materialized_known_hosts
from netbox_config_backup.transports.netmiko import (
    SSH_DISABLED_ALGORITHMS,
    _parse_private_key,
    _translate_exception,
)


@dataclass(frozen=True, slots=True)
class SshArtifactResult:
    content: bytes
    command_output: str = ""


class SshArtifactTransport:
    """Run one vendor backup command and download its artifact over SFTP or SCP."""

    def __init__(
        self,
        client_factory=SSHClient,
        *,
        disabled_algorithms=None,
        transfer_mode: str = "sftp",
        scp_factory=SCPClient,
    ) -> None:
        if transfer_mode not in {"sftp", "scp"}:
            raise ValueError("SSH artifact transfer mode must be 'sftp' or 'scp'.")
        self._client_factory = client_factory
        self._disabled_algorithms = (
            SSH_DISABLED_ALGORITHMS if disabled_algorithms is None else disabled_algorithms
        )
        self._transfer_mode = transfer_mode
        self._scp_factory = scp_factory

    def collect(
        self,
        context: DriverContext,
        *,
        remote_path: str,
        export_command: str = "",
        max_bytes: int = 50 * 1024 * 1024,
    ) -> SshArtifactResult:
        self._validate(context, remote_path=remote_path, export_command=export_command)
        credentials = context.credentials
        if credentials is None:  # validated above, kept for type checking
            raise DriverError("NO_CREDENTIALS", "No credential profile is configured.")

        client = self._client_factory()
        try:
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
                    "disabled_algorithms": self._disabled_algorithms,
                }
                if credentials.private_key:
                    parameters["pkey"] = _parse_private_key(credentials.private_key)
                else:
                    parameters["password"] = credentials.password or ""
                client.connect(**parameters)

                output = ""
                if export_command:
                    _stdin, stdout, stderr = client.exec_command(
                        export_command,
                        timeout=context.connection.command_timeout,
                    )
                    stdout.channel.settimeout(context.connection.command_timeout)
                    output_bytes = stdout.read(64 * 1024 + 1)
                    error_bytes = stderr.read(64 * 1024 + 1)
                    exit_status = stdout.channel.recv_exit_status()
                    if len(output_bytes) > 64 * 1024 or len(error_bytes) > 64 * 1024:
                        raise DriverError(
                            "INVALID_OUTPUT", "Device backup command output is too large."
                        )
                    output = output_bytes.decode("utf-8", errors="replace")
                    if exit_status not in {0, -1}:
                        raise DriverError(
                            "COMMAND_REJECTED",
                            f"Device rejected the backup export command (exit status {exit_status}).",
                        )

                if self._transfer_mode == "scp":
                    content = self._download_scp(
                        client,
                        remote_path=remote_path,
                        max_bytes=max_bytes,
                        timeout=context.connection.command_timeout,
                    )
                else:
                    content = self._download_sftp(
                        client,
                        remote_path=remote_path,
                        max_bytes=max_bytes,
                    )
                return SshArtifactResult(content=content, command_output=output)
        except DriverError:
            raise
        except AuthenticationException as exc:
            raise DriverError("AUTH_FAILED", "Device authentication failed.") from exc
        except (SSHException, OSError, TimeoutError) as exc:
            raise _translate_exception(exc, phase="connect") from exc
        finally:
            client.close()

    @staticmethod
    def _download_sftp(client, *, remote_path: str, max_bytes: int) -> bytes:
        with client.open_sftp() as sftp:
            try:
                size = sftp.stat(remote_path).st_size
            except OSError as exc:
                raise DriverError(
                    "BACKUP_FILE_MISSING",
                    "The expected backup file was not found on the device.",
                ) from exc
            if size <= 0:
                raise DriverError("EMPTY_CONFIG", "The device backup file is empty.")
            if size > max_bytes:
                raise DriverError("CONFIG_TOO_LARGE", "The device backup file is too large.")
            buffer = BytesIO()
            sftp.getfo(remote_path, buffer)
            content = buffer.getvalue()
            if len(content) != size or len(content) > max_bytes:
                raise DriverError(
                    "INVALID_OUTPUT", "The device backup file download was incomplete."
                )
            return content

    def _download_scp(
        self,
        client,
        *,
        remote_path: str,
        max_bytes: int,
        timeout: int,
    ) -> bytes:
        def enforce_limit(_filename, size, transferred, _peername) -> None:
            if size > max_bytes or transferred > max_bytes:
                raise DriverError("CONFIG_TOO_LARGE", "The device backup file is too large.")

        with TemporaryDirectory(prefix="netbox-config-backup-scp-") as directory:
            destination = Path(directory) / "artifact"
            try:
                with self._scp_factory(
                    client.get_transport(),
                    socket_timeout=timeout,
                    progress4=enforce_limit,
                ) as scp:
                    scp.get(remote_path, local_path=str(destination))
            except DriverError:
                raise
            except SCPException as exc:
                raise DriverError(
                    "ARTIFACT_DOWNLOAD_FAILED",
                    "The device backup file could not be downloaded over SCP.",
                ) from exc
            try:
                size = destination.stat().st_size
            except OSError as exc:
                raise DriverError(
                    "BACKUP_FILE_MISSING",
                    "The expected backup file was not found on the device.",
                ) from exc
            if size <= 0:
                raise DriverError("EMPTY_CONFIG", "The device backup file is empty.")
            if size > max_bytes:
                raise DriverError("CONFIG_TOO_LARGE", "The device backup file is too large.")
            with destination.open("rb") as artifact_file:
                content = artifact_file.read(max_bytes + 1)
            if len(content) != size or len(content) > max_bytes:
                raise DriverError(
                    "INVALID_OUTPUT", "The device backup file download was incomplete."
                )
            return content

    @staticmethod
    def _validate(context: DriverContext, *, remote_path: str, export_command: str) -> None:
        if not context.address:
            raise DriverError("NO_ADDRESS", "The device has no usable management address.")
        if context.credentials is None:
            raise DriverError("NO_CREDENTIALS", "No credential profile is configured.")
        if (
            not remote_path
            or "\x00" in remote_path
            or any(part == ".." for part in remote_path.replace("\\", "/").split("/"))
            or len(remote_path) > 512
        ):
            raise DriverError("INVALID_DRIVER_OPTIONS", "The remote backup path is invalid.")
        if (
            any(character in export_command for character in "\r\n\x00")
            or len(export_command) > 512
        ):
            raise DriverError("INVALID_DRIVER_OPTIONS", "The backup export command is invalid.")


class RacomRaySshArtifactTransport:
    """RACOM transport with automatic compatibility for old ssh-dss host keys."""

    def __init__(self, connect_factory=asyncssh.connect, scp_factory=asyncssh.scp) -> None:
        self._connect_factory = connect_factory
        self._scp_factory = scp_factory

    def collect(
        self,
        context: DriverContext,
        *,
        remote_path: str,
        export_command: str = "",
        max_bytes: int = 50 * 1024 * 1024,
    ) -> SshArtifactResult:
        SshArtifactTransport._validate(
            context,
            remote_path=remote_path,
            export_command=export_command,
        )
        try:
            return asyncio.run(
                self._collect_async(
                    context,
                    remote_path=remote_path,
                    export_command=export_command,
                    max_bytes=max_bytes,
                )
            )
        except DriverError:
            raise
        except asyncssh.PermissionDenied as exc:
            raise DriverError("AUTH_FAILED", "Device authentication failed.") from exc
        except asyncssh.HostKeyNotVerifiable as exc:
            raise DriverError("HOST_KEY_FAILED", "SSH host key verification failed.") from exc
        except asyncssh.KeyExchangeFailed as exc:
            raise DriverError(
                "SSH_NEGOTIATION_FAILED",
                "The RAy SSH algorithms could not be negotiated.",
            ) from exc
        except TimeoutError as exc:
            raise DriverError("CONNECTION_TIMEOUT", "The device connection timed out.") from exc
        except (asyncssh.Error, OSError) as exc:
            raise _translate_exception(exc, phase="connect") from exc

    async def _collect_async(
        self,
        context: DriverContext,
        *,
        remote_path: str,
        export_command: str,
        max_bytes: int,
    ) -> SshArtifactResult:
        credentials = context.credentials
        if credentials is None:  # validated by the shared validator
            raise DriverError("NO_CREDENTIALS", "No credential profile is configured.")

        connect_options = {
            "host": context.address,
            "port": context.connection.port,
            "username": credentials.username,
            # Keep AsyncSSH's modern defaults and add DSA only inside the
            # RACOM driver for older RAy firmware.
            "server_host_key_algs": "+ssh-dss",
            "connect_timeout": context.connection.connect_timeout,
            "login_timeout": context.connection.connect_timeout,
            "keepalive_interval": context.connection.keepalive,
            "agent_path": None,
            "encoding": "utf-8",
        }
        with materialized_known_hosts(context.connection) as known_hosts_path:
            if context.connection.verify_host_key:
                if known_hosts_path:
                    connect_options["known_hosts"] = known_hosts_path
            else:
                connect_options["known_hosts"] = None
            if credentials.private_key:
                try:
                    connect_options["client_keys"] = [
                        asyncssh.import_private_key(credentials.private_key)
                    ]
                except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as exc:
                    raise DriverError(
                        "INVALID_PRIVATE_KEY",
                        "The SSH private key could not be loaded.",
                    ) from exc
            else:
                connect_options["password"] = credentials.password or ""
                connect_options["client_keys"] = []

            return await self._download_async(
                connect_options,
                context=context,
                remote_path=remote_path,
                export_command=export_command,
                max_bytes=max_bytes,
            )

    async def _download_async(
        self, connect_options, *, context, remote_path, export_command, max_bytes
    ) -> SshArtifactResult:
        async with self._connect_factory(**connect_options) as connection:
            output = ""
            if export_command:
                result = await connection.run(
                    export_command,
                    check=False,
                    timeout=context.connection.command_timeout,
                )
                output = result.stdout or ""
                error_output = result.stderr or ""
                if len(output.encode()) > 64 * 1024 or len(error_output.encode()) > 64 * 1024:
                    raise DriverError(
                        "INVALID_OUTPUT", "Device backup command output is too large."
                    )
                if result.exit_status not in {0, -1}:
                    raise DriverError(
                        "COMMAND_REJECTED",
                        f"Device rejected the backup export command (exit status {result.exit_status}).",
                    )

            with TemporaryDirectory(prefix="netbox-config-backup-dsa-scp-") as directory:
                destination = Path(directory) / "artifact"

                def enforce_limit(_source, _destination, copied, total) -> None:
                    if total > max_bytes or copied > max_bytes:
                        raise DriverError(
                            "CONFIG_TOO_LARGE",
                            "The device backup file is too large.",
                        )

                await self._scp_factory(
                    (connection, remote_path),
                    destination,
                    recurse=False,
                    preserve=False,
                    progress_handler=enforce_limit,
                )
                try:
                    size = destination.stat().st_size
                except OSError as exc:
                    raise DriverError(
                        "BACKUP_FILE_MISSING",
                        "The expected backup file was not found on the device.",
                    ) from exc
                if size <= 0:
                    raise DriverError("EMPTY_CONFIG", "The device backup file is empty.")
                if size > max_bytes:
                    raise DriverError("CONFIG_TOO_LARGE", "The device backup file is too large.")
                content = destination.read_bytes()
                if len(content) != size or len(content) > max_bytes:
                    raise DriverError(
                        "INVALID_OUTPUT",
                        "The device backup file download was incomplete.",
                    )

        return SshArtifactResult(content=content, command_output=output)
