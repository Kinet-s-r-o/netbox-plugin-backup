from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from netbox_config_backup.credentials.encrypted_database import (
    DatabaseCredentialCipher,
    MasterKeyConfigurationError,
)
from netbox_config_backup.models import DownloadEncryptionSecret, StoredCredential


class Command(BaseCommand):
    help = (
        "Verify database-encrypted credentials and optionally re-encrypt them "
        "with the configured active master key."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the rotation. Without this flag the command is a read-only dry-run.",
        )
        parser.add_argument(
            "--expected-active-version",
            help=(
                "Required with --apply. Rotation stops unless this exactly matches the "
                "configured active key version."
            ),
        )

    def handle(self, *args, **options) -> None:
        apply_rotation = bool(options["apply"])
        expected_version = options.get("expected_active_version")
        cipher = DatabaseCredentialCipher()
        try:
            _active_key, active_version = cipher.active_key()
            configured_versions = set(cipher.configured_key_versions())
        except MasterKeyConfigurationError as exc:
            raise CommandError(str(exc)) from exc

        if apply_rotation and not expected_version:
            raise CommandError("--expected-active-version is required with --apply.")
        if expected_version and expected_version != active_version:
            raise CommandError("The expected active key version does not match configuration.")

        version_counts = Counter(StoredCredential.objects.values_list("key_version", flat=True))
        version_counts.update(
            DownloadEncryptionSecret.objects.values_list("key_version", flat=True)
        )
        missing_versions = set(version_counts) - configured_versions
        if missing_versions:
            raise CommandError(
                "One or more stored credentials require an unconfigured master key version."
            )

        if not apply_rotation:
            verified, pending = self._verify(cipher, active_version=active_version)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry-run successful: verified={verified}, pending_rotation={pending}, "
                    f"active_version={active_version}."
                )
            )
            self.stdout.write("No database records were changed.")
            return

        rotated, verified = self._rotate(cipher, active_version=active_version)
        self.stdout.write(
            self.style.SUCCESS(
                f"Rotation successful: rotated={rotated}, verified={verified}, "
                f"active_version={active_version}."
            )
        )

    @staticmethod
    def _decrypt(
        cipher: DatabaseCredentialCipher,
        stored: StoredCredential | DownloadEncryptionSecret,
    ) -> str:
        try:
            return cipher.decrypt(
                reference=stored.reference,
                ciphertext=bytes(stored.ciphertext),
                nonce=bytes(stored.nonce),
                key_version=stored.key_version,
            )
        except MasterKeyConfigurationError as exc:
            raise CommandError(
                "Encrypted secret verification failed; no secret material was displayed."
            ) from exc

    def _verify(self, cipher: DatabaseCredentialCipher, *, active_version: str) -> tuple[int, int]:
        verified = 0
        pending = 0
        queryset = StoredCredential.objects.only(
            "reference", "ciphertext", "nonce", "key_version"
        ).order_by("pk")
        for stored in queryset.iterator(chunk_size=200):
            self._decrypt(cipher, stored)
            verified += 1
            pending += stored.key_version != active_version
        download_secrets = DownloadEncryptionSecret.objects.only(
            "reference", "ciphertext", "nonce", "key_version"
        ).order_by("pk")
        for stored in download_secrets.iterator(chunk_size=200):
            self._decrypt(cipher, stored)
            verified += 1
            pending += stored.key_version != active_version
        return verified, pending

    def _rotate(self, cipher: DatabaseCredentialCipher, *, active_version: str) -> tuple[int, int]:
        rotated = 0
        now = timezone.now()
        with transaction.atomic():
            credentials = list(
                StoredCredential.objects.select_for_update()
                .only("reference", "ciphertext", "nonce", "key_version", "rotated_at")
                .order_by("pk")
            )
            download_secrets = list(
                DownloadEncryptionSecret.objects.select_for_update()
                .only("reference", "ciphertext", "nonce", "key_version", "rotated_at")
                .order_by("pk")
            )
            changed: list[StoredCredential] = []
            for stored in credentials:
                plaintext = self._decrypt(cipher, stored)
                if stored.key_version == active_version:
                    continue
                payload = cipher.encrypt(reference=stored.reference, plaintext=plaintext)
                stored.ciphertext = payload.ciphertext
                stored.nonce = payload.nonce
                stored.key_version = payload.key_version
                stored.rotated_at = now
                changed.append(stored)
            if changed:
                StoredCredential.objects.bulk_update(
                    changed,
                    fields=("ciphertext", "nonce", "key_version", "rotated_at"),
                    batch_size=200,
                )
            changed_download_secrets: list[DownloadEncryptionSecret] = []
            for stored in download_secrets:
                plaintext = self._decrypt(cipher, stored)
                if stored.key_version == active_version:
                    continue
                payload = cipher.encrypt(reference=stored.reference, plaintext=plaintext)
                stored.ciphertext = payload.ciphertext
                stored.nonce = payload.nonce
                stored.key_version = payload.key_version
                stored.rotated_at = now
                changed_download_secrets.append(stored)
            if changed_download_secrets:
                DownloadEncryptionSecret.objects.bulk_update(
                    changed_download_secrets,
                    fields=("ciphertext", "nonce", "key_version", "rotated_at"),
                    batch_size=200,
                )
            rotated = len(changed) + len(changed_download_secrets)
            verified, pending = self._verify(cipher, active_version=active_version)
            if pending:
                raise CommandError(
                    "Rotation verification found encrypted secrets on a previous key version."
                )
        return rotated, verified
