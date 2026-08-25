from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0008_operationalsettings_runtime_controls"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="operationalsettings",
            name="metrics_enabled",
        ),
    ]
