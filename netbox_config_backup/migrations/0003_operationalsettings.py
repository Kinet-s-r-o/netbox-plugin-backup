from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import netbox.models.deletion
import taggit.managers
import utilities.json


def create_default_settings(apps, schema_editor):
    OperationalSettings = apps.get_model(
        "netbox_config_backup", "OperationalSettings"
    )
    OperationalSettings.objects.get_or_create(
        singleton=True,
        defaults={
            "retention_scheduler_enabled": False,
            "retention_scheduler_batch_size": 25,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_config_backup", "0002_storedcredential"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(auto_now_add=True, null=True),
                ),
                (
                    "last_updated",
                    models.DateTimeField(auto_now=True, null=True),
                ),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                (
                    "singleton",
                    models.BooleanField(default=True, editable=False, unique=True),
                ),
                (
                    "retention_scheduler_enabled",
                    models.BooleanField(default=False),
                ),
                (
                    "retention_scheduler_batch_size",
                    models.PositiveSmallIntegerField(
                        default=25,
                        validators=[
                            MinValueValidator(1),
                            MaxValueValidator(1000),
                        ],
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
            options={
                "verbose_name": "operational settings",
                "verbose_name_plural": "operational settings",
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.RunPython(
            create_default_settings,
            migrations.RunPython.noop,
        ),
    ]
