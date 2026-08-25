from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from .base import BackupDriver

DriverType = TypeVar("DriverType", bound=type[BackupDriver])
DRIVER_ENTRY_POINT_GROUP = "netbox_config_backup.drivers"
SUPPORTED_DRIVER_API_VERSION = 1


class DriverRegistryError(LookupError):
    pass


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, type[BackupDriver]] = {}

    def register(self, driver_class: DriverType) -> DriverType:
        driver_id = getattr(driver_class, "driver_id", "")
        if not driver_id or not isinstance(driver_id, str):
            raise ValueError("A driver must declare a non-empty string driver_id.")
        if driver_id in self._drivers:
            raise ValueError(f"Driver {driver_id!r} is already registered.")
        self._drivers[driver_id] = driver_class
        return driver_class

    def unregister(self, driver_id: str) -> None:
        try:
            del self._drivers[driver_id]
        except KeyError as exc:
            raise DriverRegistryError(f"Unknown backup driver: {driver_id}") from exc

    def create(self, driver_id: str) -> BackupDriver:
        try:
            driver_class = self._drivers[driver_id]
        except KeyError as exc:
            raise DriverRegistryError(f"Unknown backup driver: {driver_id}") from exc
        return driver_class()

    def contains(self, driver_id: str) -> bool:
        return driver_id in self._drivers

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))

    def classes(self) -> Iterable[type[BackupDriver]]:
        return tuple(self._drivers[key] for key in sorted(self._drivers))

    def load_entry_points(self, entries: Iterable[Any] | None = None) -> tuple[str, ...]:
        if entries is None:
            from importlib.metadata import entry_points

            entries = entry_points(group=DRIVER_ENTRY_POINT_GROUP)

        loaded = []
        for entry in sorted(entries, key=lambda item: item.name):
            try:
                driver_class = entry.load()
            except Exception as exc:
                raise DriverRegistryError(
                    f"External driver entry point {entry.name!r} could not be loaded."
                ) from exc
            if not isinstance(driver_class, type) or not issubclass(
                driver_class,
                BackupDriver,
            ):
                raise DriverRegistryError(
                    f"External driver entry point {entry.name!r} is not a BackupDriver."
                )
            if driver_class.driver_id != entry.name:
                raise DriverRegistryError(
                    f"External driver entry point {entry.name!r} has a mismatched driver ID."
                )
            if driver_class.driver_api_version != SUPPORTED_DRIVER_API_VERSION:
                raise DriverRegistryError(
                    f"External driver {entry.name!r} uses an unsupported API version."
                )
            try:
                self.register(driver_class)
            except ValueError as exc:
                raise DriverRegistryError(
                    f"External driver {entry.name!r} conflicts with a registered driver."
                ) from exc
            loaded.append(entry.name)
        return tuple(loaded)
