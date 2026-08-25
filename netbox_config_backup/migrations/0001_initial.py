import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("dcim", "0233_device_render_config_permission"),
        ("extras", "0138_customfieldchoiceset_choice_colors"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("schedule_type", models.CharField(default="daily", max_length=16)),
                ("interval_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("time_of_day", models.TimeField(blank=True, null=True)),
                ("timezone_mode", models.CharField(default="site", max_length=16)),
                ("jitter_minutes", models.PositiveIntegerField(default=0)),
                ("connection_timeout", models.PositiveIntegerField(default=15)),
                ("command_timeout", models.PositiveIntegerField(default=60)),
                ("max_retries", models.PositiveSmallIntegerField(default=3)),
                ("retry_backoff_minutes", models.JSONField(blank=True, default=list)),
                ("store_mode", models.CharField(default="changed_only", max_length=24)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name="BackupTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("driver_override", models.CharField(blank=True, max_length=100)),
                ("next_run_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_change_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(db_index=True, default="never", max_length=24)),
                ("consecutive_failures", models.PositiveIntegerField(default=0)),
                (
                    "device",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="config_backup_target",
                        to="dcim.device",
                    ),
                ),
                (
                    "policy_override",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="target_overrides",
                        to="netbox_config_backup.backuppolicy",
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("device__name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name="ConfigRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                (
                    "revision_uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("normalized_hash", models.CharField(db_index=True, max_length=64)),
                ("normalizer_version", models.CharField(max_length=50)),
                ("driver_id", models.CharField(max_length=100)),
                ("content_changed", models.BooleanField(default=True)),
                ("protected", models.BooleanField(db_index=True, default=False)),
                ("label", models.CharField(blank=True, max_length=200)),
                ("comments", models.TextField(blank=True)),
                (
                    "previous_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="next_revisions",
                        to="netbox_config_backup.configrevision",
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to="netbox_config_backup.backuptarget",
                    ),
                ),
            ],
            options={
                "ordering": ("-created",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name="ConfigArtifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("artifact_type", models.CharField(max_length=100)),
                ("format", models.CharField(default="text", max_length=50)),
                ("storage_key", models.CharField(max_length=1000, unique=True)),
                ("size", models.PositiveBigIntegerField()),
                ("raw_hash", models.CharField(max_length=64)),
                ("normalized_hash", models.CharField(max_length=64)),
                ("is_primary", models.BooleanField(default=False)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="netbox_config_backup.configrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("artifact_type",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="last_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="current_for_targets",
                to="netbox_config_backup.configrevision",
            ),
        ),
        migrations.CreateModel(
            name="BackupRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("source", models.CharField(default="manual", max_length=16)),
                ("scheduled_for", models.DateTimeField(blank=True, null=True)),
                ("dedupe_key", models.CharField(blank=True, max_length=128, null=True)),
                ("queued_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(db_index=True, default="queued", max_length=24)),
                ("attempt_number", models.PositiveSmallIntegerField(default=1)),
                ("error_code", models.CharField(blank=True, db_index=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=1000)),
                ("changed", models.BooleanField(default=False)),
                ("raw_changed", models.BooleanField(default=False)),
                ("job_id", models.UUIDField(blank=True, null=True)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="config_backup_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="runs",
                        to="netbox_config_backup.backuptarget",
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="runs",
                        to="netbox_config_backup.configrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("-queued_at",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name="ConnectionProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("address_preference", models.CharField(default="oob_first", max_length=24)),
                ("host_override", models.CharField(blank=True, max_length=255)),
                (
                    "port",
                    models.PositiveIntegerField(
                        default=22,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(65535),
                        ],
                    ),
                ),
                ("connect_timeout", models.PositiveIntegerField(default=15)),
                ("command_timeout", models.PositiveIntegerField(default=60)),
                ("keepalive", models.PositiveIntegerField(default=30)),
                ("verify_host_key", models.BooleanField(default=True)),
                ("known_hosts_path", models.CharField(blank=True, max_length=500)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="connection_override",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.connectionprofile",
            ),
        ),
        migrations.CreateModel(
            name="CredentialProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("provider_id", models.CharField(max_length=100)),
                ("secret_reference", models.CharField(max_length=500)),
                ("auth_type", models.CharField(default="password", max_length=16)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="credential_override",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.credentialprofile",
            ),
        ),
        migrations.CreateModel(
            name="PlatformMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("driver_id", models.CharField(max_length=100)),
                ("enabled", models.BooleanField(default=True)),
                ("driver_options", models.JSONField(blank=True, default=dict)),
                (
                    "connection_profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="platform_mappings",
                        to="netbox_config_backup.connectionprofile",
                    ),
                ),
                (
                    "credential_profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="platform_mappings",
                        to="netbox_config_backup.credentialprofile",
                    ),
                ),
                (
                    "platform",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="config_backup_mapping",
                        to="dcim.platform",
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("platform__name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name="RetentionPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("keep_all_days", models.PositiveIntegerField(default=7)),
                ("daily_days", models.PositiveIntegerField(default=30)),
                ("weekly_weeks", models.PositiveIntegerField(default=12)),
                ("monthly_months", models.PositiveIntegerField(default=12)),
                ("minimum_changed_revisions", models.PositiveIntegerField(default=10)),
                ("unchanged_run_days", models.PositiveIntegerField(default=90)),
                ("changed_run_days", models.PositiveIntegerField(default=180)),
                ("failed_run_days", models.PositiveIntegerField(default=180)),
                (
                    "tags",
                    taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag"),
                ),
            ],
            options={
                "ordering": ("name",),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(
            model_name="backuptarget",
            name="retention_override",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.retentionpolicy",
            ),
        ),
        migrations.AddField(
            model_name="backuppolicy",
            name="retention_policy",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="backup_policies",
                to="netbox_config_backup.retentionpolicy",
            ),
        ),
        migrations.AddIndex(
            model_name="configrevision",
            index=models.Index(fields=["target", "-created"], name="ncb_revision_target_created"),
        ),
        migrations.AddConstraint(
            model_name="configartifact",
            constraint=models.UniqueConstraint(
                fields=("revision", "artifact_type"), name="ncb_artifact_revision_type"
            ),
        ),
        migrations.AddConstraint(
            model_name="configartifact",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_primary", True)),
                fields=("revision",),
                name="ncb_artifact_one_primary",
            ),
        ),
        migrations.AddIndex(
            model_name="backuprun",
            index=models.Index(fields=["target", "-queued_at"], name="ncb_run_target_queued"),
        ),
        migrations.AddConstraint(
            model_name="backuprun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("dedupe_key__isnull", False)),
                fields=("target", "dedupe_key"),
                name="ncb_run_target_dedupe",
            ),
        ),
        migrations.AddConstraint(
            model_name="backuprun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ("queued", "running"))),
                fields=("target",),
                name="ncb_run_one_active_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="backuppolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("interval_minutes__isnull", False),
                        ("schedule_type", "interval"),
                        ("time_of_day__isnull", True),
                    ),
                    models.Q(
                        ("interval_minutes__isnull", True),
                        ("schedule_type", "daily"),
                        ("time_of_day__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="ncb_policy_schedule_fields",
            ),
        ),
    ]
