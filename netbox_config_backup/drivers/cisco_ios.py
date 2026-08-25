from __future__ import annotations

import re

from netbox_config_backup.transports import (
    LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
    NetmikoTransport,
)

from .netmiko_text import NetmikoTextConfigDriver


class _CiscoIOSFamilyDriver(NetmikoTextConfigDriver):
    """Shared read-only configuration collector for Cisco IOS family devices."""

    vendor_name = "Cisco IOS"
    command = "show running-config"
    artifact_format = "cisco_ios_config"
    source = "show_running_config"
    supports_enable = True
    validation_patterns = (re.compile(r"^\s*version\s+\S+", re.IGNORECASE | re.MULTILINE),)
    volatile_line_patterns = (
        re.compile(rb"^Building configuration\.\.\.$", re.IGNORECASE),
        re.compile(
            rb"^Current configuration\s*:\s*\d+\s+bytes$",
            re.IGNORECASE,
        ),
        re.compile(
            rb"^!\s*(?:Last configuration change|NVRAM config last updated) at .*$",
            re.IGNORECASE,
        ),
    )


class CiscoIOSDriver(_CiscoIOSFamilyDriver):
    driver_id = "cisco_ios"
    display_name = "Cisco IOS (Netmiko)"
    netmiko_device_type = "cisco_ios"

    def __init__(self, transport=None) -> None:
        # Older Catalyst IOS releases offer only an ssh-rsa server host key.
        # Keep this compatibility exception local to IOS and require the
        # connection profile's normal strict known_hosts verification.
        super().__init__(
            transport=transport
            or NetmikoTransport(
                disabled_algorithms=LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
            )
        )


class CiscoIOSXEDriver(_CiscoIOSFamilyDriver):
    driver_id = "cisco_xe"
    display_name = "Cisco IOS-XE (Netmiko)"
    netmiko_device_type = "cisco_xe"
