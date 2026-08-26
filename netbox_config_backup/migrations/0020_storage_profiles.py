import django.db.models.deletion
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0019_remote_retention_profiles")]

    operations = [
        migrations.AlterModelOptions(
            name="backupdestination",
            options={
                "ordering": ("name",),
                "verbose_name": "storage",
                "verbose_name_plural": "storages",
            },
        ),
        migrations.AlterField(
            model_name="remoteretentionpolicy",
            name="max_copies_per_target",
            field=models.PositiveIntegerField(
                default=1000,
                help_text=(
                    "Maximum number of revisions retained for one backup device on each FTP "
                    "storage."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(100000)],
                verbose_name="maximum remote revisions per device",
            ),
        ),
        migrations.AddField(
            model_name="backupdestination",
            name="enforce_retention_policy",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Always use this storage's retention profile instead of a device retention "
                    "override."
                ),
            ),
        ),
        migrations.AddField(
            model_name="backupdestination",
            name="is_default",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="backupdestination",
            name="local_retention_policy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="local_storages",
                to="netbox_config_backup.retentionpolicy",
                verbose_name="local retention profile",
            ),
        ),
        migrations.AddField(
            model_name="backupdestination",
            name="remote_retention_policy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="remote_storages",
                to="netbox_config_backup.remoteretentionpolicy",
                verbose_name="FTP retention profile",
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="base_path",
            field=models.CharField(
                blank=True,
                default="netbox-config-backup",
                help_text="Remote directory below which immutable revision copies are stored.",
                max_length=500,
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="connect_timeout",
            field=models.PositiveIntegerField(
                blank=True,
                default=15,
                null=True,
                validators=[MinValueValidator(1), MaxValueValidator(300)],
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="credential_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="backup_destinations",
                to="netbox_config_backup.credentialprofile",
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="host",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="max_artifact_size",
            field=models.PositiveBigIntegerField(
                blank=True,
                default=1073741824,
                help_text="Maximum size of one artifact copied to this destination.",
                null=True,
                validators=[MinValueValidator(1024), MaxValueValidator(10737418240)],
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="max_retries",
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=3,
                null=True,
                validators=[MinValueValidator(0), MaxValueValidator(20)],
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="port",
            field=models.PositiveIntegerField(
                blank=True,
                default=22,
                null=True,
                validators=[MinValueValidator(1), MaxValueValidator(65535)],
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="protocol",
            field=models.CharField(
                choices=[
                    ("local", "Local (primary storage)"),
                    ("sftp", "SFTP (recommended, encrypted)"),
                    ("ftp", "FTP (unencrypted)"),
                ],
                default="sftp",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="retry_delay_minutes",
            field=models.PositiveIntegerField(
                blank=True,
                default=15,
                null=True,
                validators=[MinValueValidator(1), MaxValueValidator(10080)],
            ),
        ),
    ]
