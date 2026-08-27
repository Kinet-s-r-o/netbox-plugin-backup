from __future__ import annotations

import asyncio
import base64
import hashlib
import socket
from dataclasses import dataclass

import asyncssh
import paramiko
from django.db import transaction
from django.utils import timezone

from netbox_config_backup.choices import (
    ConnectionProtocolChoices,
    SSHHostKeyStatusChoices,
)
from netbox_config_backup.drivers.base import DriverError
from netbox_config_backup.models import SSHHostKey

from .django_repository import DjangoBackupRepository


@dataclass(frozen=True, slots=True)
class ScannedHostKey:
    candidate_id: int
    address: str
    port: int
    key_type: str
    fingerprint_sha256: str
    fingerprint_md5: str
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.candidate_id,
            "address": self.address,
            "port": self.port,
            "key_type": self.key_type,
            "fingerprint_sha256": self.fingerprint_sha256,
            "fingerprint_md5": self.fingerprint_md5,
            "status": self.status,
        }


def _fingerprints(public_key: str) -> tuple[str, str]:
    try:
        raw = base64.b64decode(public_key, validate=True)
    except ValueError as exc:
        raise DriverError(
            "HOST_KEY_SCAN_FAILED", "The device presented an invalid SSH key."
        ) from exc
    sha256 = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    md5 = ":".join(f"{byte:02x}" for byte in hashlib.md5(raw, usedforsecurity=False).digest())
    return f"SHA256:{sha256}", f"MD5:{md5}"


def _scan_legacy_host_key(address: str, port: int, timeout: int) -> tuple[str, str]:
    """Fallback for old appliances which only offer an ssh-dss host key."""

    async def retrieve():
        return await asyncio.wait_for(
            asyncssh.get_server_host_key(
                address,
                port,
                server_host_key_algs="+ssh-dss",
                config=None,
            ),
            timeout=timeout,
        )

    try:
        key = asyncio.run(retrieve())
        if key is None:
            raise DriverError("HOST_KEY_SCAN_FAILED", "The device did not present an SSH host key.")
        parts = key.export_public_key("openssh").decode().strip().split()
        if len(parts) < 2:
            raise DriverError("HOST_KEY_SCAN_FAILED", "The device presented an invalid SSH key.")
        return parts[0], parts[1]
    except DriverError:
        raise
    except TimeoutError as exc:
        raise DriverError(
            "TIMEOUT", "The SSH identity scan timed out before the device responded."
        ) from exc
    except (asyncssh.Error, OSError, ValueError) as exc:
        raise DriverError(
            "HOST_KEY_SCAN_FAILED", "The device SSH identity could not be scanned safely."
        ) from exc


def scan_target_host_key(target_id: int) -> ScannedHostKey:
    """Read the pre-authentication SSH server key and store an approval candidate."""
    context = DjangoBackupRepository().get_target_execution_context(target_id)
    if not context.address:
        raise DriverError("NO_ADDRESS", "The device has no usable management address.")
    if not context.connection.verify_host_key:
        raise DriverError(
            "HOST_KEY_VERIFICATION_DISABLED",
            "SSH server identity verification is disabled for this connection profile.",
        )
    if context.connection.protocol == ConnectionProtocolChoices.TELNET or (
        context.connection.protocol == ConnectionProtocolChoices.AUTOMATIC
        and context.connection.port == 23
    ):
        raise DriverError("NOT_SSH", "SSH host-key approval is not used for Telnet devices.")

    sock = None
    transport = None
    key_type = ""
    public_key = ""
    try:
        sock = socket.create_connection(
            (context.address, context.connection.port),
            timeout=context.connection.connect_timeout,
        )
        sock.settimeout(context.connection.connect_timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=context.connection.connect_timeout)
        key = transport.get_remote_server_key()
        if key is None:
            raise DriverError("HOST_KEY_SCAN_FAILED", "The device did not present an SSH host key.")
        key_type = key.get_name()
        public_key = key.get_base64()
    except DriverError:
        raise
    except ConnectionRefusedError as exc:
        raise DriverError(
            "CONNECTION_REFUSED",
            "The device rejected the TCP connection while its SSH identity was scanned.",
        ) from exc
    except TimeoutError as exc:
        raise DriverError(
            "TIMEOUT", "The SSH identity scan timed out before the device responded."
        ) from exc
    except paramiko.SSHException:
        if transport is not None:
            transport.close()
            transport = None
            sock = None
        key_type, public_key = _scan_legacy_host_key(
            context.address,
            context.connection.port,
            context.connection.connect_timeout,
        )
    except OSError as exc:
        raise DriverError(
            "HOST_KEY_SCAN_FAILED", "The device SSH identity could not be scanned safely."
        ) from exc
    finally:
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()

    fingerprint_sha256, fingerprint_md5 = _fingerprints(public_key)
    now = timezone.now()
    candidate, _created = SSHHostKey.objects.update_or_create(
        target_id=target_id,
        address=context.address,
        port=context.connection.port,
        key_type=key_type,
        fingerprint_sha256=fingerprint_sha256,
        defaults={
            "public_key": public_key,
            "fingerprint_md5": fingerprint_md5,
            "last_seen_at": now,
        },
    )
    if (
        context.connection.verify_host_key
        and context.connection.auto_trust_first_host_key
        and candidate.status == SSHHostKeyStatusChoices.PENDING
    ):
        candidate = trust_first_seen_host_key(candidate.pk)
    return ScannedHostKey(
        candidate_id=candidate.pk,
        address=candidate.address,
        port=candidate.port,
        key_type=candidate.key_type,
        fingerprint_sha256=candidate.fingerprint_sha256,
        fingerprint_md5=candidate.fingerprint_md5,
        status=candidate.status,
    )


