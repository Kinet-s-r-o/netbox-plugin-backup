from __future__ import annotations

from django.db import OperationalError, ProgrammingError

from netbox_config_backup.choices import InterfaceLanguageChoices
from netbox_config_backup.models import OperationalSettings

SESSION_KEY = "netbox_config_backup.ui_language"
SUPPORTED_UI_LANGUAGES = frozenset(InterfaceLanguageChoices.values)


def configured_ui_language() -> str:
    try:
        value = (
            OperationalSettings.objects.filter(singleton=True)
            .values_list("ui_language", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        return InterfaceLanguageChoices.ENGLISH
    return value if value in SUPPORTED_UI_LANGUAGES else InterfaceLanguageChoices.ENGLISH


def resolve_ui_language(request) -> str:
    active_language = getattr(request, "netbox_config_backup_language", None)
    if active_language in SUPPORTED_UI_LANGUAGES:
        return active_language
    session_language = getattr(request, "session", {}).get(SESSION_KEY)
    if session_language in SUPPORTED_UI_LANGUAGES:
        return session_language
    return configured_ui_language()
