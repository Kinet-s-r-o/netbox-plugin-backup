from __future__ import annotations

import re
import time
from dataclasses import replace

from netmiko.cisco.cisco_ios import CiscoIosSSH, CiscoIosTelnet
from netmiko.exceptions import NetmikoAuthenticationException, ReadTimeout

from netbox_config_backup.choices import ConnectionProtocolChoices
from netbox_config_backup.transports import (
    LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
    NetmikoTransport,
)

from .base import DriverContext, DriverError, ValidationResult
from .netmiko_text import NetmikoTextConfigDriver


class _SiaeSmosCliSession:
    """Read-only SM-OS CLI behavior shared by SSH and legacy Telnet sessions."""

    _prompt_pattern = re.compile(r"([^\r\n]*[>#])\s*$")
    _pager_pattern = re.compile(r"\x08*--More--\x08*", re.IGNORECASE)
    _maximum_pager_responses = 10000
    _maximum_paginated_output_chars = 50 * 1024 * 1024

    def cleanup(self, command: str = "logout") -> None:
        """Release the limited SM-OS CLI session before closing the transport."""
        if self.session_log:
            self.session_log.fin = True
        self.write_channel(command + "\r")

    def send_command(
        self,
        command_string: str,
        *,
        read_timeout: float = 90.0,
        strip_prompt: bool = True,
        strip_command: bool = True,
        **_kwargs,
    ) -> str:
        """Run a read-only command and advance SM-OS ``--More--`` prompts."""
        normalized_command = self.normalize_cmd(command_string)
        self.write_channel(normalized_command)
        output = self.read_channel_timing(last_read=0.5, read_timeout=read_timeout)
        pager_responses = 0

        while self._pager_pattern.search(output):
            output = self._pager_pattern.sub("", output)
            pager_responses += 1
            if (
                pager_responses > self._maximum_pager_responses
                or len(output) > self._maximum_paginated_output_chars
            ):
                raise ReadTimeout("SIAE SM-OS paginated output exceeded its safety limit.")
            # Space advances the current page without executing another command.
            self.write_channel(" ")
            output += self.read_channel_timing(last_read=0.5, read_timeout=read_timeout)

        return self._sanitize_output(
            output,
            strip_command=strip_command,
            command_string=normalized_command,
            strip_prompt=strip_prompt,
        )


class SiaeSmosTelnet(_SiaeSmosCliSession, CiscoIosTelnet):
    """Minimal SM-OS Telnet session without configuration-changing setup commands."""

    def telnet_login(
        self,
        pri_prompt_terminator: str = r"#\s*$",
        alt_prompt_terminator: str = r">\s*$",
        username_pattern: str = r"(?:user\s*name|username|login|user:)",
        pwd_pattern: str = r"(?:password|passwd)",
        delay_factor: float = 1.0,
        max_loops: int = 30,
    ) -> str:
        """Log in to SM-OS, whose username prompt requires an LF terminator."""
        delay = self.select_delay_factor(delay_factor)
        if delay < 1 and not self._legacy_mode and self.fast_cli:
            delay = 1

        transcript = ""
        username_sent = False
        password_sent = False
        time.sleep(0.5 * delay)

        for _ in range(max_loops):
            try:
                new_data = self.read_channel()
                transcript += new_data
            except EOFError as exc:
                self._close_failed_login()
                raise NetmikoAuthenticationException("SIAE Telnet login failed.") from exc

            tail = transcript[-2048:]
            if not username_sent and re.search(
                rf"{username_pattern}\s*:?\s*$", tail, re.IGNORECASE
            ):
                # SM-OS does not advance to its password prompt when Netmiko's
                # generic Telnet login sends username + CR.
                self.write_channel(self.username + "\n")
                username_sent = True
                time.sleep(0.5 * delay)
                continue

            if (
                username_sent
                and not password_sent
                and re.search(rf"{pwd_pattern}\s*:?\s*$", tail, re.IGNORECASE)
            ):
                # Unlike the username field, the hidden SM-OS password field
                # accepts an interactive CR terminator and rejects LF.
                self.write_channel((self.password or "") + "\r")
                password_sent = True
                time.sleep(0.5 * delay)
                continue

            if (
                password_sent
                and (
                    prompt_match := self._prompt_pattern.search(
                        new_data if new_data.strip() else tail
                    )
                )
                and (
                    re.search(pri_prompt_terminator, prompt_match.group(1))
                    or re.search(alt_prompt_terminator, prompt_match.group(1))
                )
            ):
                prompt = prompt_match.group(1).strip()
                self.base_prompt = prompt[:-1] if len(prompt) > 1 else prompt
                return transcript

            if password_sent and re.search(
                r"(?:incorrect|invalid|failed|denied|bad password)",
                tail,
                re.IGNORECASE,
            ):
                break
            time.sleep(0.25 * delay)

        self._close_failed_login()
        raise NetmikoAuthenticationException("SIAE Telnet login failed.")

    def _close_failed_login(self) -> None:
        if self.remote_conn is not None:
            self.remote_conn.close()

    def session_preparation(self) -> None:
        # telnet_login() has already consumed and recorded the prompt. Do not ask
        # for it again and do not send Cisco terminal configuration commands.
        if not self.base_prompt:
            raise ReadTimeout("SIAE SM-OS prompt was not detected after login.")


