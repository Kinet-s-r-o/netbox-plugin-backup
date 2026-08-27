from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0023_update_target_remote_retention_help"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="backuppolicy",
            options={
                "ordering": ("name",),
                "verbose_name": "backup policy",
                "verbose_name_plural": "backup policies",
            },
        ),
        migrations.AlterModelOptions(
            name="remoteretentionpolicy",
            options={
                "ordering": ("name",),
                "verbose_name": "FTP retention profile",
                "verbose_name_plural": "FTP retention profiles",
            },
        ),
        migrations.AlterModelOptions(
            name="retentionpolicy",
            options={
                "ordering": ("name",),
                "verbose_name": "local retention profile",
                "verbose_name_plural": "local retention profiles",
            },
        ),
        migrations.AlterModelOptions(
            name="sftpreceiverprofile",
            options={
                "ordering": ("name",),
                "verbose_name": "device upload receiver",
                "verbose_name_plural": "device upload receivers",
            },
        ),
    ]
