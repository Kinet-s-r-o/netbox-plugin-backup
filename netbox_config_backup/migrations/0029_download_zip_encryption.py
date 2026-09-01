import uuid

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_config_backup", "0028_alter_connectionprofile_address_preference"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsettings",
            name="download_zip_encryption_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Wrap downloaded backups in an AES-256 encrypted ZIP archive.",
            ),
        ),
        migrations.CreateModel(
            name="DownloadEncryptionSecret",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "singleton",
                    models.BooleanField(default=True, editable=False, unique=True),
                ),
                (
                    "reference",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("ciphertext", models.BinaryField(editable=False)),
                ("nonce", models.BinaryField(editable=False)),
                ("key_version", models.CharField(editable=False, max_length=50)),
                (
                    "rotated_at",
                    models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
            ],
            options={"default_permissions": ()},
        ),
    ]
