"""Verify that representative 0.6-era records survived the current upgrade."""

from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    ConnectionProfile,
    DownloadEncryptionSecret,
    OperationalSettings,
    RemoteRetentionPolicy,
    RetentionPolicy,
)

PREFIX = "ncb-ci-upgrade"

settings = OperationalSettings.objects.get(singleton=True)
assert settings.retention_scheduler_enabled is True
assert settings.retention_scheduler_batch_size == 37
assert settings.events_enabled is False
assert settings.ui_language == "en"
assert settings.download_zip_encryption_enabled is False
assert not DownloadEncryptionSecret.objects.exists()

retention = RetentionPolicy.objects.get(name=f"{PREFIX}-local-retention")
assert retention.keep_all_days == 11
assert retention.max_runs_per_target == 321

remote_retention = RemoteRetentionPolicy.objects.get(name=f"{PREFIX}-remote-retention")
assert remote_retention.keep_all_days == 17
assert remote_retention.max_copies_per_target == 654

policy = BackupPolicy.objects.get(name=f"{PREFIX}-policy")
assert policy.command_timeout == 77
assert policy.retention_policy == retention

connection_profile = ConnectionProfile.objects.get(name=f"{PREFIX}-connection")
assert connection_profile.protocol == "ssh"
assert connection_profile.address_preference == "oob_first"
assert connection_profile.port == 2202
assert connection_profile.verify_host_key is False
assert connection_profile.auto_trust_first_host_key is False

target = BackupTarget.objects.select_related("device", "last_revision").get(
    device__name=f"{PREFIX}-device"
)
assert target.policy_override == policy
assert target.retention_override == retention
assert target.remote_retention_policy == remote_retention
assert target.last_revision is not None

revision = ConfigRevision.objects.get(target=target)
assert revision == target.last_revision
assert revision.label == "0.6 upgrade fixture"
assert ConfigArtifact.objects.filter(revision=revision, is_primary=True).count() == 1
assert BackupRun.objects.filter(
    target=target,
    revision=revision,
    status="success_changed",
).count() == 1

local_storage = BackupDestination.objects.get(protocol="local", is_default=True)
assert local_storage.local_retention_policy == retention
assert local_storage.enforce_retention_policy is True
assert BackupDestination.objects.filter(is_default=True).count() == 1

print(
    {
        "upgrade_data_preserved": True,
        "device": target.device.name,
        "revision": str(revision.revision_uuid),
        "ui_language_default": settings.ui_language,
    }
)
