"""Exercise encrypted, write-only credentials through the real NetBox UI forms."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.models import CredentialProfile, StoredCredential

prefix = f"ncb-credential-{uuid4().hex[:8]}"
plaintext = f"initial-{uuid4().hex}"
rotated_plaintext = f"rotated-{uuid4().hex}"

user = get_user_model().objects.create_superuser(
    username=f"{prefix}-admin",
    password=uuid4().hex,
)
client = Client()
client.force_login(user)

add_url = reverse("plugins:netbox_config_backup:credentialprofile_add")
response = client.post(
    add_url,
    {
        "name": prefix,
        "provider_id": "encrypted_database",
        "secret_reference": "",
        "auth_type": "password",
        "username": "backup-user",
        "password": plaintext,
        "password_confirm": plaintext,
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors

profile = CredentialProfile.objects.get(name=prefix)
stored = StoredCredential.objects.get(profile=profile)
assert profile.secret_reference == f"db://{stored.reference}"
assert plaintext.encode() not in bytes(stored.ciphertext)
assert stored.username == "backup-user"

material = secret_provider_registry.get(profile.provider_id).resolve(profile.secret_reference)
assert material.username == "backup-user"
assert material.password == plaintext

for url in (
    reverse("plugins:netbox_config_backup:credentialprofile_list"),
    profile.get_absolute_url(),
    reverse(
        "plugins:netbox_config_backup:credentialprofile_edit",
        kwargs={"pk": profile.pk},
    ),
):
    response = client.get(url)
    assert response.status_code == 200
    assert plaintext.encode() not in response.content

original_ciphertext = bytes(stored.ciphertext)
original_rotated_at = stored.rotated_at
edit_url = reverse(
    "plugins:netbox_config_backup:credentialprofile_edit",
    kwargs={"pk": profile.pk},
)
response = client.post(
    edit_url,
    {
        "name": prefix,
        "provider_id": "encrypted_database",
        "secret_reference": profile.secret_reference,
        "auth_type": "password",
        "username": "renamed-backup-user",
        "password": "",
        "password_confirm": "",
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors
stored.refresh_from_db()
assert bytes(stored.ciphertext) == original_ciphertext
assert stored.rotated_at == original_rotated_at
assert stored.username == "renamed-backup-user"

response = client.post(
    edit_url,
    {
        "name": prefix,
        "provider_id": "encrypted_database",
        "secret_reference": profile.secret_reference,
        "auth_type": "password",
        "username": "renamed-backup-user",
        "password": rotated_plaintext,
        "password_confirm": rotated_plaintext,
    },
)
assert response.status_code == 302, response.context and response.context["form"].errors
stored.refresh_from_db()
assert bytes(stored.ciphertext) != original_ciphertext
material = secret_provider_registry.get(profile.provider_id).resolve(profile.secret_reference)
assert material.password == rotated_plaintext

limited_user = get_user_model().objects.create_user(username=f"{prefix}-limited")
limited_user.user_permissions.add(
    Permission.objects.get(
        content_type__app_label="netbox_config_backup",
        codename="add_credentialprofile",
    )
)
limited_client = Client()
limited_client.force_login(limited_user)
response = limited_client.post(
    add_url,
    {
        "name": f"{prefix}-forbidden",
        "provider_id": "encrypted_database",
        "auth_type": "password",
        "username": "forbidden-user",
        "password": plaintext,
        "password_confirm": plaintext,
    },
)
assert response.status_code == 403
assert not CredentialProfile.objects.filter(name=f"{prefix}-forbidden").exists()

print(
    {
        "credential_profile": profile.pk,
        "provider": profile.provider_id,
        "password_is_write_only": True,
        "blank_edit_preserved_password": True,
        "rotation_succeeded": True,
        "permission_boundary_verified": True,
    }
)
