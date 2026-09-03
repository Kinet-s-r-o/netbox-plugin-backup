"""Verify bulk editing backup devices through the registered NetBox endpoint."""

from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import (
    BackupPolicy,
    BackupTarget,
    OperationalSettings,
    RemoteRetentionPolicy,
    RetentionPolicy,
)

with transaction.atomic():
    prefix = f"ncb-bulk-edit-{uuid4().hex[:8]}"
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
    local_retention = RetentionPolicy.objects.create(name=f"{prefix}-local")
    remote_retention = RemoteRetentionPolicy.objects.create(name=f"{prefix}-remote")
    policy = BackupPolicy.objects.create(
        name=f"{prefix}-policy",
        schedule_type="interval",
        interval_minutes=60,
        retention_policy=local_retention,
    )
    targets = [
        BackupTarget.objects.create(device=device, enabled=False, driver_override="fake")
        for device in devices
    ]
    target_ids = [target.pk for target in targets]

    # The retention safety gate requires the singleton to be visible to the
    # editor, even when the editor is a superuser.
    OperationalSettings.objects.get_or_create(singleton=True)
    user = get_user_model().objects.create_superuser(
        username=f"{prefix}-admin",
        password=uuid4().hex,
    )
    client = Client()
    client.force_login(user)
    list_url = reverse("plugins:netbox_config_backup:backuptarget_list")
    bulk_edit_url = reverse("plugins:netbox_config_backup:backuptarget_bulk_edit")

    # This is the request submitted by NetBox's Edit Selected action. It used
    # to resolve a nonexistent detail view as /targets/None.
    preview = client.post(
        bulk_edit_url,
        {"pk": target_ids, "return_url": list_url},
    )
    assert preview.status_code == 200, preview.status_code
    assert b"Bulk Edit" in preview.content
    assert all(str(target.device).encode() in preview.content for target in targets)
    for field_name in (
        "enabled",
        "policy_override",
        "retention_override",
        "remote_retention_policy",
        "credential_override",
        "connection_override",
        "receiver_override",
        "driver_override",
    ):
        assert b'name="' + field_name.encode() + b'"' in preview.content, field_name

    applied = client.post(
        bulk_edit_url,
        {
            "pk": target_ids,
            "enabled": "2",  # BulkEditNullBooleanSelect: Yes
            "policy_override": policy.pk,
            "retention_override": local_retention.pk,
            "remote_retention_policy": remote_retention.pk,
            "credential_override": "",
            "connection_override": "",
            "receiver_override": "",
            "driver_override": "fake",
            "return_url": list_url,
            "_apply": "1",
        },
    )
    assert applied.status_code == 302, applied.status_code
    updated = list(BackupTarget.objects.filter(pk__in=target_ids).order_by("pk"))
    assert all(target.enabled for target in updated)
    assert all(target.policy_override_id == policy.pk for target in updated)
    assert all(target.retention_override_id == local_retention.pk for target in updated)
    assert all(target.remote_retention_policy_id == remote_retention.pk for target in updated)
    assert all(target.driver_override == "fake" for target in updated)
    assert all(target.next_run_at is not None for target in updated)

    # Nullable fields use NetBox's explicit clear control; an empty select by
    # itself must mean "leave the value unchanged".
    cleared = client.post(
        bulk_edit_url,
        {
            "pk": target_ids,
            "enabled": "1",  # BulkEditNullBooleanSelect: leave unchanged
            "policy_override": "",
            "retention_override": "",
            "remote_retention_policy": "",
            "credential_override": "",
            "connection_override": "",
            "receiver_override": "",
            "driver_override": "",
            "_nullify": [
                "policy_override",
                "retention_override",
                "remote_retention_policy",
                "driver_override",
            ],
            "return_url": list_url,
            "_apply": "1",
        },
    )
    assert cleared.status_code == 302, cleared.status_code
    updated = list(BackupTarget.objects.filter(pk__in=target_ids))
    assert all(target.policy_override_id is None for target in updated)
    assert all(target.retention_override_id is None for target in updated)
    assert all(target.remote_retention_policy_id is None for target in updated)
    assert all(target.driver_override == "" for target in updated)
    assert all(target.next_run_at is None for target in updated)

    print(
        {
            "bulk_edit_url": bulk_edit_url,
            "preview": True,
            "selected_count": len(target_ids),
            "shared_settings_updated": True,
            "nullable_profiles_cleared": True,
            "schedules_recalculated": True,
            "database_rollback": True,
        }
    )
    transaction.set_rollback(True)
