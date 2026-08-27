from __future__ import annotations

from django.utils import translation

from netbox_config_backup.services.ui_language import resolve_ui_language


class ConfigBackupLanguageMiddleware:
    """Activate the configured language only for Config Backup pages."""

    path_prefix = "/plugins/config-backup/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith(self.path_prefix):
            return self.get_response(request)

        language = resolve_ui_language(request)
        request.netbox_config_backup_language = language
        with translation.override(language):
            response = self.get_response(request)
            response.headers["Content-Language"] = language
            return response
