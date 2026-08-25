from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0005_target_driver_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectionprofile",
            name="allow_legacy_dsa_host_key",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Enable only for old RACOM RAy devices which offer an ssh-dss host key. "
                    "Strict verification against the configured known_hosts file remains required."
                ),
                verbose_name="Allow legacy RAy DSA host key",
            ),
        ),
    ]
