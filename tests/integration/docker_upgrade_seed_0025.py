"""Create representative 0.6-era data using the historical migration state."""

from datetime import time

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

MIGRATION = ("netbox_config_backup", "0025_nfs_smb3_storages")
PREFIX = "ncb-ci-upgrade"

executor = MigrationExecutor(connection)
apps = executor.loader.project_state([MIGRATION]).apps

OperationalSettings = apps.get_model("netbox_config_backup", "OperationalSettings")
RetentionPolicy = apps.get_model("netbox_config_backup", "RetentionPolicy")
RemoteRetentionPolicy = apps.get_model("netbox_config_backup", "RemoteRetentionPolicy")
BackupPolicy = apps.get_model("netbox_config_backup", "BackupPolicy")
ConnectionProfile = apps.get_model("netbox_config_backup", "ConnectionProfile")
PlatformMapping = apps.get_model("netbox_config_backup", "PlatformMapping")
BackupDestination = apps.get_model("netbox_config_backup", "BackupDestination")
BackupTarget = apps.get_model("netbox_config_backup", "BackupTarget")
ConfigRevision = apps.get_model("netbox_config_backup", "ConfigRevision")
ConfigArtifact = apps.get_model("netbox_config_backup", "ConfigArtifact")
BackupRun = apps.get_model("netbox_config_backup", "BackupRun")
Site = apps.get_model("dcim", "Site")
Manufacturer = apps.get_model("dcim", "Manufacturer")
DeviceType = apps.get_model("dcim", "DeviceType")
DeviceRole = apps.get_model("dcim", "DeviceRole")
Platform = apps.get_model("dcim", "Platform")
Device = apps.get_model("dcim", "Device")

settings = OperationalSettings.objects.get(singleton=True)
settings.retention_scheduler_enabled = True
settings.retention_scheduler_batch_size = 37
settings.events_enabled = False
settings.save()

retention = RetentionPolicy.objects.create(
    name=f"{PREFIX}-local-retention",
    keep_all_days=11,
    max_runs_per_target=321,
)
remote_retention = RemoteRetentionPolicy.objects.create(
    name=f"{PREFIX}-remote-retention",
    keep_all_days=17,
    max_copies_per_target=654,
)
policy = BackupPolicy.objects.create(
    name=f"{PREFIX}-policy",
    schedule_type="daily",
    time_of_day=time(4, 15),
    command_timeout=77,
    retention_policy=retention,
)
connection_profile = ConnectionProfile.objects.create(
    name=f"{PREFIX}-connection",
    protocol="ssh",
    address_preference="oob_first",
    port=2202,
    verify_host_key=False,
)

site = Site.objects.create(
    name=f"{PREFIX}-site",
    slug=f"{PREFIX}-site",
    facility="",
    description="",
    physical_address="",
    shipping_address="",
    comments="",
)
manufacturer = Manufacturer.objects.create(
    name=f"{PREFIX}-manufacturer",
    slug=f"{PREFIX}-manufacturer",
    description="",
    comments="",
)
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{PREFIX}-type",
    slug=f"{PREFIX}-type",
    part_number="",
    front_image="",
    rear_image="",
    description="",
    comments="",
)
role = DeviceRole.objects.create(
    name=f"{PREFIX}-role",
    slug=f"{PREFIX}-role",
    description="",
    comments="",
    level=0,
    lft=1,
    rght=2,
    tree_id=1,
)
platform = Platform.objects.create(
    name=f"{PREFIX}-platform",
    slug=f"{PREFIX}-platform",
    description="",
    comments="",
    level=0,
    lft=1,
    rght=2,
    tree_id=1,
)
device = Device.objects.create(
    name=f"{PREFIX}-device",
    site=site,
    role=role,
    device_type=device_type,
    platform=platform,
    serial="",
    description="",
    comments="",
)
PlatformMapping.objects.create(
    platform=platform,
    driver_id="fake",
    connection_profile=connection_profile,
    driver_options={"config": "hostname ncb-ci-upgrade-device\n"},
)
target = BackupTarget.objects.create(
    device=device,
    policy_override=policy,
    retention_override=retention,
    remote_retention_policy=remote_retention,
)
revision = ConfigRevision.objects.create(
    target=target,
    normalized_hash="a" * 64,
    normalizer_version="1",
    driver_id="fake",
    content_changed=True,
    label="0.6 upgrade fixture",
)
ConfigArtifact.objects.create(
    revision=revision,
    artifact_type="configuration",
    format="text",
    storage_key=f"{PREFIX}/configuration.txt",
    size=31,
    raw_hash="b" * 64,
    normalized_hash="a" * 64,
    is_primary=True,
)
BackupRun.objects.create(
    target=target,
    revision=revision,
    status="success_changed",
    changed=True,
    raw_changed=True,
)
target.last_revision = revision
target.save(update_fields=("last_revision",))

local_storage = BackupDestination.objects.get(protocol="local", is_default=True)
local_storage.local_retention_policy = retention
local_storage.enforce_retention_policy = True
local_storage.save()

print(
    {
        "migration": MIGRATION[1],
        "device": device.name,
        "revision": str(revision.revision_uuid),
        "policy": policy.name,
    }
)
