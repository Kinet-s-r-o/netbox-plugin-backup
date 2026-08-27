from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from netbox_config_backup.events import (
    BACKUP_FAILED,
    BACKUP_RECOVERED,
    BACKUP_STUCK,
    FTP_AUDIT_FAILED,
    FTP_AUDIT_RECOVERED,
    REPLICA_FAILED,
    REPLICA_RECOVERED,
    TARGET_STALE,
)


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    name: str
    description: str
    event_type: str
    model_name: str
    legacy_names: tuple[str, ...] = ()


RULES = (
    RuleDefinition(
        "Config Backup - Failed",
        "Notify when a configuration backup enters a failed state.",
        BACKUP_FAILED,
        "backuprun",
    ),
    RuleDefinition(
        "Config Backup - Recovered",
        "Notify after the first successful backup following a failure.",
        BACKUP_RECOVERED,
        "backuprun",
    ),
    RuleDefinition(
        "Config Backup - Stale target",
        "Notify when a target has no successful backup by its expected deadline.",
        TARGET_STALE,
        "backuptarget",
    ),
    RuleDefinition(
        "Config Backup - Stuck run",
        "Notify when an abandoned queued or running backup is reconciled.",
        BACKUP_STUCK,
        "backuprun",
    ),
    RuleDefinition(
        "Config Backup - External copy failed",
        "Notify when a configuration revision cannot be copied to an external destination.",
        REPLICA_FAILED,
        "revisionreplica",
    ),
    RuleDefinition(
        "Config Backup - External copy recovered",
        "Notify after an external destination succeeds following a failed copy.",
        REPLICA_RECOVERED,
        "revisionreplica",
    ),
    RuleDefinition(
        "Config Backup - Remote integrity audit failed",
        "Notify when an automatic remote-storage integrity audit finds missing or damaged copies.",
        FTP_AUDIT_FAILED,
        "backupdestination",
        ("Config Backup - FTP integrity audit failed",),
    ),
    RuleDefinition(
        "Config Backup - Remote integrity audit recovered",
        "Notify when a remote storage passes after a failed integrity audit.",
        FTP_AUDIT_RECOVERED,
        "backupdestination",
        ("Config Backup - FTP integrity audit recovered",),
    ),
)


class Command(BaseCommand):
    help = "Create the built-in Config Backup notification group and NetBox event rules."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--notification-group",
            default="Config Backup Notifications",
            help="Name of the NetBox in-app notification group to create or update.",
        )
        parser.add_argument(
            "--user",
            action="append",
            default=[],
            dest="users",
            help="Active NetBox username to add as a recipient; may be repeated.",
        )
        parser.add_argument(
            "--group",
            action="append",
            default=[],
            dest="groups",
            help="NetBox group name to add as a recipient; may be repeated.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create or update objects. Without this flag the command is read-only.",
        )

    def handle(self, *args, **options) -> None:
        from core.models import ObjectType
        from extras.models import EventRule, NotificationGroup
        from users.models import Group, User

        group_name = options["notification_group"].strip()
        usernames = tuple(dict.fromkeys(options["users"]))
        group_names = tuple(dict.fromkeys(options["groups"]))
        if not group_name:
            raise CommandError("Notification group name must not be empty.")
        if not usernames and not group_names:
            raise CommandError("Specify at least one --user or --group recipient.")

        users = list(User.objects.filter(username__in=usernames, is_active=True))
        found_users = {user.username for user in users}
        if set(usernames) - found_users:
            raise CommandError("One or more requested users do not exist or are inactive.")
        groups = list(Group.objects.filter(name__in=group_names))
        found_groups = {group.name for group in groups}
        if set(group_names) - found_groups:
            raise CommandError("One or more requested recipient groups do not exist.")

        if not options["apply"]:
            self.stdout.write(
                f"Dry-run: notification_group={group_name!r}, recipients="
                f"{len(users) + len(groups)}, event_rules={len(RULES)}."
            )
            self.stdout.write("No database records were changed.")
            return

        with transaction.atomic():
            notification_group, _created = NotificationGroup.objects.update_or_create(
                name=group_name,
                defaults={
                    "description": (
                        "In-app recipients for configuration backup failures, recovery, "
                        "stale targets, stuck runs, external copies, and remote integrity."
                    )
                },
            )
            notification_group.users.add(*users)
            notification_group.groups.add(*groups)
            action_type = ObjectType.objects.get_for_model(notification_group)
            for definition in RULES:
                object_type = ObjectType.objects.get(
                    app_label="netbox_config_backup",
                    model=definition.model_name,
                )
                # Preserve the identity of rules created by earlier releases
                # when their user-facing name was FTP-specific.
                if (
                    definition.legacy_names
                    and not EventRule.objects.filter(name=definition.name).exists()
                ):
                    legacy_rule = EventRule.objects.filter(
                        name__in=definition.legacy_names,
                        comments="Managed by config_backup_configure_notifications.",
                    ).first()
                    if legacy_rule is not None:
                        legacy_rule.name = definition.name
                        legacy_rule.save(update_fields=("name",))
                rule, _created = EventRule.objects.update_or_create(
                    name=definition.name,
                    defaults={
                        "description": definition.description,
                        "event_types": [definition.event_type],
                        "enabled": True,
                        "conditions": None,
                        "action_type": "notification",
                        "action_object_type": action_type,
                        "action_object_id": notification_group.pk,
                        "action_data": None,
                        "comments": "Managed by config_backup_configure_notifications.",
                    },
                )
                rule.full_clean(exclude=("object_types",))
                rule.save()
                rule.object_types.set((object_type,))

        self.stdout.write(
            self.style.SUCCESS(
                f"Configured notification_group={group_name!r}, recipients="
                f"{len(users) + len(groups)}, event_rules={len(RULES)}."
            )
        )
