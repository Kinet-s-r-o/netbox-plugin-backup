"""Read-only deployment check for the encrypted credential UI."""

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.choices import DestinationProtocolChoices
from netbox_config_backup.credentials.encrypted_database import DatabaseCredentialCipher
from netbox_config_backup.forms import (
    BackupDestinationForm,
    BackupPolicyForm,
    BackupTargetForm,
    ConnectionProfileForm,
    CredentialProfileForm,
    PlatformMappingForm,
    QuickSetupForm,
    RemoteRetentionPolicyForm,
    RetentionPolicyForm,
)
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupTarget,
    RemoteRetentionPolicy,
    RetentionPolicy,
    SftpReceiverProfile,
)

key, key_version = DatabaseCredentialCipher().active_key()
assert len(key) == 32

form = CredentialProfileForm()
assert all(name in form.fields for name in ("username", "password", "password_confirm"))
assert any(value == "encrypted_database" for value, _label in form.fields["provider_id"].choices)
assert all(value != "vault_kv2" for value, _label in form.fields["provider_id"].choices)

destination_form = BackupDestinationForm()
assert "protocol" not in destination_form.fields
assert destination_form.instance.protocol == "ftp"
assert destination_form.fields["port"].initial == 21
assert "remote_retention_policy" in destination_form.fields
assert "enforce_retention_policy" in destination_form.fields
assert "local_retention_policy" not in destination_form.fields

target_form = BackupTargetForm()
assert "use each FTP storage profile" in target_form.fields["remote_retention_policy"].help_text
assert (
    "enforced Local storage profile always wins"
    in target_form.fields["retention_override"].help_text
)

assert "Keep every local revision" in RetentionPolicyForm().fields["keep_all_days"].help_text
assert (
    "per device and FTP storage"
    in RemoteRetentionPolicyForm().fields["max_copies_per_target"].help_text
)
assert "JSON list of delays" in BackupPolicyForm().fields["retry_backoff_minutes"].help_text
assert "NetBox device address" in ConnectionProfileForm().fields["address_preference"].help_text
assert "driver-specific settings" in PlatformMappingForm().fields["driver_options"].help_text
assert (
    PlatformMappingForm().fields["receiver_profile"].label
    == "Default device upload receiver"
)
assert QuickSetupForm().fields["receiver_profile"].label == "Device upload receiver"
assert RetentionPolicy._meta.verbose_name_plural == "local retention profiles"
assert RemoteRetentionPolicy._meta.verbose_name_plural == "FTP retention profiles"
assert BackupPolicy._meta.verbose_name_plural == "backup policies"
assert SftpReceiverProfile._meta.verbose_name_plural == "device upload receivers"

local_storages = BackupDestination.objects.filter(
    protocol=DestinationProtocolChoices.LOCAL,
    is_default=True,
)
assert local_storages.count() == 1
local_storage = local_storages.get()
assert local_storage.enabled is True
assert local_storage.auto_replicate is False

user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert user is not None
client = Client()
client.force_login(user)
response = client.get(reverse("plugins:netbox_config_backup:credentialprofile_add"))
assert response.status_code == 200
for field_name in (b"username", b"password", b"password_confirm"):
    assert b'name="' + field_name + b'"' in response.content

response = client.get(reverse("plugins:netbox_config_backup:backuptarget_add"))
assert response.status_code == 200
assert b"Add device" in response.content
assert b"Save &amp; test connection" in response.content

response = client.get(reverse("plugins:netbox_config_backup:advanced_settings"))
assert response.status_code == 200
assert b"Platform mappings" in response.content
assert b"FTP destination" not in response.content
assert b"Add device" not in response.content
assert b"Device defaults" in response.content
assert b"Schedules and retention" in response.content
assert b"Security and vendor-specific setup" in response.content
assert b"Automation" in response.content
assert b"Open help" in response.content
assert b"netbox_config_backup/settings.css" in response.content
assert b'type="hidden" name="retention_scheduler_batch_size"' in response.content
assert b"Maximum cleanup jobs" not in response.content
assert b"Open examples" not in response.content
for field_name in (b"events_enabled", b"notify_on_every_failure"):
    assert b'name="' + field_name + b'"' in response.content
