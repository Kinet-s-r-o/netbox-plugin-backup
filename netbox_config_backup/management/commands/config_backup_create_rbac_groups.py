from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from users.models import Group, ObjectPermission

from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    OperationalSettings,
    PlatformMapping,
    RetentionPolicy,
    RevisionReplica,
    SftpReceiverProfile,
    SSHHostKey,
    StoredCredential,
)

READ_MODELS = (BackupTarget, BackupRun, ConfigRevision, ConfigArtifact, RevisionReplica)
CONFIG_MODELS = (
    BackupPolicy,
    RetentionPolicy,
    ConnectionProfile,
    CredentialProfile,
    SftpReceiverProfile,
    PlatformMapping,
    OperationalSettings,
    SSHHostKey,
    BackupDestination,
)
ALL_PLUGIN_MODELS = (*READ_MODELS, *CONFIG_MODELS, StoredCredential)


class Command(BaseCommand):
    help = "Create or update least-privilege Config Backup reader/operator/admin groups."

    @transaction.atomic
    def handle(self, *args, **options):
        readers, _ = Group.objects.update_or_create(
            name="Config Backup Readers",
            defaults={"description": "Read backup status, revisions, redacted content, and diffs."},
        )
        operators, _ = Group.objects.update_or_create(
            name="Config Backup Operators",
            defaults={
                "description": "Read plugin configuration, run/test backups, and manage schedules."
            },
        )
        administrators, _ = Group.objects.update_or_create(
            name="Config Backup Administrators",
            defaults={"description": "Full administration of the Config Backup plugin."},
        )

        reader_view = self._permission(
            "Config Backup - reader view",
            actions=("view",),
            models=READ_MODELS,
        )
        operator_view = self._permission(
            "Config Backup - operator view",
            actions=("view",),
            models=ALL_PLUGIN_MODELS,
        )
        operator_run = self._permission(
            "Config Backup - operator run and test",
            actions=("add",),
            models=(BackupRun,),
        )
        operator_change = self._permission(
            "Config Backup - operator scheduling and protection",
            actions=("change",),
            models=(BackupTarget, ConfigRevision),
        )
        administrator_manage = self._permission(
            "Config Backup - administrator manage",
            actions=("view", "add", "change", "delete"),
            models=ALL_PLUGIN_MODELS,
        )

        readers.object_permissions.set((reader_view,))
        operators.object_permissions.set(
            (reader_view, operator_view, operator_run, operator_change)
        )
        administrators.object_permissions.set((administrator_manage,))

        self.stdout.write(
            self.style.SUCCESS(
                "Config Backup RBAC groups are ready. No users were assigned automatically."
            )
        )

    @staticmethod
    def _permission(name: str, *, actions: tuple[str, ...], models: tuple[type, ...]):
        permission, _ = ObjectPermission.objects.update_or_create(
            name=name,
            defaults={
                "description": "Managed by config_backup_create_rbac_groups.",
                "enabled": True,
                "actions": list(actions),
                "constraints": None,
            },
        )
        permission.object_types.set(ContentType.objects.get_for_models(*models).values())
        return permission
