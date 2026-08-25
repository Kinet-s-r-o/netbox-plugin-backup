"""Verify all eight in-app notification rules without persisting test data."""

from django.db import transaction
from extras.models import Notification
from users.models import User

from netbox_config_backup.events import (
    BACKUP_FAILED,
    BACKUP_RECOVERED,
    BACKUP_STUCK,
    FTP_AUDIT_FAILED,
    FTP_AUDIT_RECOVERED,
    REPLICA_FAILED,
    REPLICA_RECOVERED,
    TARGET_STALE,
    _emit_destination_event,
    _emit_replica_event,
    _emit_run_event,
    _emit_target_event,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    CredentialProfile,
    RevisionReplica,
)

admin = User.objects.get(username="admin")
runs = list(BackupRun.objects.order_by("pk")[:3])
target = BackupTarget.objects.order_by("pk").first()
revisions = list(ConfigRevision.objects.order_by("pk")[:2])
credential_profile = CredentialProfile.objects.order_by("pk").first()
assert len(runs) == 3 and target is not None
assert len(revisions) == 2 and credential_profile is not None

with transaction.atomic():
    destination = BackupDestination.objects.create(
        name="Config Backup notification smoke destination",
        enabled=False,
        auto_replicate=False,
        host="notification-smoke.invalid",
        credential_profile=credential_profile,
    )
    replicas = [
        RevisionReplica.objects.create(revision=revisions[0], destination=destination),
        RevisionReplica.objects.create(
            revision=revisions[1],
            destination=destination,
        ),
    ]
    before_ids = set(Notification.objects.filter(user=admin).values_list("pk", flat=True))
    _emit_run_event(BACKUP_FAILED, runs[0].pk)
    _emit_run_event(BACKUP_RECOVERED, runs[1].pk)
    _emit_run_event(BACKUP_STUCK, runs[2].pk)
    _emit_target_event(TARGET_STALE, target.pk)
    _emit_replica_event(REPLICA_FAILED, replicas[0].pk)
    _emit_replica_event(REPLICA_RECOVERED, replicas[1].pk)
    destination.last_integrity_audit_status = "problems"
    destination.last_integrity_audit_problem_count = 1
    destination.save()
    _emit_destination_event(FTP_AUDIT_FAILED, destination.pk)
    assert (
        Notification.objects.filter(
            user=admin,
            event_type=FTP_AUDIT_FAILED,
            object_id=destination.pk,
        )
        .exclude(pk__in=before_ids)
        .exists()
    )
    destination.last_integrity_audit_status = "healthy"
    destination.last_integrity_audit_problem_count = 0
    destination.save()
    _emit_destination_event(FTP_AUDIT_RECOVERED, destination.pk)
    created = Notification.objects.filter(user=admin).exclude(pk__in=before_ids)
    assert set(created.values_list("event_type", flat=True)) == {
        BACKUP_FAILED,
        BACKUP_RECOVERED,
        BACKUP_STUCK,
        TARGET_STALE,
        REPLICA_FAILED,
        REPLICA_RECOVERED,
        FTP_AUDIT_RECOVERED,
    }
    transaction.set_rollback(True)

assert set(Notification.objects.filter(user=admin).values_list("pk", flat=True)) == before_ids
print("NOTIFICATIONS_SMOKE_OK")
