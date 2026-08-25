from django.conf import settings
from django.db import migrations, models


def preserve_deployment_controls(apps, schema_editor):
    OperationalSettings = apps.get_model(
        "netbox_config_backup", "OperationalSettings"
    )
    plugin_settings = settings.PLUGINS_CONFIG.get("netbox_config_backup", {})
    OperationalSettings.objects.filter(singleton=True).update(
        events_enabled=bool(plugin_settings.get("events_enabled", True)),
        notify_on_every_failure=bool(
            plugin_settings.get("notify_on_every_failure", False)
        ),
        metrics_enabled=bool(plugin_settings.get("metrics_enabled", False)),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0007_remove_connectionprofile_legacy_dsa"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsettings",
            name="events_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="operationalsettings",
            name="metrics_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="operationalsettings",
            name="notify_on_every_failure",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            preserve_deployment_controls,
            migrations.RunPython.noop,
        ),
    ]
