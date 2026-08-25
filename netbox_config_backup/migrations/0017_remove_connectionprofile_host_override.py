from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0016_backupdestination_integrity_audit")]

    operations = [
        migrations.RemoveField(
            model_name="connectionprofile",
            name="host_override",
        ),
    ]
