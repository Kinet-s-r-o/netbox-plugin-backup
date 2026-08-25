from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0004_sftpreceiverprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="backuptarget",
            name="driver_options_override",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
