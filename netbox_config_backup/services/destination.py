from __future__ import annotations

from netbox_config_backup.choices import (
    MOUNTED_DESTINATION_PROTOCOLS,
    DestinationProtocolChoices,
)
from netbox_config_backup.models import BackupDestination, ConfigRevision

from .destination_ftp import (
    delete_revision_replica_ftp,
    reconcile_ftp_destination,
    replicate_revision_ftp,
    test_ftp_destination,
)
from .destination_mounted import (
    delete_revision_replica_mounted,
    reconcile_mounted_destination,
    replicate_revision_mounted,
    test_mounted_destination,
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
    if destination.protocol in MOUNTED_DESTINATION_PROTOCOLS:
        return test_mounted_destination(destination)
    if destination.protocol == DestinationProtocolChoices.SFTP:
        return test_sftp_destination(destination)
    raise DestinationError("PROTOCOL_UNSUPPORTED", "This storage type cannot be tested.")


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
    if destination.protocol in MOUNTED_DESTINATION_PROTOCOLS:
        return replicate_revision_mounted(
            destination,
            revision,
            recorded_remote_path=recorded_remote_path,
        )
    if destination.protocol == DestinationProtocolChoices.SFTP:
        return replicate_revision_sftp(destination, revision)
    raise DestinationError("PROTOCOL_UNSUPPORTED", "This storage type cannot receive replicas.")


def reconcile_destination(destination: BackupDestination) -> dict[str, object]:
    if destination.protocol == DestinationProtocolChoices.FTP:
        return reconcile_ftp_destination(destination)
    if destination.protocol in MOUNTED_DESTINATION_PROTOCOLS:
        return reconcile_mounted_destination(destination)
    raise DestinationError(
        "PROTOCOL_UNSUPPORTED",
        "Integrity reconciliation is not available for this storage type.",
    )


def delete_revision_replica(replica):
    if replica.destination.protocol == DestinationProtocolChoices.FTP:
        return delete_revision_replica_ftp(replica)
    if replica.destination.protocol in MOUNTED_DESTINATION_PROTOCOLS:
        return delete_revision_replica_mounted(replica)
    raise DestinationError(
        "DELETE_PROTOCOL_UNSUPPORTED",
        "This storage type does not support managed revision deletion.",
    )


__all__ = (
    "DestinationError",
    "ReplicationResult",
    "delete_revision_replica",
    "reconcile_destination",
    "replicate_revision",
    "test_destination",
)
