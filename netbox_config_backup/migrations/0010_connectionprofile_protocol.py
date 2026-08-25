from django.db import migrations, models
from django.db.models import Q


def infer_existing_protocols(apps, schema_editor):
    ConnectionProfile = apps.get_model("netbox_config_backup", "ConnectionProfile")
    ConnectionProfile.objects.filter(Q(port=23) | Q(name__icontains="telnet")).update(
        protocol="telnet"
    )
    ConnectionProfile.objects.filter(name__icontains="ssh").exclude(protocol="telnet").update(
        protocol="ssh"
    )


class Migration(migrations.Migration):
    dependencies = [("netbox_config_backup", "0009_remove_operationalsettings_metrics_enabled")]

    operations = [
        migrations.AddField(
            model_name="connectionprofile",
            name="protocol",
            field=models.CharField(
                choices=[
                    ("auto", "Automatic from driver and port"),
                    ("ssh", "SSH"),
                    ("telnet", "Telnet"),
                ],
                default="auto",
                help_text=(
                    "Select SSH or Telnet when the same device family supports both transports."
                ),
                max_length=16,
            ),
        ),
        migrations.RunPython(infer_existing_protocols, migrations.RunPython.noop),
    ]
