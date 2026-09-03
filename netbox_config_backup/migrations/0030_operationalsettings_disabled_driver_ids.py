from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0029_download_zip_encryption"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsettings",
            name="disabled_driver_ids",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text=(
                    "Drivers disabled for new assignments and backup execution; history is retained."
                ),
            ),
        ),
    ]