assert b"Prometheus metrics" not in response.content

help_response = client.get(reverse("plugins:netbox_config_backup:help"))
assert help_response.status_code == 200
for expected in (
    b"Recommended setup",
    b"Create connection and credential profiles",
    b"How a backup moves",
    b"revision creation",
    b"Local retention profiles",
    b"FTP retention profiles",
    b"Local profile precedence",
    b"FTP profile precedence",
    b"FTP storage or device upload receiver?",
    b"HOST_KEY_UNKNOWN",
):
    assert expected in help_response.content, expected
for obsolete in (
    b"Create access profiles",
    b"storage mode",
    b"Most installations use all six reusable profile types",
):
    assert obsolete not in help_response.content, obsolete
assert b'name="password"' not in help_response.content
assert b'name="secret_reference"' not in help_response.content

for list_name, expected_title in (
    ("retentionpolicy_list", b"Local Retention Profiles | NetBox"),
    ("remoteretentionpolicy_list", b"FTP Retention Profiles | NetBox"),
    ("backuppolicy_list", b"Backup Policies | NetBox"),
    ("sftpreceiverprofile_list", b"Device Upload Receivers | NetBox"),
):
    response = client.get(reverse(f"plugins:netbox_config_backup:{list_name}"))
    assert response.status_code == 200
    assert b"<title>" + expected_title + b"</title>" in response.content

response = client.get(reverse("plugins:netbox_config_backup:backupdestination_add"))
assert response.status_code == 200
assert b'name="protocol"' not in response.content
assert b'name="remote_retention_policy"' in response.content
assert b'name="enforce_retention_policy"' in response.content
assert b'name="local_retention_policy"' not in response.content

response = client.get(reverse("plugins:netbox_config_backup:backupdestination_list"))
assert response.status_code == 200
assert b"Storages" in response.content
assert local_storage.name.encode() in response.content
assert b"Local" in response.content
assert b"Default" in response.content

response = client.get(local_storage.get_absolute_url())
assert response.status_code == 200
assert b"Default primary storage" in response.content
assert b"cannot be deleted" in response.content
assert b"Test FTP storage" not in response.content

target = BackupTarget.objects.select_related("remote_retention_policy").first()
if target is not None:
    response = client.get(target.get_absolute_url())
    assert response.status_code == 200
    if target.remote_retention_policy_id:
        assert b"an enforced storage profile wins" in response.content
    else:
        assert b"Uses each FTP storage profile" in response.content

response = client.get(
    reverse(
        "plugins:netbox_config_backup:backupdestination_edit",
        kwargs={"pk": local_storage.pk},
    )
)
assert response.status_code == 200
assert b'name="local_retention_policy"' in response.content
assert b'name="enforce_retention_policy"' in response.content
for transport_field in (b"host", b"port", b"credential_profile", b"base_path"):
    assert b'name="' + transport_field + b'"' not in response.content

response = client.get(
    reverse(
        "plugins:netbox_config_backup:backupdestination_delete",
        kwargs={"pk": local_storage.pk},
    )
)
assert response.status_code == 404

response = client.get(reverse("plugins:netbox_config_backup:examples"))
assert response.status_code == 200
assert b"HashiCorp Vault" not in response.content
assert b"Amazon S3" not in response.content

print(
    {
        "master_key_valid": True,
        "key_version": key_version,
        "credential_form_status": response.status_code,
        "encrypted_fields_present": True,
        "quick_setup_present": True,
        "advanced_settings_present": True,
        "help_present": True,
        "runtime_controls_present": True,
        "storage_profiles_ui": True,
        "protected_local_storage": True,
    }
)
