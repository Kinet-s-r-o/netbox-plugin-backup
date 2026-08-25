from .cisco_ios import CiscoIOSDriver, CiscoIOSXEDriver
from .fake import FakeDriver
from .mikrotik_routeros import MikroTikRouterOSDriver
from .native_exports import CERAGON_SIAE_DRIVERS
from .netmiko_text import NetmikoTextConfigDriver
from .network_vendors import BUILTIN_NETWORK_VENDOR_DRIVERS
from .racom import RACOM_DRIVERS
from .registry import DriverRegistry
from .siae_smos import SiaeSmosAutoDriver, SiaeSmosCliDriver, SiaeSmosSSHDriver

driver_registry = DriverRegistry()
driver_registry.register(FakeDriver)
driver_registry.register(MikroTikRouterOSDriver)
driver_registry.register(CiscoIOSDriver)
driver_registry.register(CiscoIOSXEDriver)
driver_registry.register(SiaeSmosCliDriver)
driver_registry.register(SiaeSmosSSHDriver)
driver_registry.register(SiaeSmosAutoDriver)
for driver_class in BUILTIN_NETWORK_VENDOR_DRIVERS:
    driver_registry.register(driver_class)
for driver_class in (*RACOM_DRIVERS, *CERAGON_SIAE_DRIVERS):
    driver_registry.register(driver_class)
driver_registry.load_entry_points()

__all__ = [
    "CiscoIOSDriver",
    "CiscoIOSXEDriver",
    "DriverRegistry",
    "FakeDriver",
    "MikroTikRouterOSDriver",
    "NetmikoTextConfigDriver",
    "SiaeSmosAutoDriver",
    "SiaeSmosCliDriver",
    "SiaeSmosSSHDriver",
    "driver_registry",
]
