"""Read-only deployment check for the encrypted credential UI."""

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.credentials.encrypted_database import DatabaseCredentialCipher
from netbox_config_backup.forms import BackupDestinationForm, CredentialProfileForm

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
assert b"FTP destination" in response.content
assert b"Add device" not in response.content
assert b"Advanced profiles" in response.content
assert b"Automation" in response.content
assert b'type="hidden" name="retention_scheduler_batch_size"' in response.content
assert b"Maximum cleanup jobs" not in response.content
assert b"Open examples" not in response.content
for field_name in (b"events_enabled", b"notify_on_every_failure"):
    assert b'name="' + field_name + b'"' in response.content
assert b"Prometheus metrics" not in response.content

response = client.get(reverse("plugins:netbox_config_backup:backupdestination_add"))
assert response.status_code == 200
assert b'name="protocol"' not in response.content

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
        "runtime_controls_present": True,
        "ftp_only_destination_ui": True,
    }
)
