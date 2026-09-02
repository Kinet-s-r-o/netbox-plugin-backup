"""Read-only deployment check for the encrypted credential UI."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.choices import DestinationProtocolChoices, SSHHostKeyPolicyChoices
from netbox_config_backup.credentials.encrypted_database import DatabaseCredentialCipher
from netbox_config_backup.forms import (
    BackupDestinationForm,
    BackupPolicyForm,
    BackupTargetFilterForm,
    BackupTargetForm,
    ConnectionProfileForm,
    CredentialProfileForm,
    InterfaceLanguageSettingsForm,
    PlatformMappingForm,
    QuickSetupForm,
    RemoteRetentionPolicyForm,
    RetentionPolicyForm,
)
from netbox_config_backup.forms_filters import BackupTargetFilterForm as SplitBackupTargetFilterForm
from netbox_config_backup.forms_setup import QuickSetupForm as SplitQuickSetupForm
from netbox_config_backup.forms_storage import BackupDestinationForm as SplitBackupDestinationForm
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupTarget,
    RemoteRetentionPolicy,
    RetentionPolicy,
    SftpReceiverProfile,
)
from netbox_config_backup.services.ui_language import SESSION_KEY

key, key_version = DatabaseCredentialCipher().active_key()
assert len(key) == 32
assert QuickSetupForm is SplitQuickSetupForm
assert BackupDestinationForm is SplitBackupDestinationForm
assert BackupTargetFilterForm is SplitBackupTargetFilterForm

form = CredentialProfileForm()
assert all(name in form.fields for name in ("username", "password", "password_confirm"))
assert any(value == "encrypted_database" for value, _label in form.fields["provider_id"].choices)
assert all(value != "vault_kv2" for value, _label in form.fields["provider_id"].choices)

destination_form = BackupDestinationForm()
assert "protocol" in destination_form.fields
assert destination_form.instance.protocol == "ftp"
assert destination_form.fields["port"].initial == 21
assert "remote_retention_policy" in destination_form.fields
assert "enforce_retention_policy" in destination_form.fields
assert "local_retention_policy" not in destination_form.fields

target_form = BackupTargetForm()
assert "use each remote storage profile" in target_form.fields["remote_retention_policy"].help_text
assert (
    "enforced Local storage profile always wins"
    in target_form.fields["retention_override"].help_text
)

assert "Keep every local revision" in RetentionPolicyForm().fields["keep_all_days"].help_text
assert (
    "per device and remote storage"
    in RemoteRetentionPolicyForm().fields["max_copies_per_target"].help_text
)
assert "JSON list of delays" in BackupPolicyForm().fields["retry_backoff_minutes"].help_text
assert "NetBox device address" in ConnectionProfileForm().fields["address_preference"].help_text
host_key_choices = {
    str(value): str(label)
    for value, label in ConnectionProfileForm().fields["host_key_policy"].choices
}
assert set(host_key_choices) == {
    SSHHostKeyPolicyChoices.STRICT,
    SSHHostKeyPolicyChoices.TRUST_ON_FIRST_USE,
    SSHHostKeyPolicyChoices.DISABLED,
}
assert "host_key_policy" in QuickSetupForm().fields
assert "verify_host_key" not in QuickSetupForm().fields
assert "known_hosts_path" not in ConnectionProfileForm().fields
disabled_host_key_form = ConnectionProfileForm(
    data={
        "name": f"ncb-disabled-host-key-{uuid4().hex}",
        "protocol": "ssh",
        "address_preference": "oob_first",
        "port": 22,
        "connect_timeout": 15,
        "command_timeout": 60,
        "keepalive": 30,
        "host_key_policy": SSHHostKeyPolicyChoices.DISABLED,
        "known_hosts_path": "/must/be/cleared",
    }
)
assert disabled_host_key_form.is_valid(), disabled_host_key_form.errors
disabled_host_key_profile = disabled_host_key_form.save(commit=False)
assert disabled_host_key_profile.verify_host_key is False
assert disabled_host_key_profile.auto_trust_first_host_key is False
assert disabled_host_key_profile.known_hosts_path == ""
assert "driver-specific settings" in PlatformMappingForm().fields["driver_options"].help_text
assert PlatformMappingForm().fields["receiver_profile"].label == "Default device upload receiver"
assert QuickSetupForm().fields["receiver_profile"].label == "Device upload receiver"
assert RetentionPolicy._meta.verbose_name_plural == "local retention profiles"
assert RemoteRetentionPolicy._meta.verbose_name_plural == "remote retention profiles"
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
session = client.session
session[SESSION_KEY] = "en"
session.save()
response = client.get(reverse("plugins:netbox_config_backup:credentialprofile_add"))
assert response.status_code == 200
for field_name in (b"username", b"password", b"password_confirm"):
    assert b'name="' + field_name + b'"' in response.content

response = client.get(reverse("plugins:netbox_config_backup:backuptarget_add"))
assert response.status_code == 200
for field_name in (
    b"device",
    b"enabled",
    b"policy_override",
    b"retention_override",
    b"remote_retention_policy",
    b"credential_override",
    b"connection_override",
    b"receiver_override",
    b"driver_override",
    b"driver_options_override",
):
    assert b'name="' + field_name + b'"' in response.content
assert b"Save &amp; test connection" not in response.content

response = client.get(reverse("plugins:netbox_config_backup:backuptarget_quick_setup"))
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
assert b"Plugin language" in response.content
assert b'name="ui_language"' in response.content
assert b'name="download_zip_encryption_enabled"' in response.content
assert b'name="download_zip_password"' in response.content
assert b'name="download_zip_password_confirm"' in response.content
assert b"Protected ZIP downloads" in response.content
settings_stylesheet_url = reverse("plugins:netbox_config_backup:settings_stylesheet")
assert f'href="{settings_stylesheet_url}"'.encode() in response.content
stylesheet_response = client.get(settings_stylesheet_url)
assert stylesheet_response.status_code == 200
assert stylesheet_response.headers["Content-Type"].startswith("text/css")
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
    b"Viewing and downloading backups",
    b"revision creation",
    b"Local retention profiles",
    b"Remote retention profiles",
    b"Local profile precedence",
    b"Remote profile precedence",
    b"Remote storage or device upload receiver?",
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

slovak_client = Client()
slovak_client.force_login(user)
slovak_response = slovak_client.get(
    reverse("plugins:netbox_config_backup:help") + "?language=sk",
    follow=True,
)
assert slovak_response.status_code == 200
assert slovak_response.headers["Content-Language"] == "sk"
assert "Odporúčané nastavenie".encode() in slovak_response.content
assert "Jazyk pomocníka".encode() in slovak_response.content

slovak_settings_response = slovak_client.get(
    reverse("plugins:netbox_config_backup:advanced_settings")
)
assert slovak_settings_response.status_code == 200
assert slovak_settings_response.headers["Content-Language"] == "sk"
assert "Predvolené nastavenia zariadení".encode() in slovak_settings_response.content
assert "Automatické čistenie".encode() in slovak_settings_response.content

language_choices = {
    str(value): str(label)
    for value, label in InterfaceLanguageSettingsForm().fields["ui_language"].choices
}
assert language_choices["en"] == "English"
assert language_choices["sk"] == "Slovenčina"

for list_name, expected_title in (
    ("retentionpolicy_list", b"Local Retention Profiles | NetBox"),
    ("remoteretentionpolicy_list", b"Remote Retention Profiles | NetBox"),
    ("backuppolicy_list", b"Backup Policies | NetBox"),
    ("sftpreceiverprofile_list", b"Device Upload Receivers | NetBox"),
):
    response = client.get(reverse(f"plugins:netbox_config_backup:{list_name}"))
    assert response.status_code == 200
    assert b"<title>" + expected_title + b"</title>" in response.content

response = client.get(reverse("plugins:netbox_config_backup:backupdestination_add"))
assert response.status_code == 200
assert b'name="protocol"' in response.content
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
        assert b"Uses each remote storage profile" in response.content

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
