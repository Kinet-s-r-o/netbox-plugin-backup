from __future__ import annotations

from netbox_config_backup.choices import (
    REPLICATED_DESTINATION_PROTOCOLS,
    ReplicaStatusChoices,
)

from .destination_types import DestinationError


def delete_revision_replica_ftp(replica):
    from .destination_ftp import delete_revision_replica_ftp as delete

    return delete(replica)


def delete_revision_replica_mounted(replica):
    from .destination_mounted import delete_revision_replica_mounted as delete

    return delete(replica)


def validate_target_external_copies(replicas) -> None:
    if any(
        replica.status in (ReplicaStatusChoices.QUEUED, ReplicaStatusChoices.RUNNING)
        for replica in replicas
    ):
        raise ValueError("The target has an active remote transfer and cannot be deleted.")
    if any(
        replica.destination.protocol in REPLICATED_DESTINATION_PROTOCOLS
        and not replica.destination.enabled
        and (replica.remote_available or replica.remote_path)
        for replica in replicas
    ):
        raise ValueError(
            "A remote storage containing this target is disabled. Enable it before "
            "deleting the target so its external copies can be removed safely."
        )
    if any(
        replica.destination.protocol not in REPLICATED_DESTINATION_PROTOCOLS
        and (replica.remote_available or replica.remote_path)
        for replica in replicas
    ):
        raise ValueError(
            "The target still has an external copy which this version "
            "cannot remove safely. Delete that copy before removing the target."
        )


def delete_target_ftp_copies(replicas) -> None:
    for replica in replicas:
        if replica.destination.protocol not in REPLICATED_DESTINATION_PROTOCOLS:
            continue
        if not replica.remote_path and not replica.remote_available:
            continue
        if not replica.remote_path:
            raise ValueError(
                "A remote copy has inconsistent path metadata. The target was not deleted."
            )
        try:
            if replica.destination.protocol == "ftp":
                delete_revision_replica_ftp(replica)
            else:
                delete_revision_replica_mounted(replica)
        except DestinationError as exc:
            raise ValueError(
                f"A remote copy could not be removed safely ({exc.error_code}). "
                "The target was not deleted."
            ) from exc
