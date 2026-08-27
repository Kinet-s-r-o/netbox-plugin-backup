from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0025_nfs_smb3_storages"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsettings",
            name="ui_language",
            field=models.CharField(
                choices=[("en", "English"), ("sk", "Slovenčina")],
                default="en",
                help_text=(
                    "Default language for Config Backup pages. Users can override it in Help."
                ),
                max_length=8,
            ),
        ),
    ]