class SiaeSmosSSH(_SiaeSmosCliSession, CiscoIosSSH):
    """Minimal SM-OS SSH session without terminal or configuration commands."""

    def session_preparation(self) -> None:
        # Netmiko's Cisco base class would send terminal width and paging
        # commands. SM-OS paging is handled locally by send_command(), so the
        # only safe initialization needed here is prompt discovery.
        prompt = self.find_prompt(pattern=r"[>#]\s*$")
        match = self._prompt_pattern.search(prompt)
        if match is None:
            raise ReadTimeout("SIAE SM-OS prompt was not detected after SSH login.")
        detected_prompt = match.group(1).strip()
        self.base_prompt = detected_prompt[:-1] if len(detected_prompt) > 1 else detected_prompt


# This appliance generation only offers an ssh-rsa host key. Permit that host
# key for this driver while keeping legacy ssh-rsa user-key authentication
# disabled. Password authentication and strict known_hosts verification remain
# enabled through the normal connection profile.
SIAE_LEGACY_SSH_DISABLED_ALGORITHMS = LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS


class SiaeSmosCliDriver(NetmikoTextConfigDriver):
    """Collect the read-only SM-OS running configuration over legacy Telnet."""

    driver_id = "siae_smos_cli"
    display_name = "SIAE SM-OS CLI snapshot (read-only Telnet)"
    user_selectable = False
    vendor_name = "SIAE SM-OS"
    netmiko_device_type = "siae_smos_telnet"
    command = "show running-config"
    filename = "siae-smos-running-config.cfg"
    artifact_format = "siae_smos_running_config"
    source = "smos_running_configuration"
    capabilities = frozenset({"running_config", "plaintext_transport"})
    validation_patterns = (
        re.compile(
            r"^\s*(?:#\s*)?(?:building configuration|version\s+\S+|hostname\s+\S+|"
            r"interface\s+\S+|vlan\s+\d+)",
            re.IGNORECASE | re.MULTILINE,
        ),
    )
    unsupported_command_pattern = re.compile(
        r"C\s+interp:\s*unknown\s+symbol\s+name\s+['\"]?running['\"]?",
        re.IGNORECASE,
    )
    volatile_line_patterns = (re.compile(rb"^\s*#?\s*Building configuration.*$", re.IGNORECASE),)

    def __init__(self, transport=None) -> None:
        super().__init__(transport=transport or NetmikoTransport(connector=SiaeSmosTelnet))

    def collect(self, context: DriverContext):
        if context.connection.protocol not in {
            ConnectionProtocolChoices.AUTOMATIC,
            ConnectionProtocolChoices.TELNET,
        }:
            raise DriverError(
                "PROTOCOL_MISMATCH",
                "The SIAE Telnet driver cannot use an SSH connection profile.",
            )
        if context.credentials and not context.credentials.password:
            raise DriverError(
                "UNSUPPORTED_AUTH",
                "SIAE SM-OS Telnet requires password credentials.",
            )
        return super().collect(context)

    def validate(self, artifact) -> ValidationResult:
        try:
            text = artifact.content.decode("utf-8")
        except UnicodeError:
            return super().validate(artifact)
        if self.unsupported_command_pattern.search(text):
            return ValidationResult(
                valid=False,
                error_code="COMMAND_UNSUPPORTED",
                safe_message=(
                    "Connection and authentication succeeded, but this SIAE firmware does not "
                    "support the read-only show running-config command. Legacy ALFOplus "
                    "requires a model-specific native backup workflow."
                ),
            )
        result = super().validate(artifact)
        if not result.valid and result.error_code == "INCOMPLETE_CONFIG":
            return ValidationResult(
                False,
                "INCOMPLETE_CONFIG",
                (
                    "Connection and authentication succeeded, but the SIAE response did not "
                    "contain a complete running configuration."
                ),
            )
        return result


