from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.events import (
    EVENT_TYPE_KIND_DANGER,
    EVENT_TYPE_KIND_SUCCESS,
    EVENT_TYPE_KIND_WARNING,
    EventType,
    get_event_type,
)

BACKUP_FAILED = "config_backup_failed"
BACKUP_RECOVERED = "config_backup_recovered"
TARGET_STALE = "config_backup_target_stale"
BACKUP_STUCK = "config_backup_stuck"
REPLICA_FAILED = "config_backup_replica_failed"
REPLICA_RECOVERED = "config_backup_replica_recovered"
FTP_AUDIT_FAILED = "config_backup_ftp_audit_failed"
FTP_AUDIT_RECOVERED = "config_backup_ftp_audit_recovered"

logger = logging.getLogger("netbox_config_backup.events")


def register_event_types() -> None:
    """Register the plugin events once, including under Django's autoreloader."""

    definitions = (
        (BACKUP_FAILED, _("Configuration backup failed"), EVENT_TYPE_KIND_DANGER),
        (BACKUP_RECOVERED, _("Configuration backup recovered"), EVENT_TYPE_KIND_SUCCESS),
        (TARGET_STALE, _("Configuration backup target is stale"), EVENT_TYPE_KIND_WARNING),
        (BACKUP_STUCK, _("Configuration backup run is stuck"), EVENT_TYPE_KIND_DANGER),
        (REPLICA_FAILED, _("Configuration backup replica failed"), EVENT_TYPE_KIND_DANGER),
        (
            REPLICA_RECOVERED,
            _("Configuration backup replica recovered"),
            EVENT_TYPE_KIND_SUCCESS,
        ),
        (
            FTP_AUDIT_FAILED,
            _("Remote storage integrity audit found problems"),
            EVENT_TYPE_KIND_DANGER,
        ),
        (
            FTP_AUDIT_RECOVERED,
            _("Remote storage integrity audit recovered"),
            EVENT_TYPE_KIND_SUCCESS,
        ),
    )
    for name, text, kind in definitions:
        if get_event_type(name) is None:
            EventType(name=name, text=text, kind=kind).register()


def queue_run_event(event_type: str, run_id: int) -> None:
    """Emit after commit so notification failures cannot roll back backup state."""

    if not _events_enabled():
        return
    transaction.on_commit(
        lambda: _emit_run_event_safely(event_type, run_id),
        robust=True,
    )


def queue_target_event(event_type: str, target_id: int) -> None:
    if not _events_enabled():
        return
    transaction.on_commit(
        lambda: _emit_target_event_safely(event_type, target_id),
        robust=True,
    )


def queue_replica_event(event_type: str, replica_id: int) -> None:
    if not _events_enabled():
        return
    transaction.on_commit(
        lambda: _emit_replica_event_safely(event_type, replica_id),
        robust=True,
    )


def queue_destination_event(event_type: str, destination_id: int) -> None:
    if not _events_enabled():
        return
    transaction.on_commit(
        lambda: _emit_destination_event_safely(event_type, destination_id),
        robust=True,
    )


def _events_enabled() -> bool:
    from netbox_config_backup.services.runtime_controls import get_runtime_controls

    return get_runtime_controls().events_enabled


def _emit_run_event_safely(event_type: str, run_id: int) -> None:
    try:
        _emit_run_event(event_type, run_id)
    except Exception:
        logger.exception("Could not emit backup event %s for run %s.", event_type, run_id)


def _emit_target_event_safely(event_type: str, target_id: int) -> None:
    try:
        _emit_target_event(event_type, target_id)
    except Exception:
        logger.exception("Could not emit backup event %s for target %s.", event_type, target_id)


def _emit_replica_event_safely(event_type: str, replica_id: int) -> None:
    try:
        _emit_replica_event(event_type, replica_id)
    except Exception:
        logger.exception("Could not emit backup event %s for replica %s.", event_type, replica_id)


