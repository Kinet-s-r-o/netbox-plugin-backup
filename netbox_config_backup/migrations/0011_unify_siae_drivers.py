from django.db import migrations


UNIFIED_DRIVER = "siae_smos_auto"
LEGACY_CLI_DRIVERS = ("siae_smos_cli", "siae_smos_ssh")
LEGACY_NATIVE_MODELS = {
    "siae_alfoplus": "alfoplus",
    "siae_alfoplus2": "alfoplus2",
    "siae_alfoplus80hd": "alfoplus80hd",
    "siae_ags20": "ags20",
}


def unify_siae_drivers(apps, schema_editor):
    BackupTarget = apps.get_model("netbox_config_backup", "BackupTarget")
    PlatformMapping = apps.get_model("netbox_config_backup", "PlatformMapping")

    BackupTarget.objects.filter(driver_override__in=LEGACY_CLI_DRIVERS).update(
        driver_override=UNIFIED_DRIVER
    )
    PlatformMapping.objects.filter(driver_id__in=LEGACY_CLI_DRIVERS).update(
        driver_id=UNIFIED_DRIVER
    )

    for legacy_driver, native_model in LEGACY_NATIVE_MODELS.items():
        for target in BackupTarget.objects.filter(driver_override=legacy_driver).iterator():
            options = dict(target.driver_options_override or {})
            if options.get("remote_path"):
                options.setdefault("backup_method", "native")
                options.setdefault("native_model", native_model)
            target.driver_override = UNIFIED_DRIVER
            target.driver_options_override = options
            target.save(update_fields=("driver_override", "driver_options_override"))

        for mapping in PlatformMapping.objects.filter(driver_id=legacy_driver).iterator():
            options = dict(mapping.driver_options or {})
            if options.get("remote_path"):
                options.setdefault("backup_method", "native")
                options.setdefault("native_model", native_model)
            mapping.driver_id = UNIFIED_DRIVER
            mapping.driver_options = options
            mapping.save(update_fields=("driver_id", "driver_options"))


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0010_connectionprofile_protocol"),
    ]

    operations = [
        migrations.RunPython(unify_siae_drivers, migrations.RunPython.noop),
    ]
