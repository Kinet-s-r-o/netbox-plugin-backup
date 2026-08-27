"""Verify TOFU and replacement-key lifecycle without changing persistent data."""

import atexit
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction

from netbox_config_backup.models import BackupTarget, SSHHostKey
from netbox_config_backup.services.ssh_host_keys import (
    trust_first_seen_host_key,
    trust_host_key,
)

_smoke_transaction = transaction.atomic()
_smoke_transaction.__enter__()


def _rollback_smoke_transaction():
    connection = transaction.get_connection()
    if connection.in_atomic_block:
        transaction.set_rollback(True)
        _smoke_transaction.__exit__(None, None, None)


atexit.register(_rollback_smoke_transaction)

target = BackupTarget.objects.first()
user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert target is not None
assert user is not None

suffix = uuid4().hex
address = f"tofu-{suffix[:12]}.invalid"
first = SSHHostKey.objects.create(
    target=target,
    address=address,
    port=65022,
    key_type="ssh-rsa",
    public_key="QUFBQQ==",
    fingerprint_sha256=f"SHA256:first-{suffix}",
)
first = trust_first_seen_host_key(first.pk)
assert first.status == "trusted"
assert first.approved_by_id is None

replacement = SSHHostKey.objects.create(
    target=target,
    address=address,
    port=65022,
    key_type="ssh-ed25519",
    public_key="QkJCQg==",
    fingerprint_sha256=f"SHA256:replacement-{suffix}",
)
replacement = trust_first_seen_host_key(replacement.pk)
assert replacement.status == "pending", "TOFU must never accept a later endpoint identity."

replacement = trust_host_key(replacement.pk, user=user)
first.refresh_from_db()
assert replacement.status == "trusted"
assert replacement.approved_by_id == user.pk
assert first.status == "rejected"
assert first.rejected_at is not None

print(
    json.dumps(
        {
            "first_key": "automatically trusted",
            "replacement_before_approval": "pending",
            "replacement_after_approval": "trusted",
            "old_key_after_approval": first.status,
        },
        sort_keys=True,
    )
)
