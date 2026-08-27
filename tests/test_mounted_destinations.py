import hashlib
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from netbox_config_backup.services.destination_mounted import (
    delete_revision_replica_mounted,
    reconcile_mounted_destination,
    replicate_revision_mounted,
    test_mounted_destination,
)


class RelatedList:
    def __init__(self, values):
        self.values = tuple(values)

    def all(self):
        return self.values


def fixture(root: Path, protocol: str):
    content = b"hostname mounted-storage-test\n"
    artifact = SimpleNamespace(
        artifact_type="running_config",
        format="text",
        storage_key="devices/7/revisions/example/configuration.txt",
        size=len(content),
        raw_hash=hashlib.sha256(content).hexdigest(),
        is_primary=True,
    )
    device = SimpleNamespace(pk=7, name="router.example", id=7)
    target = SimpleNamespace(device=device, device_id=7)
    revision = SimpleNamespace(
        pk=19,
        revision_uuid=uuid.uuid4(),
        created=datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
        target=target,
        driver_id="fake",
        artifacts=RelatedList((artifact,)),
    )
    destination = SimpleNamespace(
        pk=4,
        protocol=protocol,
        enabled=True,
        mount_path=str(root / protocol),
        base_path="netbox-config-backup",
        max_artifact_size=1024 * 1024,
        get_protocol_display=lambda: "NFS mount" if protocol == "nfs" else "SMB3 mount",
    )
    Path(destination.mount_path).mkdir()
    storage = SimpleNamespace(get=lambda key: content)
    return destination, revision, artifact, storage


class MountedDestinationTests(unittest.TestCase):
    def settings_patch(self, root):
        return patch(
            "netbox_config_backup.services.destination_mounted.settings",
            SimpleNamespace(
                PLUGINS_CONFIG={
                    "netbox_config_backup": {
                        "network_storage_mount_roots": [str(root)],
                    }
                }
            ),
        )

    def test_nfs_and_smb3_write_verify_audit_and_delete(self):
        for protocol in ("nfs", "smb"):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                destination, revision, artifact, storage = fixture(root, protocol)
                with (
                    self.settings_patch(root),
                    patch(
                        "netbox_config_backup.services.destination_mounted.os.path.ismount",
                        return_value=True,
                    ),
                    patch(
                        "netbox_config_backup.services.destination_mounted.build_config_storage",
                        return_value=storage,
                    ),
                ):
                    test_result = test_mounted_destination(destination)
                    self.assertTrue(test_result["success"])

                    result = replicate_revision_mounted(destination, revision)
                    self.assertEqual(result.artifact_count, 1)
                    self.assertGreater(result.bytes_transferred, artifact.size)

                    # Recorded paths and manifests must remain valid after a
                    # later NetBox device rename.
                    revision.target.device.name = "renamed-router.example"

                    replica = SimpleNamespace(
                        pk=22,
                        destination=destination,
                        destination_id=destination.pk,
                        revision=revision,
                        status="success",
                        remote_path=result.remote_path,
                        remote_available=True,
                        remote_deleted_at=None,
                    )
                    audit = reconcile_mounted_destination(destination, replicas=(replica,))
                    self.assertTrue(audit["success"])
                    self.assertEqual(audit["checked_files"], 2)

                    deleted = delete_revision_replica_mounted(replica)
                    self.assertEqual(deleted.deleted_file_count, 2)
                    self.assertTrue(deleted.directory_removed)

    def test_rejects_mount_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            destination, _revision, _artifact, _storage = fixture(Path(outside), "nfs")
            with (
                self.settings_patch(Path(allowed)),
                patch(
                    "netbox_config_backup.services.destination_mounted.os.path.ismount",
                    return_value=True,
                ),
                self.assertRaisesRegex(Exception, "allowed mount roots"),
            ):
                test_mounted_destination(destination)

    def test_rejects_directory_when_network_mount_is_not_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination, _revision, _artifact, _storage = fixture(root, "nfs")
            with (
                self.settings_patch(root),
                patch(
                    "netbox_config_backup.services.destination_mounted.os.path.ismount",
                    return_value=False,
                ),
                self.assertRaisesRegex(Exception, "not an active mount"),
            ):
                test_mounted_destination(destination)

    def test_rejects_filesystem_root_as_allowed_mount_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination, _revision, _artifact, _storage = fixture(root, "smb")
            filesystem_root = Path(destination.mount_path).anchor
            with (
                patch(
                    "netbox_config_backup.services.destination_mounted.settings",
                    SimpleNamespace(
                        PLUGINS_CONFIG={
                            "netbox_config_backup": {
                                "network_storage_mount_roots": [filesystem_root],
                            }
                        }
                    ),
                ),
                patch(
                    "netbox_config_backup.services.destination_mounted.os.path.ismount",
                    return_value=True,
                ),
                self.assertRaisesRegex(Exception, "allowed mount roots"),
            ):
                test_mounted_destination(destination)

    def test_retention_refuses_unknown_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination, revision, _artifact, storage = fixture(root, "nfs")
            with (
                self.settings_patch(root),
                patch(
                    "netbox_config_backup.services.destination_mounted.os.path.ismount",
                    return_value=True,
                ),
                patch(
                    "netbox_config_backup.services.destination_mounted.build_config_storage",
                    return_value=storage,
                ),
            ):
                result = replicate_revision_mounted(destination, revision)
                replica = SimpleNamespace(
                    destination=destination,
                    revision=revision,
                    remote_path=result.remote_path,
                )
                revision_dir = Path(destination.mount_path) / result.remote_path.lstrip("/")
                (revision_dir / "untracked.txt").write_text("do not delete", encoding="utf-8")
                with self.assertRaisesRegex(Exception, "unknown files"):
                    delete_revision_replica_mounted(replica)
                self.assertTrue((revision_dir / "untracked.txt").exists())


if __name__ == "__main__":
    unittest.main()
