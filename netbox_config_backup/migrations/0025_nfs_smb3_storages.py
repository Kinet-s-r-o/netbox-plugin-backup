import django.db.models.deletion
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0024_consistent_ui_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="backupdestination",
            name="mount_path",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Absolute directory where Docker or the operating system mounted the NFS or "
                    "SMB3 share. It must be below an allowed network-storage mount root."
                ),
                max_length=500,
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
                    ("nfs", "NFS mount"),
                    ("smb", "SMB3 / Samba mount"),
                ],
                default="sftp",
                max_length=8,
            ),
        ),
        migrations.AlterModelOptions(
            name="remoteretentionpolicy",
            options={
                "ordering": ("name",),
                "verbose_name": "remote retention profile",
                "verbose_name_plural": "remote retention profiles",
            },
        ),
        migrations.AlterField(
            model_name="remoteretentionpolicy",
            name="max_copies_per_target",
            field=models.PositiveIntegerField(
                default=1000,
                help_text=(
                    "Maximum number of revisions retained for one backup device on each remote "
                    "storage."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(100000)],
                verbose_name="maximum remote revisions per device",
            ),
        ),
        migrations.AlterField(
            model_name="backupdestination",
            name="remote_retention_policy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="remote_storages",
                to="netbox_config_backup.remoteretentionpolicy",
                verbose_name="remote retention profile",
            ),
        ),
        migrations.AlterField(
            model_name="backuptarget",
            name="remote_retention_policy",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Leave blank to use each remote storage profile. Copies are kept indefinitely "
                    "only on a storage which also has no profile."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="target_overrides",
                to="netbox_config_backup.remoteretentionpolicy",
                verbose_name="remote retention profile",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="backupdestination",
            name="ncb_destination_local_invariants",
        ),
        migrations.RemoveConstraint(
            model_name="backupdestination",
            name="ncb_destination_remote_transport",
        ),
        migrations.RemoveConstraint(
            model_name="backupdestination",
            name="ncb_destination_enforced_policy",
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(protocol="local")
                    | models.Q(
                        allow_insecure_ftp=False,
                        auto_replicate=False,
                        base_path="",
                        connect_timeout__isnull=True,
                        credential_profile__isnull=True,
                        enabled=True,
                        host="",
                        integrity_audit_enabled=False,
                        max_artifact_size__isnull=True,
                        max_retries__isnull=True,
                        mount_path="",
                        port__isnull=True,
                        retry_delay_minutes__isnull=True,
                    )
                ),
                name="ncb_destination_local_invariants",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(protocol="local")
                    | (
                        models.Q(protocol__in=("nfs", "smb"))
                        & models.Q(
                            connect_timeout__isnull=True,
                            credential_profile__isnull=True,
                            host="",
                            max_artifact_size__isnull=False,
                            max_retries__isnull=False,
                            port__isnull=True,
                            retry_delay_minutes__isnull=False,
                        )
                        & ~models.Q(mount_path="")
                        & ~models.Q(base_path="")
                    )
                    | (
                        ~models.Q(protocol__in=("nfs", "smb"))
                        & models.Q(
                            connect_timeout__isnull=False,
                            credential_profile__isnull=False,
                            max_artifact_size__isnull=False,
                            max_retries__isnull=False,
                            port__isnull=False,
                            retry_delay_minutes__isnull=False,
                        )
                        & ~models.Q(host="")
                        & ~models.Q(base_path="")
                        & models.Q(mount_path="")
                    )
                ),
                name="ncb_destination_remote_transport",
            ),
        ),
        migrations.AddConstraint(
            model_name="backupdestination",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(enforce_retention_policy=False)
                    | models.Q(
                        local_retention_policy__isnull=False,
                        protocol="local",
                    )
                    | models.Q(
                        protocol__in=("ftp", "sftp", "nfs", "smb"),
                        remote_retention_policy__isnull=False,
                    )
                ),
                name="ncb_destination_enforced_policy",
            ),
        ),
    ]
