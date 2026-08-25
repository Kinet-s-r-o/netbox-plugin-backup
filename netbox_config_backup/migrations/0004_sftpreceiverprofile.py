import django.core.validators
import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0003_operationalsettings"),
    ]

    operations = [
        migrations.CreateModel(
            name="SftpReceiverProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("mode", models.CharField(choices=[("direct", "Direct from device"), ("reverse_tunnel", "Reverse SSH tunnel")], default="direct", max_length=24)),
                ("listen_host", models.CharField(default="0.0.0.0", max_length=255)),
                ("listen_port", models.PositiveIntegerField(default=2022, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(65535)])),
                ("advertised_host", models.CharField(blank=True, help_text="Address which devices use in direct mode.", max_length=255)),
                ("advertised_port", models.PositiveIntegerField(default=2022, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(65535)])),
                ("bridge_host", models.CharField(default="config-backup-receiver", help_text="Receiver address reachable from the backup worker.", max_length=255)),
                ("bridge_port", models.PositiveIntegerField(default=2022, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(65535)])),
                ("remote_bind_host", models.GenericIPAddressField(default="127.0.0.1")),
                ("remote_bind_port", models.PositiveIntegerField(default=2222, validators=[django.core.validators.MinValueValidator(1024), django.core.validators.MaxValueValidator(65535)])),
                ("upload_directory", models.CharField(default="incoming", max_length=100)),
                ("export_timeout", models.PositiveIntegerField(default=180, validators=[django.core.validators.MinValueValidator(10), django.core.validators.MaxValueValidator(3600)])),
                ("max_upload_size", models.PositiveBigIntegerField(default=104857600, validators=[django.core.validators.MinValueValidator(1024), django.core.validators.MaxValueValidator(1073741824)])),
                (
                    "tags",
                    taggit.managers.TaggableManager(
                        through="extras.TaggedItem",
                        to="extras.Tag",
                    ),
                ),
                ("credential_profile", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sftp_receiver_profiles", to="netbox_config_backup.credentialprofile")),
            ],
            options={"ordering": ("name",)},
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="platformmapping",
            name="receiver_profile",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="platform_mappings", to="netbox_config_backup.sftpreceiverprofile"),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="receiver_override",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="target_overrides", to="netbox_config_backup.sftpreceiverprofile"),
        ),
    ]
