"""Run through ``manage.py shell`` against the Docker integration database."""

from core.models import ObjectType
from extras.models import EventRule, Notification, NotificationGroup
from users.models import User

from netbox_config_backup.events import (
    BACKUP_FAILED,
    TARGET_STALE,
    _emit_run_event,
    _emit_target_event,
)
from netbox_config_backup.models import BackupRun, BackupTarget

prefix = "Config Backup observability smoke"
user = User.objects.filter(is_superuser=True, is_active=True).first()
run = BackupRun.objects.select_related("target").first()
target = BackupTarget.objects.first()
assert user is not None
assert run is not None
assert target is not None

group = NotificationGroup.objects.create(name=prefix)
group.users.add(user)
created_rules = []
try:
    cases = (
        ("run", BackupRun, BACKUP_FAILED, run.pk, _emit_run_event),
        ("target", BackupTarget, TARGET_STALE, target.pk, _emit_target_event),
    )
    for suffix, model, event_type, object_id, emitter in cases:
        object_type = ObjectType.objects.get_for_model(model)
        rule = EventRule(
            name=f"{prefix} {suffix}",
            event_types=[event_type],
            enabled=True,
            conditions=None,
            action_type="notification",
            action_object=group,
            action_data={},
        )
        rule.full_clean()
        rule.save()
        rule.object_types.add(object_type)
        created_rules.append(rule)

        before = Notification.objects.filter(
            user=user,
            object_type=object_type,
            object_id=object_id,
            event_type=event_type,
        ).count()
        emitter(event_type, object_id)
        after = Notification.objects.filter(
            user=user,
            object_type=object_type,
            object_id=object_id,
            event_type=event_type,
        ).count()
        assert after == before + 1, (suffix, before, after)
        Notification.objects.filter(
            user=user,
            object_type=object_type,
            object_id=object_id,
            event_type=event_type,
        ).delete()

    print("OBSERVABILITY_SMOKE_OK")
finally:
    for rule in created_rules:
        rule.delete()
    group.delete()
