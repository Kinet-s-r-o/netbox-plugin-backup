from __future__ import annotations

from netbox_config_backup.choices import (
    DestinationProtocolChoices,
    ReplicaStatusChoices,
)

from .destination_ftp import delete_revision_replica_ftp
from .destination_types import DestinationError


def validate_target_external_copies(replicas) -> None:
    if any(
        replica.status in (ReplicaStatusChoices.QUEUED, ReplicaStatusChoices.RUNNING)
        for replica in replicas
    ):
        raise ValueError("The target has an active FTP transfer and cannot be deleted.")
    if any(
        replica.destination.protocol == DestinationProtocolChoices.FTP
        and not replica.destination.enabled
        and (replica.remote_available or replica.remote_path)
        for replica in replicas
    ):
        raise ValueError(
            "An FTP destination containing this target is disabled. Enable it before "
            "deleting the target so its external copies can be removed safely."
        )
    if any(
        replica.destination.protocol != DestinationProtocolChoices.FTP
        and (replica.remote_available or replica.remote_path)
        for replica in replicas
    ):
        raise ValueError(
            "The target still has an external copy which this FTP-only version "
            "cannot remove safely. Delete that copy before removing the target."
        )


def delete_target_ftp_copies(replicas) -> None:
    for replica in replicas:
        if replica.destination.protocol != DestinationProtocolChoices.FTP:
            continue
        if not replica.remote_path and not replica.remote_available:
            continue
        if not replica.remote_path:
            raise ValueError(
                "An FTP copy has inconsistent path metadata. The target was not deleted."
            )
        try:
            delete_revision_replica_ftp(replica)
        except DestinationError as exc:
            raise ValueError(
                f"An FTP copy could not be removed safely ({exc.error_code}). "
                "The target was not deleted."
            ) from exc
