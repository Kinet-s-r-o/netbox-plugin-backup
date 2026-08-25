from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from netbox_config_backup.models import ConfigArtifact
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.factory import build_config_storage
from netbox_config_backup.storage.local import LocalConfigStorage


class Command(BaseCommand):
    help = (
        "Verify and copy existing local configuration artifacts to the configured S3 backend. "
        "Local source files are never deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-root",
            help="Local storage root; defaults to the plugin storage_root setting.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Perform copies. Without this flag the command only verifies and plans.",
        )

    def handle(self, *args, **options):
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        source = LocalConfigStorage(options["source_root"] or plugin_settings["storage_root"])
        destination_settings = {**plugin_settings, "storage_backend": "s3"}
        try:
            destination = build_config_storage(destination_settings)
        except StorageError as exc:
            raise CommandError(str(exc)) from exc

        copied = 0
        pending = 0
        existing = 0
        missing = 0
        verified = 0
        artifacts = ConfigArtifact.objects.select_related("revision").order_by("pk")
        for artifact in artifacts.iterator(chunk_size=100):
            try:
                if not source.exists(artifact.storage_key):
                    missing += 1
                    continue
                content = source.get(artifact.storage_key)
                self._verify(content, artifact)
                verified += 1
                if destination.exists(artifact.storage_key):
                    self._verify(destination.get(artifact.storage_key), artifact)
                    existing += 1
                    continue
                if options["commit"]:
                    destination.put(
                        artifact.storage_key,
                        content,
                        metadata={
                            "artifact_type": artifact.artifact_type,
                            "driver_id": artifact.revision.driver_id,
                            "raw_hash": artifact.raw_hash,
                        },
                    )
                    self._verify(destination.get(artifact.storage_key), artifact)
                    copied += 1
                else:
                    pending += 1
            except StorageError as exc:
                raise CommandError(
                    f"Storage migration stopped safely at artifact ID {artifact.pk}."
                ) from exc
            except ValueError as exc:
                raise CommandError(
                    f"Artifact ID {artifact.pk} failed its integrity check; migration stopped."
                ) from exc

        mode = "migration" if options["commit"] else "dry run"
        self.stdout.write(
            self.style.SUCCESS(
                f"S3 {mode} complete: verified={verified}, pending={pending}, copied={copied}, "
                f"already_present={existing}, missing_local={missing}."
            )
        )
        if missing:
            raise CommandError(
                "Some database artifacts are missing from local storage; do not switch backends."
            )

    @staticmethod
    def _verify(content: bytes, artifact) -> None:
        if (
            len(content) != artifact.size
            or hashlib.sha256(content).hexdigest() != artifact.raw_hash
        ):
            raise ValueError("Artifact integrity check failed.")
