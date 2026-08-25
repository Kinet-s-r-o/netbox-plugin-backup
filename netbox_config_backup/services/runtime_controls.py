from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError


@dataclass(frozen=True, slots=True)
class RuntimeControls:
    events_enabled: bool
    notify_on_every_failure: bool


def get_runtime_controls() -> RuntimeControls:
    """Return database controls, falling back to deployment configuration."""

    plugin_settings = _plugin_settings()
    fallback = RuntimeControls(
        events_enabled=bool(plugin_settings.get("events_enabled", True)),
        notify_on_every_failure=bool(plugin_settings.get("notify_on_every_failure", False)),
    )
    try:
        from netbox_config_backup.models import OperationalSettings

        operational_settings = OperationalSettings.objects.filter(singleton=True).first()
    except (DatabaseError, ImproperlyConfigured, LookupError):
        return fallback
    if operational_settings is None:
        return fallback
    return RuntimeControls(
        events_enabled=operational_settings.events_enabled,
        notify_on_every_failure=operational_settings.notify_on_every_failure,
    )


def _plugin_settings() -> dict:
    try:
        return settings.PLUGINS_CONFIG["netbox_config_backup"]
    except (AttributeError, ImproperlyConfigured, KeyError, TypeError):
        return {}
