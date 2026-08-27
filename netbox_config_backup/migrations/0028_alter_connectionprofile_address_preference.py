from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0027_connectionprofile_auto_trust_first_host_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="connectionprofile",
            name="address_preference",
            field=models.CharField(
                choices=[
                    ("oob_first", "Dedicated management IP (OOB) first"),
                    ("primary4_first", "Primary IPv4 first"),
                    ("primary6_first", "Primary IPv6 first"),
                ],
                default="oob_first",
                max_length=24,
            ),
        ),
    ]
