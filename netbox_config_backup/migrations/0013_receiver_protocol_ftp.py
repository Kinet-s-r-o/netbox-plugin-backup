from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0012_sshhostkey")]

    operations = [
        migrations.AddField(
            model_name="sftpreceiverprofile",
            name="protocol",
            field=models.CharField(
                choices=[
                    ("sftp", "SFTP (recommended)"),
                    ("ftp", "Legacy FTP (ALFOplus only)"),
                ],
                default="sftp",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="sftpreceiverprofile",
            name="passive_port_start",
            field=models.PositiveIntegerField(
                default=30000,
                help_text="First passive data port used only by the legacy FTP receiver.",
                validators=[MinValueValidator(1024), MaxValueValidator(65535)],
            ),
        ),
        migrations.AddField(
            model_name="sftpreceiverprofile",
            name="passive_port_end",
            field=models.PositiveIntegerField(
                default=30009,
                help_text="Last passive data port used only by the legacy FTP receiver.",
                validators=[MinValueValidator(1024), MaxValueValidator(65535)],
            ),
        ),
    ]