def _emit_destination_event_safely(event_type: str, destination_id: int) -> None:
    try:
        _emit_destination_event(event_type, destination_id)
    except Exception:
        logger.exception(
            "Could not emit backup event %s for destination %s.",
            event_type,
            destination_id,
        )


def _emit_run_event(event_type: str, run_id: int) -> None:
    from netbox_config_backup.models import BackupRun

    run = BackupRun.objects.select_related("target__device", "triggered_by").get(pk=run_id)
    data = {
        "id": run.pk,
        "display": str(run),
        "url": reverse("plugins:netbox_config_backup:backuprun", args=(run.pk,)),
        "run_id": run.pk,
        "target_id": run.target_id,
        "device_id": run.target.device_id,
        "device": str(run.target.device),
        "status": run.status,
        "source": run.source,
        "error_code": run.error_code,
        "consecutive_failures": run.target.consecutive_failures,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    _process_event_rules(event_type, run, data, user=run.triggered_by)


def _emit_target_event(event_type: str, target_id: int) -> None:
    from netbox_config_backup.models import BackupTarget

    target = BackupTarget.objects.select_related("device").get(pk=target_id)
    data = {
        "id": target.pk,
        "display": str(target),
        "url": reverse("plugins:netbox_config_backup:backuptarget", args=(target.pk,)),
        "target_id": target.pk,
        "device_id": target.device_id,
        "device": str(target.device),
        "status": target.status,
        "last_success_at": (target.last_success_at.isoformat() if target.last_success_at else None),
        "next_run_at": target.next_run_at.isoformat() if target.next_run_at else None,
    }
    _process_event_rules(event_type, target, data, user=None)


def _emit_replica_event(event_type: str, replica_id: int) -> None:
    from netbox_config_backup.models import RevisionReplica

    replica = RevisionReplica.objects.select_related("destination", "revision__target__device").get(
        pk=replica_id
    )
    data = {
        "id": replica.pk,
        "display": str(replica),
        "url": replica.destination.get_absolute_url(),
        "replica_id": replica.pk,
        "destination_id": replica.destination_id,
        "destination": replica.destination.name,
        "revision_id": replica.revision_id,
        "device_id": replica.revision.target.device_id,
        "device": str(replica.revision.target.device),
        "status": replica.status,
        "attempts": replica.attempts,
        "error_code": replica.error_code,
        "finished_at": replica.finished_at.isoformat() if replica.finished_at else None,
    }
    _process_event_rules(event_type, replica, data, user=None)


def _emit_destination_event(event_type: str, destination_id: int) -> None:
    from netbox_config_backup.models import BackupDestination

    destination = BackupDestination.objects.get(pk=destination_id)
    data = {
        "id": destination.pk,
        "display": str(destination),
        "url": destination.get_absolute_url(),
        "destination_id": destination.pk,
        "destination": destination.name,
        "status": destination.last_integrity_audit_status,
        "problem_count": destination.last_integrity_audit_problem_count,
        "last_audit_at": (
            destination.last_integrity_audit_at.isoformat()
            if destination.last_integrity_audit_at
            else None
        ),
        "next_audit_at": (
            destination.next_integrity_audit_at.isoformat()
            if destination.next_integrity_audit_at
            else None
        ),
    }
    _process_event_rules(event_type, destination, data, user=None)


def _process_event_rules(event_type: str, instance: Any, data: dict[str, Any], *, user) -> None:
    """Bridge background backup transitions into NetBox's native Event Rules."""

    from core.models import ObjectType
    from extras.events import EventContext, process_event_rules
    from extras.models import EventRule

    object_type = ObjectType.objects.get_for_model(instance)
    event_rules = EventRule.objects.filter(
        event_types__contains=[event_type],
        object_types=object_type,
        enabled=True,
    )
    if not event_rules.exists():
        return
    process_event_rules(
        event_rules=event_rules,
        object_type=object_type,
        event=EventContext(event_type=event_type, data=data, user=user),
    )
