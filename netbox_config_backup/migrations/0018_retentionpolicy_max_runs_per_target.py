from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0017_remove_connectionprofile_host_override")]

    operations = [
        migrations.AddField(
            model_name="retentionpolicy",
            name="max_runs_per_target",
            field=models.PositiveIntegerField(
                default=500,
                help_text=(
                    "Hard safety limit for completed backup runs retained per device. "
                    "Queued and running backups are never removed by this limit."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(100000)],
            ),
        ),
    ]
