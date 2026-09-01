"""Verify the paginated, filterable revision inventory on a storage detail page."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from netbox_config_backup.models import (
    BackupDestination,
    BackupTarget,
    ConfigRevision,
    RevisionReplica,
)

prefix = f"ncb-storage-inventory-{uuid4().hex[:8]}"
target = BackupTarget.objects.filter(device__name__startswith="ncb-smoke-").first()
assert target is not None, "The main Docker smoke test must create a backup target first."

destination = BackupDestination.objects.create(
    name=prefix,
    enabled=False,
    auto_replicate=False,
    protocol="nfs",
    host="",
    port=None,
    base_path=prefix,
    mount_path=f"/mnt/netbox-config-backup/{prefix}",
    credential_profile=None,
    connect_timeout=None,
    max_retries=3,
    retry_delay_minutes=15,
    max_artifact_size=1024 * 1024,
)
user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
revisions = []
replicas = []

try:
    for index in range(28):
        revision = ConfigRevision.objects.create(
            target=target,
            normalized_hash=f"{index + 1:064x}",
            normalizer_version="storage-inventory-smoke-v1",
            driver_id="fake",
            content_changed=True,
        )
        revisions.append(revision)
        values = {
            "revision": revision,
            "destination": destination,
            "status": "success",
            "attempts": 1,
            "bytes_transferred": 1024 + index,
            "remote_path": f"{prefix}/{target.device.name}/revision-{index:02d}",
            "remote_available": True,
            "finished_at": timezone.now(),
        }
        if index == 26:
            values.update(
                status="failed",
                remote_available=False,
                error_code="INVENTORY_SMOKE_FAILURE",
                error_message="Safe inventory failure detail.",
            )
        elif index == 27:
            values.update(
                remote_available=False,
                remote_deleted_at=timezone.now(),
            )
        replicas.append(RevisionReplica.objects.create(**values))

    client = Client()
    client.force_login(user)

    detail = client.get(destination.get_absolute_url())
    assert detail.status_code == 200
    assert b"Stored revisions" in detail.content
    assert b"28 tracked" in detail.content
    assert b"26 available" in detail.content
    assert b"1 with problems" in detail.content
    assert b"1 expired" in detail.content
    assert b"of 28 matching copies" in detail.content
    assert replicas[0].remote_path.encode() not in detail.content

    second_page = client.get(destination.get_absolute_url(), {"replica_page": "2"})
    assert second_page.status_code == 200
    assert replicas[0].remote_path.encode() in second_page.content

    available = client.get(
        destination.get_absolute_url(),
        {"replica_state": "available"},
    )
    assert available.status_code == 200
    assert b"of 26 matching copies" in available.content
    assert b"INVENTORY_SMOKE_FAILURE" not in available.content

    problems = client.get(
        destination.get_absolute_url(),
        {"replica_state": "problems"},
    )
    assert problems.status_code == 200
    assert b"of 1 matching copies" in problems.content
    assert b"INVENTORY_SMOKE_FAILURE" in problems.content

    expired = client.get(
        destination.get_absolute_url(),
        {"replica_state": "expired"},
    )
    assert expired.status_code == 200
    assert b"of 1 matching copies" in expired.content
    assert b"Expired" in expired.content

    searched = client.get(
        destination.get_absolute_url(),
        {"replica_q": str(revisions[10].revision_uuid)},
    )
    assert searched.status_code == 200
    assert b"of 1 matching copies" in searched.content
    assert str(revisions[10].revision_uuid).encode() in searched.content

    print(
        {
            "tracked": 28,
            "available": 26,
            "problems": 1,
            "expired": 1,
            "pagination": True,
            "search": True,
        }
    )
finally:
    RevisionReplica.objects.filter(pk__in=[replica.pk for replica in replicas]).delete()
    ConfigRevision.objects.filter(pk__in=[revision.pk for revision in revisions]).delete()
    destination.delete()
    user.delete()
