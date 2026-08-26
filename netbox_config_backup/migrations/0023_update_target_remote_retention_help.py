from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0022_storage_constraints"),
    ]

    operations = [
        migrations.AlterField(
            model_name="backuptarget",
            name="remote_retention_policy",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Leave blank to use each FTP storage profile. Copies are kept indefinitely "
                    "only on a storage which also has no profile."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.remoteretentionpolicy",
                verbose_name="FTP retention profile",
            ),
        ),
    ]
