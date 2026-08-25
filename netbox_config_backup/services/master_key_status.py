from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from netbox_config_backup.credentials.encrypted_database import (
    DatabaseCredentialCipher,
    MasterKeyConfigurationError,
)


@dataclass(frozen=True, slots=True)
class MasterKeyStatus:
    state: str
    active_version: str = ""
    configured_previous_keys: int = 0
    credential_count: int = 0
    active_credential_count: int = 0
    pending_credential_count: int = 0
    unavailable_credential_count: int = 0


def get_master_key_status() -> MasterKeyStatus:
    from netbox_config_backup.models import StoredCredential

    counts = Counter(StoredCredential.objects.values_list("key_version", flat=True))
    total = sum(counts.values())
    cipher = DatabaseCredentialCipher()
    try:
        _key, active_version = cipher.active_key()
        configured_versions = set(cipher.configured_key_versions())
    except MasterKeyConfigurationError:
        return MasterKeyStatus(state="invalid", credential_count=total)

    unavailable = sum(
        count for version, count in counts.items() if version not in configured_versions
    )
    active_count = counts.get(active_version, 0)
    pending = total - active_count
    state = "unavailable" if unavailable else "pending" if pending else "ready"
    return MasterKeyStatus(
        state=state,
        active_version=active_version,
        configured_previous_keys=len(configured_versions) - 1,
        credential_count=total,
        active_credential_count=active_count,
        pending_credential_count=pending,
        unavailable_credential_count=unavailable,
    )
