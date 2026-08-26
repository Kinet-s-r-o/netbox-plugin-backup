from __future__ import annotations

from netbox_config_backup.choices import DestinationProtocolChoices
from netbox_config_backup.models import BackupDestination, ConfigRevision

from .destination_ftp import (
    reconcile_ftp_destination,
    replicate_revision_ftp,
    test_ftp_destination,
)
from .destination_sftp import (
    replicate_revision as replicate_revision_sftp,
)
from .destination_sftp import (
    test_destination as test_sftp_destination,
)
from .destination_types import DestinationError, ReplicationResult


def test_destination(destination: BackupDestination) -> dict[str, object]:
    if destination.protocol == DestinationProtocolChoices.FTP:
        return test_ftp_destination(destination)
    return test_sftp_destination(destination)


def replicate_revision(
    destination: BackupDestination,
    revision: ConfigRevision,
    *,
    recorded_remote_path: str | None = None,
) -> ReplicationResult:
    if destination.protocol == DestinationProtocolChoices.FTP:
        return replicate_revision_ftp(
            destination,
            revision,
            recorded_remote_path=recorded_remote_path,
        )
    return replicate_revision_sftp(destination, revision)


def reconcile_destination(destination: BackupDestination) -> dict[str, object]:
    if destination.protocol != DestinationProtocolChoices.FTP:
        raise DestinationError(
            "PROTOCOL_UNSUPPORTED",
            "Integrity reconciliation is currently available only for FTP destinations.",
        )
    return reconcile_ftp_destination(destination)


__all__ = (
    "DestinationError",
    "ReplicationResult",
    "reconcile_destination",
    "replicate_revision",
    "test_destination",
)
