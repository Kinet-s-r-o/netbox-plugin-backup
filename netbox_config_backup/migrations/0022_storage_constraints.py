from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0021_create_default_local_storage")]

    operations = [
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.UniqueConstraint(
                condition=models.Q(("protocol", "local")),
                fields=("protocol",),
                name="ncb_destination_one_local",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("is_default", True),
                        ("protocol", "local"),
                        ("remote_retention_policy__isnull", True),
                    )
                    | (
                        ~models.Q(("protocol", "local"))
                        & models.Q(
                            ("is_default", False),
                            ("local_retention_policy__isnull", True),
                        )
                    )
                ),
                name="ncb_destination_typed_retention",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(("protocol", "local"))
                    | models.Q(
                        ("allow_insecure_ftp", False),
                        ("auto_replicate", False),
                        ("base_path", ""),
                        ("connect_timeout__isnull", True),
                        ("credential_profile__isnull", True),
                        ("enabled", True),
                        ("host", ""),
                        ("integrity_audit_enabled", False),
                        ("max_artifact_size__isnull", True),
                        ("max_retries__isnull", True),
                        ("port__isnull", True),
                        ("retry_delay_minutes__isnull", True),
                    )
                ),
                name="ncb_destination_local_invariants",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("protocol", "local"))
                    | (
                        models.Q(
                            ("connect_timeout__isnull", False),
                            ("credential_profile__isnull", False),
                            ("max_artifact_size__isnull", False),
                            ("max_retries__isnull", False),
                            ("port__isnull", False),
                            ("retry_delay_minutes__isnull", False),
                        )
                        & ~models.Q(("host", ""))
                        & ~models.Q(("base_path", ""))
                    )
                ),
                name="ncb_destination_remote_transport",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("enforce_retention_policy", False))
                    | models.Q(
                        ("local_retention_policy__isnull", False),
                        ("protocol", "local"),
                    )
                    | models.Q(
                        ("protocol__in", ("ftp", "sftp")),
                        ("remote_retention_policy__isnull", False),
                    )
                ),
                name="ncb_destination_enforced_policy",
            ),
        ),
    ]