def _snapshot(instance) -> None:
    if hasattr(instance, "snapshot"):
        instance.snapshot()


@transaction.atomic
def trust_first_seen_host_key(candidate_id: int) -> SSHHostKey:
    """Trust only the first identity ever observed for one target endpoint."""
    candidate = SSHHostKey.objects.select_for_update().get(pk=candidate_id)
    if candidate.status != SSHHostKeyStatusChoices.PENDING:
        return candidate

    endpoint_history_exists = (
        SSHHostKey.objects.select_for_update()
        .filter(
            target_id=candidate.target_id,
            address=candidate.address,
            port=candidate.port,
        )
        .exclude(pk=candidate.pk)
        .exists()
    )
    if endpoint_history_exists:
        return candidate

    _snapshot(candidate)
    candidate.status = SSHHostKeyStatusChoices.TRUSTED
    candidate.approved_at = timezone.now()
    candidate.approved_by = None
    candidate.rejected_at = None
    candidate._changelog_message = "Automatically trusted the first observed SSH host key."
    candidate.save(
        update_fields=(
            "status",
            "approved_at",
            "approved_by",
            "rejected_at",
            "last_updated",
        )
    )
    return candidate


def ensure_first_host_key_trusted(target_id: int) -> ScannedHostKey | None:
    """Materialize TOFU before authentication; later identity changes remain blocked."""
    context = DjangoBackupRepository().get_target_execution_context(target_id)
    if not context.connection.verify_host_key or not context.connection.auto_trust_first_host_key:
        return None
    if context.connection.trusted_host_keys:
        return None
    return scan_target_host_key(target_id)


@transaction.atomic
def trust_host_key(candidate_id: int, *, user) -> SSHHostKey:
    candidate = SSHHostKey.objects.select_for_update().get(pk=candidate_id)
    previous = (
        SSHHostKey.objects.select_for_update()
        .filter(
            target_id=candidate.target_id,
            address=candidate.address,
            port=candidate.port,
            status=SSHHostKeyStatusChoices.TRUSTED,
        )
        .exclude(pk=candidate.pk)
    )
    now = timezone.now()
    for item in previous:
        _snapshot(item)
        item.status = SSHHostKeyStatusChoices.REJECTED
        item.rejected_at = now
        item._changelog_message = "Replaced by a newly approved SSH host key."
        item.save(update_fields=("status", "rejected_at", "last_updated"))

    _snapshot(candidate)
    candidate.status = SSHHostKeyStatusChoices.TRUSTED
    candidate.approved_at = now
    candidate.approved_by = user
    candidate.rejected_at = None
    candidate._changelog_message = "SSH host key approved after fingerprint verification."
    candidate.save(
        update_fields=(
            "status",
            "approved_at",
            "approved_by",
            "rejected_at",
            "last_updated",
        )
    )
    return candidate


@transaction.atomic
def reject_host_key(candidate_id: int, *, user) -> SSHHostKey:
    candidate = SSHHostKey.objects.select_for_update().get(pk=candidate_id)
    _snapshot(candidate)
    candidate.status = SSHHostKeyStatusChoices.REJECTED
    candidate.rejected_at = timezone.now()
    candidate.approved_at = None
    candidate.approved_by = None
    candidate._changelog_message = f"SSH host key rejected by {user}."
    candidate.save(
        update_fields=(
            "status",
            "rejected_at",
            "approved_at",
            "approved_by",
            "last_updated",
        )
    )
    return candidate
