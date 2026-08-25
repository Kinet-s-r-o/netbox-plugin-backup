from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0006_connectionprofile_legacy_dsa"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="connectionprofile",
            name="allow_legacy_dsa_host_key",
        ),
    ]
