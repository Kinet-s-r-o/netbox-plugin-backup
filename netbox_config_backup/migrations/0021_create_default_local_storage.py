from django.db import migrations


def create_default_local_storage(apps, schema_editor):
    BackupDestination = apps.get_model("netbox_config_backup", "BackupDestination")
    local_storages = list(BackupDestination.objects.filter(protocol="local").order_by("pk")[:2])
    if len(local_storages) > 1:
        raise RuntimeError(
            "More than one Local storage exists; resolve the duplicate rows before migrating."
        )

    if local_storages:
        local_storage = local_storages[0]
    else:
        base_name = "Local storage (default)"
        name = base_name
        suffix = 2
        while BackupDestination.objects.filter(name=name).exists():
            name = f"{base_name} (default {suffix})"
            suffix += 1
        local_storage = BackupDestination(name=name, protocol="local")

    local_storage.is_default = True
    local_storage.enabled = True
    local_storage.auto_replicate = False
    local_storage.integrity_audit_enabled = False
    local_storage.next_integrity_audit_at = None
    local_storage.allow_insecure_ftp = False
    local_storage.host = ""
    local_storage.port = None
    local_storage.base_path = ""
    local_storage.credential_profile_id = None
    local_storage.connect_timeout = None
    local_storage.max_retries = None
    local_storage.retry_delay_minutes = None
    local_storage.max_artifact_size = None
    local_storage.local_retention_policy_id = None
    local_storage.remote_retention_policy_id = None
    local_storage.enforce_retention_policy = False
    local_storage.save()


def remove_default_local_storage(apps, schema_editor):
    BackupDestination = apps.get_model("netbox_config_backup", "BackupDestination")
    BackupDestination.objects.filter(protocol="local", is_default=True).delete()


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0020_storage_profiles")]

    operations = [
        migrations.RunPython(
            create_default_local_storage,
            remove_default_local_storage,
        ),
    ]
