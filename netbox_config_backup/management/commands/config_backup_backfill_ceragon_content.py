from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from netbox_config_backup.choices import RunStatusChoices
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import CollectedArtifact
from netbox_config_backup.models import BackupRun, BackupTarget, ConfigArtifact, ConfigRevision
from netbox_config_backup.services.revision_display import (
    RevisionDisplayError,
    load_artifact_content,
)
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.factory import build_config_storage


class Command(BaseCommand):
    help = (
        "Extract Ceragon IP-50 config_dump.txt files into primary text artifacts. "
        "The command is a dry run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write extracted artifacts and update revision hashes.",
        )
        parser.add_argument(
            "--revision-id",
            type=int,
            help="Limit processing to one ConfigRevision ID.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        storage = build_config_storage(plugin_settings)
        driver = driver_registry.create("ceragon_ip50")

        queryset = ConfigRevision.objects.filter(driver_id="ceragon_ip50").prefetch_related(
            "artifacts"
        )
        if options["revision_id"]:
            queryset = queryset.filter(pk=options["revision_id"])

        planned = 0
        migrated = 0
        existing = 0
        affected_target_ids: set[int] = set()

        for revision in queryset.order_by("target_id", "created", "pk"):
            current = next(
                (
                    item
                    for item in revision.artifacts.all()
                    if item.artifact_type == "configuration_dump"
                ),
                None,
            )
            if current is not None:
                try:
                    load_artifact_content(current, storage=storage)
                except RevisionDisplayError as exc:
                    raise CommandError(
                        f"Revision {revision.pk} has an invalid extracted artifact."
                    ) from exc
                existing += 1
                continue

            native = next(
                (
                    item
                    for item in revision.artifacts.all()
                    if item.artifact_type == "native_backup"
                ),
                None,
            )
            if native is None:
                raise CommandError(f"Revision {revision.pk} has no native Ceragon backup artifact.")
            try:
                archive = load_artifact_content(native, storage=storage)
                configuration = driver.extract_configuration(archive)
            except (RevisionDisplayError, ValueError) as exc:
                raise CommandError(
                    f"Revision {revision.pk} could not be extracted safely."
                ) from exc

            collected = CollectedArtifact(
                artifact_type="configuration_dump",
                filename=driver.configuration_filename,
                content=configuration,
                format=driver.configuration_format,
                is_primary=True,
            )
            validation = driver.validate(collected)
            if not validation.valid:
                raise CommandError(f"Revision {revision.pk} failed configuration validation.")
            raw_hash = hashlib.sha256(configuration).hexdigest()
            normalized_hash = hashlib.sha256(driver.normalize(collected)).hexdigest()
            key = str(PurePosixPath(native.storage_key).parent / driver.configuration_filename)
            planned += 1
            affected_target_ids.add(revision.target_id)

            if not apply_changes:
                continue

            created_storage = False
            try:
                if storage.exists(key):
                    stored = storage.get(key)
                    if (
                        len(stored) != len(configuration)
                        or hashlib.sha256(stored).hexdigest() != raw_hash
                    ):
                        raise CommandError(
                            f"Revision {revision.pk} has a conflicting storage object."
                        )
                else:
                    storage.put(
                        key,
                        configuration,
                        metadata={
                            "artifact_type": "configuration_dump",
                            "driver_id": "ceragon_ip50",
                            "raw_hash": raw_hash,
                        },
                    )
                    created_storage = True

                with transaction.atomic():
                    locked = ConfigRevision.objects.select_for_update().get(pk=revision.pk)
                    if ConfigArtifact.objects.filter(
                        revision=locked,
                        artifact_type="configuration_dump",
                    ).exists():
                        existing += 1
                        if created_storage:
                            storage.delete(key)
                        continue
                    ConfigArtifact.objects.filter(
                        revision=locked,
                        is_primary=True,
                    ).update(is_primary=False)
                    ConfigArtifact.objects.create(
                        revision=locked,
                        artifact_type="configuration_dump",
                        format=driver.configuration_format,
                        storage_key=key,
                        size=len(configuration),
                        raw_hash=raw_hash,
                        normalized_hash=normalized_hash,
                        is_primary=True,
                    )
                    locked.normalized_hash = normalized_hash
                    locked.normalizer_version = driver.normalizer_version
                    locked.save(update_fields=("normalized_hash", "normalizer_version"))
                migrated += 1
            except (StorageError, CommandError):
                if created_storage:
                    try:
                        storage.delete(key)
                    except StorageError:
                        pass
                raise
            except Exception as exc:
                if created_storage:
                    try:
                        storage.delete(key)
                    except StorageError:
                        pass
                raise CommandError(
                    f"Revision {revision.pk} backfill failed and was rolled back."
                ) from exc

        if apply_changes:
            for target_id in affected_target_ids:
                self._recompute_change_flags(target_id)

        mode = "apply" if apply_changes else "dry run"
        self.stdout.write(
            self.style.SUCCESS(
                f"Ceragon content backfill {mode} complete: planned={planned}, "
                f"migrated={migrated}, already_present={existing}."
            )
        )

    @staticmethod
    @transaction.atomic
    def _recompute_change_flags(target_id: int) -> None:
        revisions = list(
            ConfigRevision.objects.select_for_update()
            .filter(target_id=target_id, driver_id="ceragon_ip50")
            .order_by("created", "pk")
        )
        previous_hash = None
        last_changed_at = None
        for revision in revisions:
            changed = previous_hash is None or revision.normalized_hash != previous_hash
            if revision.content_changed != changed:
                revision.content_changed = changed
                revision.save(update_fields=("content_changed",))
            if changed:
                last_changed_at = revision.created
            else:
                BackupRun.objects.filter(
                    revision=revision,
                    status=RunStatusChoices.SUCCESS_CHANGED,
                ).update(
                    status=RunStatusChoices.SUCCESS_UNCHANGED,
                    changed=False,
                )
            previous_hash = revision.normalized_hash

        if last_changed_at is not None:
            BackupTarget.objects.filter(pk=target_id).update(last_change_at=last_changed_at)
