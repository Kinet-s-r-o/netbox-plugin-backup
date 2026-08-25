"""Exercise immutable FTP replication against an in-memory FTP double."""

import ftplib
from unittest.mock import patch

from netbox_config_backup.models import BackupDestination, ConfigRevision, CredentialProfile
from netbox_config_backup.services.destination_ftp import (
    replicate_revision_ftp,
    test_ftp_destination,
)


class FakeFTP:
    def __init__(self):
        self.files = {}
        self.directories = {"/"}
        self.closed = False
        self.sock = object()

    def cwd(self, path):
        if path not in self.directories:
            raise ftplib.error_perm("550 directory not found")

    def mkd(self, path):
        self.directories.add(path)

    def voidcmd(self, _command):
        return "200 OK"

    def size(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        return len(self.files[path])

    def nlst(self, path):
        return [path] if path in self.files else []

    def storbinary(self, command, source):
        self.files[command.removeprefix("STOR ")] = source.read()

    def retrbinary(self, command, callback):
        callback(self.files[command.removeprefix("RETR ")])

    def rename(self, source, destination):
        if destination in self.files:
            raise ftplib.error_perm("550 destination exists")
        self.files[destination] = self.files.pop(source)

    def delete(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        del self.files[path]

    def quit(self):
        self.closed = True
        self.sock = None

    def close(self):
        self.closed = True
        self.sock = None


credential = CredentialProfile.objects.filter(
    provider_id="encrypted_database", auth_type="password"
).first()
revision = ConfigRevision.objects.prefetch_related("artifacts").order_by("-created").first()
assert credential is not None
assert revision is not None
destination = BackupDestination(
    name="FTP transport smoke",
    enabled=True,
    protocol="ftp",
    allow_insecure_ftp=True,
    host="ftp.invalid",
    port=21,
    base_path="backup",
    credential_profile=credential,
)

test_server = FakeFTP()
with patch("netbox_config_backup.services.destination_ftp._connect", return_value=test_server):
    test_result = test_ftp_destination(destination)
assert test_result["success"] is True
assert not test_server.files

ftp = FakeFTP()
with patch("netbox_config_backup.services.destination_ftp._connect", return_value=ftp):
    first = replicate_revision_ftp(destination, revision)
    second = replicate_revision_ftp(destination, revision)

assert first.artifact_count == revision.artifacts.count()
assert first.bytes_transferred > 0
assert second.bytes_transferred == 0, "Idempotent replay transferred existing objects"
assert any(path.endswith("/_netbox_manifest.json") for path in ftp.files)
assert not any(".part-" in path for path in ftp.files)
assert ftp.closed
print("FTP_TRANSPORT_SMOKE_OK")
