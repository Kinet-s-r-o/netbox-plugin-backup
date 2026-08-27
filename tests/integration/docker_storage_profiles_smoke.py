"""Exercise protected Local storage and configurable FTP storage profiles.

Run this only after the plugin migrations have been applied. The smoke test never
changes the protected Local row. It creates isolated retention profiles and one
disabled FTP storage, then removes those temporary records in ``finally``.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import Client
from django.urls import reverse
from netbox.api.viewsets import NetBoxModelViewSet
from users.models import User

from netbox_config_backup.api.views import BackupDestinationViewSet
from netbox_config_backup.choices import DestinationProtocolChoices
from netbox_config_backup.forms import BackupDestinationForm
from netbox_config_backup.models import (
    BackupDestination,
    CredentialProfile,
    RemoteRetentionPolicy,
    RetentionPolicy,
)
from netbox_config_backup.services.retention import (
    effective_local_retention_policy,
    effective_remote_retention_policy,
    local_retention_policy_source,
    remote_retention_policy_source,
)

prefix = f"ncb-storage-smoke-{uuid4().hex[:8]}"
local_storage_policy = device_local_policy = None
ftp_storage_policy = device_ftp_policy = None
ftp_storage = None

local_rows = BackupDestination.objects.filter(protocol=DestinationProtocolChoices.LOCAL)
assert local_rows.count() == 1, "Migration must provision exactly one Local storage."
local_storage = local_rows.select_related("local_retention_policy").get()
assert local_storage.is_default is True
assert local_storage.enabled is True
assert local_storage.auto_replicate is False
assert local_storage.host == ""
assert local_storage.port is None
assert local_storage.credential_profile_id is None

# Execute the delete probe inside a transaction which is always rolled back. The
# live Local row remains safe even if a future regression removes the guard.
deletion_blocked = False
with transaction.atomic():
    try:
        local_storage.delete()
    except ProtectedError:
        deletion_blocked = True
    finally:
        transaction.set_rollback(True)
assert deletion_blocked, "The default Local storage was deletable."
local_storage.refresh_from_db()
assert BackupDestination.objects.filter(pk=local_storage.pk).exists()

credential = CredentialProfile.objects.filter(
    provider_id="encrypted_database",
    auth_type="password",
).first()
administrator = User.objects.filter(is_superuser=True, is_active=True).first()
assert credential is not None, "Storage smoke requires one encrypted password profile."
assert administrator is not None, "Storage smoke requires an active superuser."

try:
    local_storage_policy = RetentionPolicy.objects.create(name=f"{prefix}-local-storage")
    device_local_policy = RetentionPolicy.objects.create(name=f"{prefix}-local-device")
    ftp_storage_policy = RemoteRetentionPolicy.objects.create(name=f"{prefix}-ftp-storage")
    device_ftp_policy = RemoteRetentionPolicy.objects.create(name=f"{prefix}-ftp-device")

    create_form = BackupDestinationForm(
        data={
            "name": f"{prefix}-ftp",
            "enabled": "",
            "auto_replicate": "",
            "remote_retention_policy": str(ftp_storage_policy.pk),
            "enforce_retention_policy": "on",
            "allow_insecure_ftp": "on",
            "host": "ftp.invalid",
            "port": "21",
            "base_path": f"netbox-config-backup/{prefix}",
            "credential_profile": str(credential.pk),
            "connect_timeout": "15",
            "max_retries": "3",
            "retry_delay_minutes": "15",
            "max_artifact_size": str(1024 * 1024 * 1024),
        }
    )
    assert create_form.is_valid(), create_form.errors.as_json()
    ftp_storage = create_form.save()
    assert ftp_storage.protocol == DestinationProtocolChoices.FTP
    assert ftp_storage.remote_retention_policy_id == ftp_storage_policy.pk
    assert ftp_storage.enforce_retention_policy is True

    target = SimpleNamespace(
        retention_override_id=device_local_policy.pk,
        retention_override=device_local_policy,
        policy_override_id=None,
        policy_override=None,
        remote_retention_policy_id=device_ftp_policy.pk,
        remote_retention_policy=device_ftp_policy,
    )

    # Enforced storage profiles win over device profiles.
    local_storage.local_retention_policy = local_storage_policy
    local_storage.enforce_retention_policy = True
    assert effective_local_retention_policy(target, local_storage) == local_storage_policy
    assert local_retention_policy_source(target, local_storage) == "Storage enforced"
    assert effective_remote_retention_policy(target, ftp_storage) == ftp_storage_policy
    assert remote_retention_policy_source(target, ftp_storage) == "Storage enforced"

    # Without the checkbox, device-specific profiles retain their precedence.
    local_storage.enforce_retention_policy = False
    ftp_storage.enforce_retention_policy = False
    assert effective_local_retention_policy(target, local_storage) == device_local_policy
    assert local_retention_policy_source(target, local_storage) == "Device override"
    assert effective_remote_retention_policy(target, ftp_storage) == device_ftp_policy
    assert remote_retention_policy_source(target, ftp_storage) == "Device override"

    client = Client()
    client.force_login(administrator)
    list_response = client.get(reverse("plugins:netbox_config_backup:backupdestination_list"))
    assert list_response.status_code == 200
    assert b"Storages" in list_response.content
    assert local_storage.name.encode() in list_response.content
    assert ftp_storage.name.encode() in list_response.content
    assert b"Storage policy enforced" in list_response.content

    local_detail = client.get(local_storage.get_absolute_url())
    assert local_detail.status_code == 200
    assert b"Default primary storage" in local_detail.content
    assert b"Test FTP storage" not in local_detail.content

    local_delete = client.get(
        reverse(
            "plugins:netbox_config_backup:backupdestination_delete",
            kwargs={"pk": local_storage.pk},
        )
    )
    assert local_delete.status_code == 404

    ftp_edit = client.get(
        reverse(
            "plugins:netbox_config_backup:backupdestination_edit",
            kwargs={"pk": ftp_storage.pk},
        )
    )
    assert ftp_edit.status_code == 200
    assert b'name="remote_retention_policy"' in ftp_edit.content
    assert b'name="enforce_retention_policy"' in ftp_edit.content
    assert b'name="local_retention_policy"' not in ftp_edit.content

    # NetBox serializer validation may temporarily mutate its model instance.
    # Permission checks must compare against a fresh database snapshot so an
    # API caller cannot bypass the destructive-retention permission gate.
    api_instance = BackupDestination.objects.get(pk=ftp_storage.pk)
    api_instance.remote_retention_policy = device_ftp_policy
    captured_permissions = {}
    api_view = BackupDestinationViewSet()
    api_view._assert_retention_permissions = lambda **kwargs: captured_permissions.update(kwargs)
    with patch.object(NetBoxModelViewSet, "perform_update", return_value=None):
        api_view.perform_update(
            SimpleNamespace(
                instance=api_instance,
                validated_data={"remote_retention_policy": device_ftp_policy},
                save=lambda: None,
            )
        )
    assert captured_permissions == {"local_changed": False, "remote_changed": True}
finally:
    # Discard the unsaved in-memory changes made while testing Local precedence.
    local_storage.refresh_from_db()
    if ftp_storage is not None:
        ftp_storage.delete()
    for policy in (
        device_ftp_policy,
        ftp_storage_policy,
        device_local_policy,
        local_storage_policy,
    ):
        if policy is not None:
            policy.delete()

print("STORAGE_PROFILES_SMOKE_OK")
