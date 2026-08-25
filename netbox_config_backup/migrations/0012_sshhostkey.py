from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import netbox.models.deletion
import taggit.managers
import utilities.json


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0011_unify_siae_drivers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SSHHostKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("custom_field_data", models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
                ("address", models.CharField(max_length=255)),
                ("port", models.PositiveIntegerField(default=22, validators=[MinValueValidator(1), MaxValueValidator(65535)])),
                ("key_type", models.CharField(max_length=64)),
                ("public_key", models.TextField()),
                ("fingerprint_sha256", models.CharField(max_length=100)),
                ("fingerprint_md5", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("pending", "Pending approval"), ("trusted", "Trusted"), ("rejected", "Rejected")], db_index=True, default="pending", max_length=16)),
                ("first_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_config_backup_host_keys", to=settings.AUTH_USER_MODEL)),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ssh_host_keys", to="netbox_config_backup.backuptarget")),
                ("tags", taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag")),
            ],
            options={"ordering": ("-last_seen_at",)},
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name="sshhostkey",
            constraint=models.UniqueConstraint(fields=("target", "address", "port", "key_type", "fingerprint_sha256"), name="ncb_sshkey_target_identity"),
        ),
    ]
