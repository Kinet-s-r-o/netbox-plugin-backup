"""Run inside a NetBox container with ``manage.py shell``."""

import json
from datetime import time
from pathlib import Path
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.conf import settings

from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.models import (
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    PlatformMapping,
    RetentionPolicy,
)
from netbox_config_backup.services.backup import BackupPipeline
from netbox_config_backup.services.django_repository import DjangoBackupRepository
from netbox_config_backup.storage.local import LocalConfigStorage

prefix = f"ncb-smoke-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(
    name=f"{prefix}-manufacturer", slug=f"{prefix}-manufacturer"
)
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-device-type",
    slug=f"{prefix}-device-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
platform = Platform.objects.create(name=f"{prefix}-platform", slug=f"{prefix}-platform")
device = Device.objects.create(
    name=f"{prefix}-device",
    site=site,
    role=role,
    device_type=device_type,
    platform=platform,
)

retention = RetentionPolicy.objects.create(name=f"{prefix}-retention")
policy = BackupPolicy.objects.create(
    name=f"{prefix}-policy",
    schedule_type="daily",
    time_of_day=time(3, 0),
    store_mode="changed_only",
    retention_policy=retention,
)
mapping = PlatformMapping.objects.create(
    platform=platform,
    driver_id="fake",
    driver_options={
        "config": "hostname ncb-smoke-device\ninterface Loopback0\n description first\n"
    },
)
target = BackupTarget.objects.create(device=device, policy_override=policy)

storage_root = settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"]
pipeline = BackupPipeline(
    repository=DjangoBackupRepository(),
    drivers=driver_registry,
    storage=LocalConfigStorage(storage_root),
)

first_run = BackupRun.objects.create(target=target)
first = pipeline.execute(first_run.pk)
assert first.status == "success_changed", first
assert ConfigRevision.objects.filter(target=target).count() == 1
artifact = ConfigArtifact.objects.get(revision_id=first.revision_id, is_primary=True)
assert (
    Path(storage_root, artifact.storage_key)
    .read_text(encoding="utf-8")
    .startswith("hostname ncb-smoke-device")
)

second_run = BackupRun.objects.create(target=target)
second = pipeline.execute(second_run.pk)
assert second.status == "success_unchanged", second
assert ConfigRevision.objects.filter(target=target).count() == 1

mapping.driver_options = {
    "config": "hostname ncb-smoke-device\ninterface Loopback0\n description changed\n"
}
mapping.save()
third_run = BackupRun.objects.create(target=target)
third = pipeline.execute(third_run.pk)
assert third.status == "success_changed", third
assert ConfigRevision.objects.filter(target=target).count() == 2
assert ConfigArtifact.objects.filter(revision__target=target).count() == 2

print(
    json.dumps(
        {
            "plugin": "netbox_config_backup",
            "driver_registered": driver_registry.contains("fake"),
            "first_run": first.status,
            "second_run": second.status,
            "third_run": third.status,
            "revision_count": ConfigRevision.objects.filter(target=target).count(),
            "artifact_count": ConfigArtifact.objects.filter(revision__target=target).count(),
            "storage_key": ConfigArtifact.objects.filter(revision__target=target)
            .order_by("created")
            .values_list("storage_key", flat=True)
            .first(),
        },
        sort_keys=True,
    )
)
