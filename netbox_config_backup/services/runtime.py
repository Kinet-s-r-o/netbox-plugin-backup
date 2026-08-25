from django.conf import settings

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.storage.factory import build_config_storage

from .backup import BackupPipeline
from .connection_test import ConnectionTester
from .django_repository import DjangoBackupRepository


def build_backup_pipeline() -> BackupPipeline:
    plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
    return BackupPipeline(
        repository=DjangoBackupRepository(),
        drivers=driver_registry,
        storage=build_config_storage(plugin_settings),
        secret_providers=secret_provider_registry,
        error_message_max_length=plugin_settings["error_message_max_length"],
    )


def build_connection_tester() -> ConnectionTester:
    return ConnectionTester(
        repository=DjangoBackupRepository(),
        drivers=driver_registry,
        secret_providers=secret_provider_registry,
    )