class SiaeSmosSSHDriver(SiaeSmosCliDriver):
    """Collect the read-only SM-OS running configuration over SSH."""

    driver_id = "siae_smos_ssh"
    display_name = "SIAE SM-OS CLI snapshot (read-only SSH)"
    user_selectable = False
    netmiko_device_type = "siae_smos"
    capabilities = frozenset({"running_config", "ssh", "legacy_ssh_rsa_host_key"})

    def __init__(self, transport=None) -> None:
        super().__init__(
            transport=transport
            or NetmikoTransport(
                connector=SiaeSmosSSH,
                disabled_algorithms=SIAE_LEGACY_SSH_DISABLED_ALGORITHMS,
            )
        )

    def collect(self, context: DriverContext):
        if context.connection.protocol not in {
            ConnectionProtocolChoices.AUTOMATIC,
            ConnectionProtocolChoices.SSH,
        }:
            raise DriverError(
                "PROTOCOL_MISMATCH",
                "The SIAE SSH driver cannot use a Telnet connection profile.",
            )
        if context.credentials and not context.credentials.password:
            raise DriverError(
                "UNSUPPORTED_AUTH",
                "SIAE SM-OS SSH currently requires password credentials.",
            )
        return NetmikoTextConfigDriver.collect(self, context)


class SiaeSmosAutoDriver(SiaeSmosCliDriver):
    """Collect an SM-OS backup without exposing transport/model drivers in the UI.

    The selected connection profile remains authoritative for SSH versus Telnet. The
    default automatic method first attempts the read-only running configuration. A
    native SFTP fallback is used only when an administrator has configured both its
    model recipe and remote path; export commands are still subject to the native
    driver's explicit confirmation guard.
    """

    driver_id = "siae_smos_auto"
    display_name = "SIAE SM-OS (automatic backup)"
    user_selectable = True
    capabilities = frozenset(
        {
            "running_config",
            "native_backup",
            "automatic_method",
            "sftp",
            "ssh",
            "telnet",
            "plaintext_transport_optional",
            "legacy_ssh_rsa_host_key",
        }
    )

    def __init__(self, *, telnet_driver=None, ssh_driver=None) -> None:
        self._telnet_driver = telnet_driver or SiaeSmosCliDriver()
        self._ssh_driver = ssh_driver or SiaeSmosSSHDriver()

    def collect(self, context: DriverContext):
        method = self._backup_method(context.options)
        if method == "native":
            return self._collect_native(context)

        cli_driver = self._cli_driver(context)
        cli_context = replace(
            context,
            options={
                key: value for key, value in context.options.items() if key == "max_output_bytes"
            },
        )
        artifacts = cli_driver.collect(cli_context)
        if method == "cli" or all(cli_driver.validate(item).valid for item in artifacts):
            return artifacts

        validation_codes = {cli_driver.validate(item).error_code for item in artifacts}
        if validation_codes & {
            "COMMAND_UNSUPPORTED",
            "INCOMPLETE_CONFIG",
        } and self._native_fallback_configured(context):
            return self._collect_native(context)
        return artifacts

    def _cli_driver(self, context: DriverContext):
        protocol = context.connection.protocol
        if protocol == ConnectionProtocolChoices.AUTOMATIC:
            if context.connection.port == 22:
                protocol = ConnectionProtocolChoices.SSH
            elif context.connection.port == 23:
                protocol = ConnectionProtocolChoices.TELNET
            else:
                raise DriverError(
                    "DRIVER_SETUP_REQUIRED",
                    (
                        "Select SSH or Telnet in the connection profile when SIAE uses a "
                        "non-standard management port."
                    ),
                )
        if protocol == ConnectionProtocolChoices.SSH:
            return self._ssh_driver
        if protocol == ConnectionProtocolChoices.TELNET:
            return self._telnet_driver
        raise DriverError(
            "UNSUPPORTED_PROTOCOL",
            "The SIAE SM-OS driver supports only SSH and Telnet.",
        )

    @staticmethod
    def _backup_method(options) -> str:
        allowed = {
            "backup_method",
            "native_model",
            "remote_path",
            "export_command",
            "allow_export_command",
            "allow_device_export",
            "allow_legacy_ftp_setup",
            "sync_receiver_credentials",
            "web_port",
            "max_output_bytes",
        }
        if set(options) - allowed:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                "SIAE SM-OS driver options contain an unsupported setting.",
            )
        method = options.get("backup_method", "auto")
        if method not in {"auto", "cli", "native"}:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                "SIAE backup_method must be auto, cli, or native.",
            )
        return method

    @staticmethod
    def _native_fallback_configured(context: DriverContext) -> bool:
        options = context.options
        receiver = context.receiver
        if (
            receiver is not None
            and receiver.protocol == "ftp"
            and options.get("allow_device_export") is True
        ):
            return True
        return bool(options.get("remote_path") and options.get("native_model"))

    @staticmethod
    def _native_driver(model: str):
        from .native_exports import (
            SiaeAGS20Driver,
            SiaeALFOplus2Driver,
            SiaeALFOplus80HDDriver,
            SiaeALFOplusDriver,
        )

        drivers = {
            "alfoplus": SiaeALFOplusDriver,
            "alfoplus2": SiaeALFOplus2Driver,
            "alfoplus80hd": SiaeALFOplus80HDDriver,
            "alfoplus80hdx": SiaeALFOplus80HDDriver,
            "ags20": SiaeAGS20Driver,
        }
        try:
            return drivers[model]()
        except KeyError as exc:
            raise DriverError(
                "DRIVER_SETUP_REQUIRED",
                "Configure a supported native_model for the SIAE native backup fallback.",
            ) from exc

    def _collect_native(self, context: DriverContext):
        receiver = context.receiver
        model = context.options.get("native_model", "")
        if not model and receiver is not None and receiver.protocol == "ftp":
            model = "alfoplus"
        if not isinstance(model, str) or not model:
            raise DriverError(
                "DRIVER_SETUP_REQUIRED",
                "Configure native_model before using the SIAE native backup fallback.",
            )
        native_options = {
            key: value
            for key, value in context.options.items()
            if key
            in {
                "remote_path",
                "export_command",
                "allow_export_command",
                "allow_device_export",
                "allow_legacy_ftp_setup",
                "sync_receiver_credentials",
                "web_port",
                "max_output_bytes",
            }
        }
        return self._native_driver(model).collect(replace(context, options=native_options))

    def validate(self, artifact) -> ValidationResult:
        if artifact.artifact_type in {"backup_manifest", "native_backup"}:
            from .native_exports import SftpNativeBackupDriver

            return SftpNativeBackupDriver().validate(artifact)
        return super().validate(artifact)

    def normalize(self, artifact) -> bytes:
        if artifact.artifact_type in {"backup_manifest", "native_backup"}:
            return artifact.content
        return super().normalize(artifact)
