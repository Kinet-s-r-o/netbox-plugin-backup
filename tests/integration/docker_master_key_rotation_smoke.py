"""Exercise transactional master-key rotation against a real NetBox database."""

import base64
import io
import json
import os
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from netbox_config_backup.credentials.encrypted_database import DatabaseCredentialCipher
from netbox_config_backup.models import CredentialProfile, StoredCredential

old_key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
new_key = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
old_environment = {
    "NETBOX_CONFIG_BACKUP_MASTER_KEY": old_key,
    "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "rotation-old",
    "NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS": "{}",
}
rotating_environment = {
    "NETBOX_CONFIG_BACKUP_MASTER_KEY": new_key,
    "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "rotation-new",
    "NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS": json.dumps(
        {"rotation-old": old_key}, separators=(",", ":")
    ),
}

with transaction.atomic():
    credentials = list(StoredCredential.objects.order_by("pk"))
    if not credentials:
        profile = CredentialProfile.objects.create(
            name="rotation-smoke",
            provider_id="encrypted_database",
            secret_reference="db://temporary",
            auth_type="password",
        )
        credentials = [
            StoredCredential.objects.create(
                profile=profile,
                username="rotation-user",
                ciphertext=b"temporary",
                nonce=b"temporary",
                key_version="temporary",
            )
        ]

    with patch.dict(os.environ, old_environment, clear=False):
        old_cipher = DatabaseCredentialCipher()
        for index, stored in enumerate(credentials):
            payload = old_cipher.encrypt(
                reference=stored.reference,
                plaintext=f"rotation-secret-{index}",
            )
            stored.ciphertext = payload.ciphertext
            stored.nonce = payload.nonce
            stored.key_version = payload.key_version
        StoredCredential.objects.bulk_update(
            credentials, fields=("ciphertext", "nonce", "key_version")
        )

    with patch.dict(os.environ, rotating_environment, clear=False):
        dry_run_output = io.StringIO()
        call_command("config_backup_rotate_master_key", stdout=dry_run_output)
        output = dry_run_output.getvalue()
        assert f"verified={len(credentials)}" in output
        assert f"pending_rotation={len(credentials)}" in output
        assert "rotation-secret" not in output
        assert old_key not in output and new_key not in output
        assert not any(str(stored.reference) in output for stored in credentials)

        try:
            call_command(
                "config_backup_rotate_master_key",
                apply=True,
                expected_active_version="wrong-version",
                stdout=io.StringIO(),
            )
        except CommandError:
            pass
        else:
            raise AssertionError("Rotation accepted an incorrect expected active version.")

        apply_output = io.StringIO()
        call_command(
            "config_backup_rotate_master_key",
            apply=True,
            expected_active_version="rotation-new",
            stdout=apply_output,
        )
        assert f"rotated={len(credentials)}" in apply_output.getvalue()
        assert not StoredCredential.objects.exclude(key_version="rotation-new").exists()

        new_only_cipher = DatabaseCredentialCipher(
            {
                "NETBOX_CONFIG_BACKUP_MASTER_KEY": new_key,
                "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION": "rotation-new",
            }
        )
        for index, stored in enumerate(StoredCredential.objects.order_by("pk")):
            assert (
                new_only_cipher.decrypt(
                    reference=stored.reference,
                    ciphertext=bytes(stored.ciphertext),
                    nonce=bytes(stored.nonce),
                    key_version=stored.key_version,
                )
                == f"rotation-secret-{index}"
            )

    with patch.dict(os.environ, old_environment, clear=False):
        old_cipher = DatabaseCredentialCipher()
        credentials = list(StoredCredential.objects.order_by("pk"))
        for index, stored in enumerate(credentials):
            payload = old_cipher.encrypt(
                reference=stored.reference,
                plaintext=f"rollback-secret-{index}",
            )
            stored.ciphertext = payload.ciphertext
            stored.nonce = payload.nonce
            stored.key_version = payload.key_version
        credentials[-1].ciphertext = b"corrupt-authenticated-ciphertext"
        StoredCredential.objects.bulk_update(
            credentials, fields=("ciphertext", "nonce", "key_version")
        )
    before_failure = list(
        StoredCredential.objects.order_by("pk").values_list(
            "pk", "ciphertext", "nonce", "key_version", "rotated_at"
        )
    )
    with patch.dict(os.environ, rotating_environment, clear=False):
        try:
            call_command(
                "config_backup_rotate_master_key",
                apply=True,
                expected_active_version="rotation-new",
                stdout=io.StringIO(),
            )
        except CommandError as exc:
            assert "no secret material was displayed" in str(exc)
        else:
            raise AssertionError("Rotation accepted corrupt encrypted material.")
    after_failure = list(
        StoredCredential.objects.order_by("pk").values_list(
            "pk", "ciphertext", "nonce", "key_version", "rotated_at"
        )
    )
    assert after_failure == before_failure

    transaction.set_rollback(True)

print("MASTER_KEY_ROTATION_SMOKE_OK")
