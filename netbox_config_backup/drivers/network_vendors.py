from __future__ import annotations

import re

from netbox_config_backup.transports import (
    LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
    NetmikoTransport,
)
from netbox_config_backup.transports.netmiko import SSH_DISABLED_ALGORITHMS

from .netmiko_text import NetmikoTextConfigDriver

SHOW_RUNNING_CONFIG = "show running-config"

IOS_LIKE_MARKERS = (
    re.compile(
        r"^\s*(?:hostname|interface|version|current configuration\s*:|"
        r"!\s*current configuration\s*:)",
        re.IGNORECASE | re.MULTILINE,
    ),
)
VERSION_OR_HOST_MARKERS = (
    re.compile(
        r"^\s*!?\s*(?:version|hostname|sysname)\s+\S+",
        re.IGNORECASE | re.MULTILINE,
    ),
)


class DellOS6Driver(NetmikoTextConfigDriver):
    driver_id = "dell_os6"
    display_name = "Dell Networking OS6 (Netmiko)"
    vendor_name = "Dell OS6"
    netmiko_device_type = "dell_os6"
    command = SHOW_RUNNING_CONFIG
    validation_patterns = IOS_LIKE_MARKERS


class DellOS9Driver(DellOS6Driver):
    driver_id = "dell_os9"
    display_name = "Dell Networking OS9 / Force10 (Netmiko)"
    vendor_name = "Dell OS9"
    netmiko_device_type = "dell_os9"


class DellPowerConnectDriver(DellOS6Driver):
    driver_id = "dell_powerconnect"
    display_name = "Dell PowerConnect (Netmiko)"
    vendor_name = "Dell PowerConnect"
    netmiko_device_type = "dell_powerconnect"


class DellOS10Driver(NetmikoTextConfigDriver):
    driver_id = "dell_os10"
    display_name = "Dell SmartFabric OS10 (Netmiko)"
    vendor_name = "Dell OS10"
    netmiko_device_type = "dell_os10"
    command = "show running-configuration"
    validation_patterns = (
        re.compile(
            r"^\s*(?:!\s*Version\s+\S+|hostname\s+\S+|interface\s+\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
    )
    volatile_line_patterns = (re.compile(rb"^!\s*Last configuration change at .*$", re.IGNORECASE),)


class FiberstoreFSOSDriver(NetmikoTextConfigDriver):
    driver_id = "fiberstore_fsos"
    display_name = "FS / Fiberstore FSOS (Netmiko)"
    vendor_name = "FSOS"
    netmiko_device_type = "fiberstore_fsos"
    command = SHOW_RUNNING_CONFIG
    validation_patterns = VERSION_OR_HOST_MARKERS
    volatile_line_patterns = (re.compile(rb"^!\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}$"),)


class FiberstoreFSOSV2Driver(FiberstoreFSOSDriver):
    driver_id = "fiberstore_fsosv2"
    display_name = "FS / Fiberstore FSOS v2 (Netmiko)"
    netmiko_device_type = "fiberstore_fsosv2"


class HPComwareDriver(NetmikoTextConfigDriver):
    driver_id = "hp_comware"
    display_name = "HP/HPE Comware (Netmiko)"
    vendor_name = "HP Comware"
    netmiko_device_type = "hp_comware"
    command = "display current-configuration"
    validation_patterns = VERSION_OR_HOST_MARKERS


class HPProCurveDriver(NetmikoTextConfigDriver):
    driver_id = "hp_procurve"
    display_name = "HP/Aruba ProCurve (Netmiko)"
    vendor_name = "HP ProCurve"
    netmiko_device_type = "hp_procurve"
    command = SHOW_RUNNING_CONFIG
    validation_patterns = (
        re.compile(
            r"^\s*(?:hostname\s+\S+|;\s*\S+\s+Configuration Editor)",
            re.IGNORECASE | re.MULTILINE,
        ),
    )


class HuaweiVRPDriver(NetmikoTextConfigDriver):
    driver_id = "huawei_vrp"
    display_name = "Huawei VRP (Netmiko)"
    vendor_name = "Huawei VRP"
    netmiko_device_type = "huawei_vrp"
    command = "display current-configuration"
    validation_patterns = VERSION_OR_HOST_MARKERS

    def __init__(self, transport=None) -> None:
        # Older VRP releases can offer only an ssh-rsa server host key. Keep
        # that compatibility local to this driver and retain strict host-key
        # verification through the selected Connection Profile.
        super().__init__(
            transport=transport
            or NetmikoTransport(
                disabled_algorithms=LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
            )
        )


class HuaweiVRPV8Driver(HuaweiVRPDriver):
    driver_id = "huawei_vrpv8"
    display_name = "Huawei VRP v8 (Netmiko)"
    netmiko_device_type = "huawei_vrpv8"

    def __init__(self, transport=None) -> None:
        NetmikoTextConfigDriver.__init__(
            self,
            transport=transport or NetmikoTransport(disabled_algorithms=SSH_DISABLED_ALGORITHMS),
        )


class TPLinkJetStreamDriver(NetmikoTextConfigDriver):
    driver_id = "tplink_jetstream"
    display_name = "TP-Link JetStream (Netmiko)"
    vendor_name = "TP-Link JetStream"
    netmiko_device_type = "tplink_jetstream"
    command = SHOW_RUNNING_CONFIG
    supports_enable = True
    validation_patterns = IOS_LIKE_MARKERS


class UbiquitiEdgeRouterDriver(NetmikoTextConfigDriver):
    driver_id = "ubiquiti_edgerouter"
    display_name = "Ubiquiti EdgeRouter (Netmiko)"
    vendor_name = "Ubiquiti EdgeRouter"
    netmiko_device_type = "ubiquiti_edgerouter"
    command = "show configuration commands"
    artifact_format = "edgeos_set_commands"
    validation_patterns = (re.compile(r"^\s*set\s+\S+", re.IGNORECASE | re.MULTILINE),)


class UbiquitiEdgeSwitchDriver(NetmikoTextConfigDriver):
    driver_id = "ubiquiti_edgeswitch"
    display_name = "Ubiquiti EdgeSwitch (Netmiko)"
    vendor_name = "Ubiquiti EdgeSwitch"
    netmiko_device_type = "ubiquiti_edgeswitch"
    command = SHOW_RUNNING_CONFIG
    supports_enable = True
    validation_patterns = IOS_LIKE_MARKERS


class ZTEZXROSDriver(NetmikoTextConfigDriver):
    driver_id = "zte_zxros"
    display_name = "ZTE ZXROS (Netmiko)"
    vendor_name = "ZTE ZXROS"
    netmiko_device_type = "zte_zxros"
    command = SHOW_RUNNING_CONFIG
    supports_enable = True
    validation_patterns = IOS_LIKE_MARKERS


BUILTIN_NETWORK_VENDOR_DRIVERS = (
    DellOS6Driver,
    DellOS9Driver,
    DellOS10Driver,
    DellPowerConnectDriver,
    FiberstoreFSOSDriver,
    FiberstoreFSOSV2Driver,
    HPComwareDriver,
    HPProCurveDriver,
    HuaweiVRPDriver,
    HuaweiVRPV8Driver,
    TPLinkJetStreamDriver,
    UbiquitiEdgeRouterDriver,
    UbiquitiEdgeSwitchDriver,
    ZTEZXROSDriver,
)
