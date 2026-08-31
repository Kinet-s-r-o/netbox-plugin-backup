"""Verify NetBox bulk target deletion without leaving test records behind."""

from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import BackupRun, BackupTarget

with transaction.atomic():
    prefix = f"ncb-bulk-delete-{uuid4().hex[:8]}"
    site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
    manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
    device_type = DeviceType.objects.create(
        manufacturer=manufacturer,
        model=f"{prefix}-type",
        slug=f"{prefix}-type",
    )
    role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
    devices = [
        Device.objects.create(
            name=f"{prefix}-device-{number}",
            site=site,
            role=role,
            device_type=device_type,
        )
        for number in (1, 2)
    ]
    targets = [
        BackupTarget.objects.create(device=device, driver_override="fake") for device in devices
    ]
    target_ids = [target.pk for target in targets]

    user = get_user_model().objects.create_superuser(
        username=f"{prefix}-admin",
        password=uuid4().hex,
    )
    client = Client()
    client.force_login(user)
    bulk_delete_url = reverse("plugins:netbox_config_backup:backuptarget_bulk_delete")
    list_url = reverse("plugins:netbox_config_backup:backuptarget_list")

    preview = client.post(
        bulk_delete_url,
        {"pk": target_ids, "return_url": list_url},
    )
    assert preview.status_code == 200, preview.status_code
    assert all(str(target.device).encode() in preview.content for target in targets)
    assert b"Background job" not in preview.content

    active_run = BackupRun.objects.create(target=targets[1])
    confirmation = {
        "pk": target_ids,
        "confirm": "true",
        "_confirm": "1",
        "return_url": list_url,
        # A stale client may still submit the old field. It must not defer the
        # deletion to a worker or prevent the synchronous removal.
        "background_job": "on",
    }
    blocked = client.post(bulk_delete_url, confirmation)
    assert blocked.status_code == 302, blocked.status_code
    assert BackupTarget.objects.filter(pk__in=target_ids).count() == 2

    active_run.delete()
    deleted = client.post(bulk_delete_url, confirmation)
    assert deleted.status_code == 302, deleted.status_code
    assert not BackupTarget.objects.filter(pk__in=target_ids).exists()

    print(
        {
            "bulk_delete_url": bulk_delete_url,
            "confirmation_page": True,
            "active_run_blocks_all": True,
            "selected_count": len(target_ids),
            "bulk_deleted": True,
            "synchronous_delete": True,
            "database_rollback": True,
        }
    )
    transaction.set_rollback(True)
