import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def mark_existing_successful_replicas_available(apps, schema_editor):
    RevisionReplica = apps.get_model("netbox_config_backup", "RevisionReplica")
    RevisionReplica.objects.filter(status="success").exclude(remote_path="").update(
        remote_available=True
    )


def mark_existing_replicas_unavailable(apps, schema_editor):
    RevisionReplica = apps.get_model("netbox_config_backup", "RevisionReplica")
    RevisionReplica.objects.update(remote_available=False, remote_deleted_at=None)


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0018_retentionpolicy_max_runs_per_target")]

    operations = [
        migrations.CreateModel(
            name="RemoteRetentionPolicy",
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
                ("keep_all_days", models.PositiveIntegerField(default=30)),
                ("daily_days", models.PositiveIntegerField(default=365)),
                ("weekly_weeks", models.PositiveIntegerField(default=104)),
                ("monthly_months", models.PositiveIntegerField(default=60)),
                ("minimum_changed_revisions", models.PositiveIntegerField(default=12)),
                (
                    "max_copies_per_target",
                    models.PositiveIntegerField(
                        default=1000,
                        help_text=(
                            "Maximum number of remote revisions retained for one backup device; "
                            "physical artifact copies on multiple FTP destinations are not counted "
                            "separately."
                        ),
                        validators=[MinValueValidator(1), MaxValueValidator(100000)],
                        verbose_name="maximum remote revisions per device",
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(
                        through="extras.TaggedItem",
                        to="extras.Tag",
                    ),
                ),
            ],
            options={"ordering": ("name",)},
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="operationalsettings",
            name="remote_retention_scheduler_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="backuptarget",
            name="retention_override",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.retentionpolicy",
                verbose_name="local retention profile",
            ),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="remote_retention_policy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.remoteretentionpolicy",
                verbose_name="FTP retention profile",
                help_text="Leave blank to keep this device's FTP copies indefinitely.",
            ),
        ),
        migrations.AddField(
            model_name="configartifact",
            name="local_available",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="configartifact",
            name="local_deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="revisionreplica",
            name="remote_available",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="revisionreplica",
            name="remote_deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_existing_successful_replicas_available,
            mark_existing_replicas_unavailable,
        ),
    ]
