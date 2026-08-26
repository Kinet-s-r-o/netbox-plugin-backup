"""Exercise legacy destination data isolation and the FTP-only destination UI."""

from django.core.exceptions import ValidationError
from django.test import Client
from users.models import User

from netbox_config_backup.models import (
    BackupDestination,
    ConfigRevision,
    CredentialProfile,
    RevisionReplica,
)

name = "[Smoke] External SFTP destination"
BackupDestination.objects.filter(name__in=(name, f"{name} FTP")).delete()
credential = CredentialProfile.objects.filter(
    provider_id="encrypted_database", auth_type="password"
).first()
revision = ConfigRevision.objects.order_by("-created").first()
administrator = User.objects.filter(is_superuser=True, is_active=True).first()
assert credential is not None, "SFTP destination smoke requires one credential profile"
assert revision is not None, "SFTP destination smoke requires one revision"
assert administrator is not None, "SFTP destination smoke requires a superuser"

invalid = BackupDestination(
    name="invalid",
    host="nas.invalid",
    base_path="../escape",
    credential_profile=credential,
)
try:
    invalid.full_clean()
except ValidationError:
    pass
else:
    raise AssertionError("Unsafe remote path was accepted")

insecure_ftp = BackupDestination(
    name="invalid FTP",
    protocol="ftp",
    host="ftp.invalid",
    port=21,
    credential_profile=credential,
)
try:
    insecure_ftp.full_clean()
except ValidationError as exc:
    assert "allow_insecure_ftp" in exc.message_dict
else:
    raise AssertionError("FTP was accepted without explicit acknowledgement")

destination = BackupDestination.objects.create(
    name=name,
    enabled=False,
    auto_replicate=False,
    host="nas.invalid",
    base_path="netbox-config-backup/smoke",
    credential_profile=credential,
)
replica = RevisionReplica.objects.create(
    revision=revision,
    destination=destination,
    status="failed",
    error_code="SMOKE_TEST",
    error_message="Safe smoke-test message.",
)
ftp_destination = BackupDestination.objects.create(
    name=f"{name} FTP",
    enabled=False,
    auto_replicate=False,
    protocol="ftp",
    allow_insecure_ftp=True,
    host="ftp.invalid",
    port=21,
    base_path="netbox-config-backup/ftp-smoke",
    credential_profile=credential,
)

try:
    client = Client()
    client.force_login(administrator)
    edit_response = client.post(
        f"/plugins/config-backup/destinations/{ftp_destination.pk}/edit/",
        {
            "name": ftp_destination.name,
            "protocol": "ftp",
            "allow_insecure_ftp": "on",
            "host": ftp_destination.host,
            "port": "2121",
            "base_path": ftp_destination.base_path,
            "credential_profile": str(credential.pk),
            "connect_timeout": "15",
            "max_retries": "3",
            "retry_delay_minutes": "15",
            "max_artifact_size": str(1024 * 1024 * 1024),
        },
    )
    assert edit_response.status_code == 302, edit_response.content[:500]
    ftp_destination.refresh_from_db()
    assert ftp_destination.port == 2121
    ftp_replica = RevisionReplica.objects.create(
        revision=revision,
        destination=ftp_destination,
        status="success",
        remote_path=(
            f"/{ftp_destination.base_path}/devices/smoke-device/backups/2026-08-26_08-11-06"
        ),
        remote_available=True,
    )
    endpoint_change = client.post(
        f"/plugins/config-backup/destinations/{ftp_destination.pk}/edit/",
        {
            "name": ftp_destination.name,
            "protocol": "ftp",
            "allow_insecure_ftp": "on",
            "host": "different-ftp.invalid",
            "port": str(ftp_destination.port),
            "base_path": ftp_destination.base_path,
            "credential_profile": str(credential.pk),
            "connect_timeout": "15",
            "max_retries": "3",
            "retry_delay_minutes": "15",
            "max_artifact_size": str(1024 * 1024 * 1024),
        },
    )
    assert endpoint_change.status_code == 200
    assert b"cannot be changed while FTP copies exist" in endpoint_change.content
    ftp_destination.refresh_from_db()
    assert ftp_destination.host == "ftp.invalid"
    destination_list = client.get("/plugins/config-backup/destinations/")
    assert destination_list.status_code == 200
    assert destination.get_absolute_url().encode() not in destination_list.content
    assert ftp_destination.name.encode() in destination_list.content
    detail = client.get(destination.get_absolute_url())
    assert detail.status_code == 404
    ftp_detail = client.get(ftp_destination.get_absolute_url())
    assert ftp_detail.status_code == 200
    assert b"FTP is not encrypted" in ftp_detail.content
    add_page = client.get("/plugins/config-backup/destinations/add/")
    assert add_page.status_code == 200
    assert b'name="protocol"' not in add_page.content
    settings_page = client.get("/plugins/config-backup/settings/")
    assert settings_page.status_code == 200
    assert b"FTP destination" in settings_page.content
finally:
    replica.delete()
    if "ftp_replica" in locals():
        ftp_replica.delete()
    destination.delete()
    ftp_destination.delete()

print("SFTP_DESTINATION_SMOKE_OK")
