from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from math import ceil
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from netbox_config_backup.choices import ConnectionProtocolChoices
from netbox_config_backup.credentials.encrypted_database import (
    DatabaseCredentialCipher,
    EncryptedDatabaseSecretProvider,
)
from netbox_config_backup.models import (
    BackupPolicy,
    BackupTarget,
    ConnectionProfile,
    CredentialProfile,
    RetentionPolicy,
    SftpReceiverProfile,
    StoredCredential,
)
from netbox_config_backup.services.scheduling import apply_target_schedule


@dataclass(frozen=True, slots=True)
class QuickSetupResult:
    target: BackupTarget
    connection_profile: ConnectionProfile
    credential_profile: CredentialProfile
    backup_policy: BackupPolicy
    retention_policy: RetentionPolicy


def _object_name(*, device, suffix: str) -> str:
    tail = f" ({device.pk}) {suffix}"
    prefix = f"[Quick] {device.name or 'device'}"
    return f"{prefix[: 100 - len(tail)]}{tail}"


def _available_name(model, *, device, suffix: str) -> str:
    base = _object_name(device=device, suffix=suffix)
    if not model.objects.filter(name=base).exists():
        return base
    for index in range(2, 1000):
        numbered_suffix = f" #{index}"
        candidate = f"{base[: 100 - len(numbered_suffix)]}{numbered_suffix}"
        if not model.objects.filter(name=candidate).exists():
            return candidate
    raise RuntimeError("Could not allocate a unique quick-setup profile name.")


def _retention_policy(days: int) -> RetentionPolicy:
    policy, _created = RetentionPolicy.objects.get_or_create(
        name=f"[Quick] Retain {days} days",
        defaults={
            "keep_all_days": min(days, 7),
            "daily_days": days,
            "weekly_weeks": ceil(days / 7),
            "monthly_months": ceil(days / 30),
            "minimum_changed_revisions": 10,
            "unchanged_run_days": days,
            "changed_run_days": days,
            "failed_run_days": days,
        },
    )
    return policy


def _backup_policy(schedule: str, retention: RetentionPolicy) -> BackupPolicy:
    schedule_defaults = {
        "6h": {"schedule_type": "interval", "interval_minutes": 360, "time_of_day": None},
        "12h": {"schedule_type": "interval", "interval_minutes": 720, "time_of_day": None},
        "daily": {
            "schedule_type": "daily",
            "interval_minutes": None,
            "time_of_day": time(2, 0),
        },
    }
    labels = {"6h": "Every 6 hours", "12h": "Every 12 hours", "daily": "Daily at 02:00"}
    policy, _created = BackupPolicy.objects.get_or_create(
        name=f"[Quick] {labels[schedule]} / {retention.daily_days} days",
        defaults={
            "enabled": True,
            **schedule_defaults[schedule],
            "timezone_mode": "site",
            "jitter_minutes": 15,
            "connection_timeout": 15,
            "command_timeout": 60,
            "max_retries": 3,
            "retry_backoff_minutes": [5, 15, 60],
            "store_mode": "changed_only",
            "retention_policy": retention,
        },
    )
    return policy


@transaction.atomic
def create_quick_setup(
    *,
    device,
    driver_id: str,
    connection_profile: ConnectionProfile | None = None,
    credential_profile: CredentialProfile | None = None,
    receiver_profile: SftpReceiverProfile | None = None,
    allow_device_export: bool = False,
    sync_receiver_credentials: bool = False,
    restore_point: str = "restore-point-1",
    port: int,
    verify_host_key: bool,
    username: str,
    password: str,
    schedule: str,
    retention_days: int,
    protocol: str = ConnectionProtocolChoices.AUTOMATIC,
) -> QuickSetupResult:
    """Create one complete target configuration as an atomic operation."""
    retention = _retention_policy(retention_days)
    policy = _backup_policy(schedule, retention)

    connection = connection_profile
    if connection is None:
        resolved_protocol = protocol
        if resolved_protocol == ConnectionProtocolChoices.AUTOMATIC:
            if driver_id == "siae_smos_cli" or port == 23:
                resolved_protocol = ConnectionProtocolChoices.TELNET
            elif driver_id == "siae_smos_ssh" or port == 22:
                resolved_protocol = ConnectionProtocolChoices.SSH
        profile_suffix = (
            "Telnet" if resolved_protocol == ConnectionProtocolChoices.TELNET else "SSH"
        )
        connection = ConnectionProfile.objects.create(
            name=_available_name(ConnectionProfile, device=device, suffix=profile_suffix),
            protocol=resolved_protocol,
            address_preference="oob_first",
            port=port,
            connect_timeout=15,
            command_timeout=60,
            keepalive=30,
            verify_host_key=(
                verify_host_key if resolved_protocol != ConnectionProtocolChoices.TELNET else False
            ),
            known_hosts_path=(
                "/etc/netbox-config-backup/ssh/known_hosts"
                if verify_host_key and resolved_protocol != ConnectionProtocolChoices.TELNET
                else ""
            ),
        )

    credential = credential_profile
    if credential is None:
        reference = uuid4()
        payload = DatabaseCredentialCipher().encrypt(reference=reference, plaintext=password)
        credential = CredentialProfile.objects.create(
            name=_available_name(CredentialProfile, device=device, suffix="credentials"),
            provider_id="encrypted_database",
            secret_reference=EncryptedDatabaseSecretProvider.format_reference(reference),
            auth_type="password",
        )
        StoredCredential.objects.create(
            profile=credential,
            reference=reference,
            username=username,
            ciphertext=payload.ciphertext,
            nonce=payload.nonce,
            key_version=payload.key_version,
            rotated_at=timezone.now(),
        )

    target = BackupTarget.objects.create(
        device=device,
        enabled=True,
        policy_override=policy,
        credential_override=credential,
        connection_override=connection,
        receiver_override=receiver_profile,
        driver_override=driver_id,
        driver_options_override=(
            {
                "allow_device_export": True,
                "restore_point": restore_point,
                "restore_sftp_port": True,
            }
            if driver_id == "ceragon_ip50" and allow_device_export
            else {
                "allow_device_export": True,
                "allow_legacy_ftp_setup": True,
                "sync_receiver_credentials": sync_receiver_credentials,
            }
            if (
                driver_id == "siae_smos_auto"
                and receiver_profile is not None
                and receiver_profile.protocol == "ftp"
                and allow_device_export
            )
            else {}
        ),
    )
    apply_target_schedule(target, now=timezone.now())
    return QuickSetupResult(
        target=target,
        connection_profile=connection,
        credential_profile=credential,
        backup_policy=policy,
        retention_policy=retention,
    )
