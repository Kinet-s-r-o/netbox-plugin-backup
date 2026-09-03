"""Installation-wide driver choices, without unloading historical revision drivers."""

from collections import Counter
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils.translation import gettext as _

from netbox_config_backup.drivers.base import DriverError
from netbox_config_backup.drivers.registry import DriverRegistry

SIAE_COMPATIBILITY_IDS = frozenset({
    "siae_smos_cli", "siae_smos_ssh", "siae_alfoplus", "siae_alfoplus2",
    "siae_alfoplus80hd", "siae_ags20",
})


def selection_id(driver_id: str) -> str:
    return "siae_smos_auto" if driver_id in SIAE_COMPATIBILITY_IDS else driver_id


def selectable_drivers():
    from netbox_config_backup.drivers import driver_registry

    return sorted(
        (driver for driver in driver_registry.classes() if driver.user_selectable),
        key=lambda driver: driver.display_name.casefold(),
    )


def disabled_driver_ids() -> frozenset[str]:
    from netbox_config_backup.models import OperationalSettings

    values = OperationalSettings.objects.filter(singleton=True).values_list(
        "disabled_driver_ids", flat=True
    ).first()
    return frozenset(values or ())


def driver_is_enabled(driver_id: str, disabled=None) -> bool:
    if disabled is None:
        disabled = disabled_driver_ids()
    return selection_id(driver_id) not in {selection_id(value) for value in disabled}


def driver_usage_counts() -> Counter:
    """Count assignments, including disabled targets/mappings and legacy SIAE IDs."""
    from netbox_config_backup.models import BackupTarget, PlatformMapping

    counts = Counter()
    for model, field in ((BackupTarget, "driver_override"), (PlatformMapping, "driver_id")):
        for row in model.objects.exclude(**{field: ""}).values(field).annotate(total=Count("pk")):
            counts[selection_id(row[field])] += row["total"]
    return counts


def validate_disabled_drivers(values) -> None:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValidationError(_("Disabled drivers must be a list of driver identifiers."))
    if not values:
        return
    in_use = {selection_id(value) for value in values} & driver_usage_counts().keys()
    if in_use:
        labels = {driver.driver_id: driver.display_name for driver in selectable_drivers()}
        raise ValidationError(
            _("These drivers are still assigned to devices or platform mappings: %(drivers)s. "
              "Reassign them before disabling the drivers."),
            params={"drivers": ", ".join(sorted(labels.get(value, value) for value in in_use))},
        )


@contextmanager
def locked_driver_settings():
    """Serialize selection changes and new assignments on the settings singleton."""
    from netbox_config_backup.models import OperationalSettings

    with transaction.atomic():
        yield OperationalSettings.objects.select_for_update().filter(singleton=True).first()


def validate_driver_assignment(driver_id: str, *, field: str, disabled=None) -> None:
    if driver_id and not driver_is_enabled(driver_id, disabled):
        raise ValidationError({field: _("This driver is disabled in Settings > Device drivers.")})


class EnabledDriverRegistry(DriverRegistry):
    """Execution-only gate. The full registry stays available to revision previews."""

    def __init__(self, registry: DriverRegistry):
        super().__init__()
        for driver in registry.classes():
            self.register(driver)

    def create(self, driver_id: str):
        if not driver_is_enabled(driver_id):
            raise DriverError("DRIVER_DISABLED", "Enable this driver in Settings > Device drivers.")
        return super().create(driver_id)
