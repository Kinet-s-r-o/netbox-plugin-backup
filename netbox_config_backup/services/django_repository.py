from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction

from netbox_config_backup.choices import (
    RunStatusChoices,
    SSHHostKeyStatusChoices,
    TargetStatusChoices,
)
from netbox_config_backup.drivers.base import ConnectionParameters, ReceiverParameters
from netbox_config_backup.models import (
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    PlatformMapping,
    SSHHostKey,
)

from .repository import (
    ExecutionContext,
    RevisionSnapshot,
    StoredArtifactRecord,
)


class DjangoBackupRepository:
    def get_execution_context(self, run_id: Any) -> ExecutionContext:
        run = BackupRun.objects.only("pk", "target_id").get(pk=run_id)
        return replace(self.get_target_execution_context(run.target_id), run_id=run.pk)

    def get_target_execution_context(self, target_id: Any) -> ExecutionContext:
        target = BackupTarget.objects.select_related(
            "device__primary_ip4",
            "device__primary_ip6",
            "device__oob_ip",
            "policy_override",
            "credential_override",
            "connection_override",
            "receiver_override__credential_profile",
        ).get(pk=target_id)
        device = target.device
        mapping = (
            PlatformMapping.objects.select_related(
                "credential_profile", "connection_profile", "receiver_profile__credential_profile"
            )
            .filter(platform_id=device.platform_id, enabled=True)
            .first()
        )
        driver_id = target.driver_override or (mapping.driver_id if mapping else "")
        credential = target.credential_override or (mapping.credential_profile if mapping else None)
        connection = target.connection_override or (mapping.connection_profile if mapping else None)
        receiver = target.receiver_override or (mapping.receiver_profile if mapping else None)
        policy = target.policy_override
        address_object = self._select_address(device, connection)
        address = str(address_object.address.ip) if address_object else None

        connect_timeout = (
            connection.connect_timeout
            if connection
            else (policy.connection_timeout if policy else 15)
        )
        command_timeout = (
            connection.command_timeout if connection else (policy.command_timeout if policy else 60)
        )
        trusted_host_keys = tuple(
            SSHHostKey.objects.filter(
                target=target,
                address=address,
                port=connection.port if connection else 22,
                status=SSHHostKeyStatusChoices.TRUSTED,
            ).values_list("address", "port", "key_type", "public_key")
        )

        return ExecutionContext(
            run_id=None,
            target_id=target.pk,
            device_id=device.pk,
            device_name=device.name,
            driver_id=driver_id,
            address=address,
            connection=ConnectionParameters(
                protocol=connection.protocol if connection else "auto",
                port=connection.port if connection else 22,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                keepalive=connection.keepalive if connection else 30,
                verify_host_key=connection.verify_host_key if connection else True,
                auto_trust_first_host_key=(
                    connection.auto_trust_first_host_key if connection else False
                ),
                known_hosts_path=connection.known_hosts_path if connection else "",
                trusted_host_keys=tuple(
                    f"{host if port == 22 else f'[{host}]:{port}'} {key_type} {public_key}"
                    for host, port, key_type, public_key in trusted_host_keys
                ),
            ),
            driver_options={
                **(
                    dict(mapping.driver_options)
                    if mapping and mapping.driver_id == driver_id
                    else {}
                ),
                **dict(target.driver_options_override),
            },
            store_mode=policy.store_mode if policy else "changed_only",
            secret_provider_id=credential.provider_id if credential else None,
            secret_reference=credential.secret_reference if credential else None,
            receiver=(
                ReceiverParameters(
                    profile_id=receiver.pk,
                    protocol=receiver.protocol,
                    mode=receiver.mode,
                    advertised_host=receiver.advertised_host,
                    advertised_port=receiver.advertised_port,
                    bridge_host=receiver.bridge_host,
                    bridge_port=receiver.bridge_port,
                    remote_bind_host=receiver.remote_bind_host,
                    remote_bind_port=receiver.remote_bind_port,
                    upload_directory=receiver.upload_directory,
                    inbox_path=str(
                        self._receiver_inbox_path(
                            settings.PLUGINS_CONFIG["netbox_config_backup"]["receiver_root"],
                            receiver.pk,
                            receiver.upload_directory,
                        )
                    ),
                    export_timeout=receiver.export_timeout,
                    max_upload_bytes=receiver.max_upload_size,
                    passive_port_start=receiver.passive_port_start,
                    passive_port_end=receiver.passive_port_end,
                )
                if receiver
                else None
            ),
            receiver_secret_provider_id=(
                receiver.credential_profile.provider_id if receiver else None
            ),
            receiver_secret_reference=(
                receiver.credential_profile.secret_reference if receiver else None
            ),
        )

    @staticmethod
    def _receiver_inbox_path(root, profile_id, upload_directory):
        from netbox_config_backup.receiver.paths import receiver_inbox_path

        return receiver_inbox_path(root, profile_id, upload_directory)

    @staticmethod
    def _select_address(device, connection):
        preference = connection.address_preference if connection else "primary4_first"
        candidates = {
            "oob_first": (device.oob_ip, device.primary_ip4, device.primary_ip6),
            "primary4_first": (device.primary_ip4, device.primary_ip6, device.oob_ip),
            "primary6_first": (device.primary_ip6, device.primary_ip4, device.oob_ip),
        }
        return next((address for address in candidates.get(preference, ()) if address), None)

    @transaction.atomic
    def mark_running(self, run_id: Any, *, started_at: datetime) -> None:
        run = BackupRun.objects.select_for_update().get(pk=run_id)
        if run.status != RunStatusChoices.QUEUED:
            raise ValueError(f"Run {run_id} is not queued.")
        run.status = RunStatusChoices.RUNNING
        run.started_at = started_at
        run.error_code = ""
        run.error_message = ""
        run.save(
            update_fields=(
                "status",
                "started_at",
                "error_code",
                "error_message",
                "last_updated",
            )
        )

    def get_latest_revision(self, target_id: int) -> RevisionSnapshot | None:
        revision = (
            ConfigRevision.objects.filter(target_id=target_id)
            .prefetch_related("artifacts")
            .order_by("-created")
            .first()
        )
        if revision is None:
            return None
        primary = next((item for item in revision.artifacts.all() if item.is_primary), None)
        if primary is None:
            raise ValueError(f"Revision {revision.pk} has no primary artifact.")
        return RevisionSnapshot(
            revision_id=revision.pk,
            normalized_hash=revision.normalized_hash,
            primary_raw_hash=primary.raw_hash,
        )

    @transaction.atomic
    def commit_unchanged(
        self,
        run_id: Any,
        *,
        raw_changed: bool,
        finished_at: datetime,
    ) -> Any:
        run = BackupRun.objects.select_for_update().select_related("target").get(pk=run_id)
        target = (
            BackupTarget.objects.select_for_update(of=("self",))
            .select_related("policy_override", "device__site")
            .get(pk=run.target_id)
        )
        latest = ConfigRevision.objects.filter(target=target).order_by("-created").first()
        was_unhealthy = self._target_was_unhealthy(target)
        run.status = RunStatusChoices.SUCCESS_UNCHANGED
        run.finished_at = finished_at
        run.changed = False
        run.raw_changed = raw_changed
        run.revision = latest
        run.save()
        self._mark_target_success(target, latest, finished_at, content_changed=False)
        if was_unhealthy:
            from netbox_config_backup.events import BACKUP_RECOVERED, queue_run_event

            queue_run_event(BACKUP_RECOVERED, run.pk)
        if latest is not None:
            transaction.on_commit(
                lambda revision_id=latest.pk: self._ensure_revision_replicas(revision_id),
                robust=True,
            )
        return latest.pk if latest else None

    @transaction.atomic
    def commit_revision(
        self,
        run_id: Any,
        *,
        revision_uuid: UUID,
        normalized_hash: str,
        normalizer_version: str,
        driver_id: str,
        content_changed: bool,
        raw_changed: bool,
        artifacts: list[StoredArtifactRecord],
        finished_at: datetime,
    ) -> Any:
        run = BackupRun.objects.select_for_update().get(pk=run_id)
        target = BackupTarget.objects.select_for_update().get(pk=run.target_id)
        previous = ConfigRevision.objects.filter(target=target).order_by("-created").first()
        was_unhealthy = self._target_was_unhealthy(target)
        revision = ConfigRevision.objects.create(
            target=target,
            revision_uuid=revision_uuid,
            normalized_hash=normalized_hash,
            normalizer_version=normalizer_version,
            driver_id=driver_id,
            content_changed=content_changed,
            previous_revision=previous,
        )
        ConfigArtifact.objects.bulk_create(
            [
                ConfigArtifact(
                    revision=revision,
                    artifact_type=item.artifact_type,
                    format=item.format,
                    storage_key=item.storage_key,
                    size=item.size,
                    raw_hash=item.raw_hash,
                    normalized_hash=item.normalized_hash,
                    is_primary=item.is_primary,
                )
                for item in artifacts
            ]
        )
        run.status = (
            RunStatusChoices.SUCCESS_CHANGED
            if content_changed
            else RunStatusChoices.SUCCESS_UNCHANGED
        )
        run.finished_at = finished_at
        run.changed = content_changed
        run.raw_changed = raw_changed
        run.revision = revision
        run.save()
        self._mark_target_success(target, revision, finished_at, content_changed)
        if was_unhealthy:
            from netbox_config_backup.events import BACKUP_RECOVERED, queue_run_event

            queue_run_event(BACKUP_RECOVERED, run.pk)
        transaction.on_commit(
            lambda revision_id=revision.pk: self._queue_revision_replicas(revision_id),
            robust=True,
        )
        return revision.pk

    @staticmethod
    def _queue_revision_replicas(revision_id: int) -> None:
        from .replication import create_revision_replicas

        create_revision_replicas(revision_id)

    @staticmethod
    def _ensure_revision_replicas(revision_id: int) -> None:
        from .replication import ensure_revision_replicas

        ensure_revision_replicas(revision_id)

    @transaction.atomic
    def mark_failed(
        self,
        run_id: Any,
        *,
        status: str,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None:
        run = BackupRun.objects.select_for_update().get(pk=run_id)
        target = BackupTarget.objects.select_for_update().get(pk=run.target_id)
        first_failure = target.consecutive_failures == 0
        run.status = status
        run.finished_at = finished_at
        run.error_code = error_code
        run.error_message = error_message
        run.save()
        target.status = TargetStatusChoices.FAILED
        target.last_attempt_at = finished_at
        target.consecutive_failures += 1
        from .dispatcher import failure_next_run

        target.next_run_at = failure_next_run(
            target,
            failed_at=finished_at,
            failures=target.consecutive_failures,
        )
        target.save(
            update_fields=(
                "status",
                "last_attempt_at",
                "consecutive_failures",
                "next_run_at",
                "last_updated",
            )
        )
        from .runtime_controls import get_runtime_controls

        notify_every_failure = get_runtime_controls().notify_on_every_failure
        if first_failure or notify_every_failure:
            from netbox_config_backup.events import (
                BACKUP_FAILED,
                BACKUP_STUCK,
                queue_run_event,
            )

            event_type = BACKUP_STUCK if error_code == "STALE_RUN" else BACKUP_FAILED
            queue_run_event(event_type, run.pk)

    @staticmethod
    def _mark_target_success(
        target: BackupTarget,
        revision: ConfigRevision | None,
        finished_at: datetime,
        content_changed: bool,
    ) -> None:
        target.status = TargetStatusChoices.HEALTHY
        target.last_attempt_at = finished_at
        target.last_success_at = finished_at
        target.consecutive_failures = 0
        if revision is not None:
            target.last_revision = revision
        if content_changed:
            target.last_change_at = finished_at
        if target.next_run_at is None:
            from .scheduling import calculate_target_next_run

            target.next_run_at = calculate_target_next_run(target, now=finished_at)
        target.save()

    @staticmethod
    def _target_was_unhealthy(target: BackupTarget) -> bool:
        return target.consecutive_failures > 0 or target.status in {
            TargetStatusChoices.FAILED,
            TargetStatusChoices.STALE,
        }
