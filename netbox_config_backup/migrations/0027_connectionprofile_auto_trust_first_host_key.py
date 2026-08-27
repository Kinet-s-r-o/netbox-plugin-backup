from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0026_operationalsettings_ui_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectionprofile",
            name="auto_trust_first_host_key",
            field=models.BooleanField(default=False),
        ),
    ]
