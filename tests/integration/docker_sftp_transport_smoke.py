"""Exercise immutable SFTP artifact replication against an in-memory SFTP double."""

from types import SimpleNamespace
from unittest.mock import patch

from netbox_config_backup.models import BackupDestination, ConfigRevision, CredentialProfile
from netbox_config_backup.services.destination_sftp import replicate_revision


class FakeSFTP:
    def __init__(self):
        self.files = {}
        self.directories = {""}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stat(self, path):
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.directories:
            return SimpleNamespace(st_size=0)
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self.directories.add(path)

    def putfo(self, source, path, confirm=True):
        assert confirm
        self.files[path] = source.read()

    def getfo(self, path, destination):
        destination.write(self.files[path])

    def rename(self, source, destination):
        if destination in self.files:
            raise FileExistsError(destination)
        self.files[destination] = self.files.pop(source)

    def remove(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]


class FakeClient:
    def __init__(self, sftp):
        self.sftp = sftp
        self.closed = False

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True


credential = CredentialProfile.objects.filter(provider_id="encrypted_database").first()
revision = ConfigRevision.objects.prefetch_related("artifacts").order_by("-created").first()
assert credential is not None
assert revision is not None
destination = BackupDestination(
    name="transport smoke",
    enabled=True,
    host="nas.invalid",
    base_path="backup",
    credential_profile=credential,
    host_key_type="ssh-ed25519",
    host_key_public="AAAA",
    host_key_fingerprint_sha256="SHA256:test",
)
destination.host_key_approved_at = revision.created
sftp = FakeSFTP()
client = FakeClient(sftp)

with patch("netbox_config_backup.services.destination_sftp._connect", return_value=client):
    first = replicate_revision(destination, revision)
    second = replicate_revision(destination, revision)

assert first.artifact_count == revision.artifacts.count()
assert first.bytes_transferred > 0
assert second.bytes_transferred == 0, "Idempotent replay transferred existing objects"
assert any(path.endswith("/_netbox_manifest.json") for path in sftp.files)
assert not any(".part-" in path for path in sftp.files)
assert client.closed
print("SFTP_TRANSPORT_SMOKE_OK")
