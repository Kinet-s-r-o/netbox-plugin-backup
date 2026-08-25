import hashlib
import io
import unittest
from typing import ClassVar
from unittest.mock import patch

from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.s3 import S3ConfigStorage


class FakeNotFound(RuntimeError):
    response: ClassVar[dict] = {
        "Error": {"Code": "NoSuchKey"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.copy_calls = []
        self.bucket_healthy = True

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise RuntimeError("precondition failed")
        content = bytes(kwargs["Body"])
        self.objects[key] = {
            "Body": content,
            "Metadata": dict(kwargs.get("Metadata", {})),
            "ETag": hashlib.sha256(content).hexdigest(),
        }

    def get_object(self, **kwargs):
        value = self._get(kwargs["Key"])
        return {
            "Body": io.BytesIO(value["Body"]),
            "ContentLength": len(value["Body"]),
            "Metadata": value["Metadata"],
        }

    def head_object(self, **kwargs):
        value = self._get(kwargs["Key"])
        return {
            "ContentLength": len(value["Body"]),
            "ETag": value["ETag"],
            "Metadata": value["Metadata"],
        }

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        source = self._get(kwargs["CopySource"]["Key"])
        self.objects[kwargs["Key"]] = {
            "Body": source["Body"],
            "Metadata": dict(source["Metadata"]),
            "ETag": source["ETag"],
        }

    def head_bucket(self, **kwargs):
        if not self.bucket_healthy:
            raise RuntimeError("not healthy")

    def _get(self, key):
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FakeNotFound() from exc


class S3StorageTests(unittest.TestCase):
    def make_storage(self, client=None, **kwargs):
        return S3ConfigStorage(
            bucket="private-backups",
            prefix="config-backup/prod",
            client=client or FakeS3Client(),
            **kwargs,
        )

    def test_round_trip_is_encrypted_and_has_no_acl(self):
        client = FakeS3Client()
        storage = self.make_storage(client)
        key = "devices/1/revisions/abc/config.txt"

        result = storage.put(key, b"hostname router\n", {"driver": "fake"})

        self.assertEqual(result.size, 16)
        self.assertTrue(storage.exists(key))
        self.assertEqual(storage.get(key), b"hostname router\n")
        call = client.put_calls[0]
        self.assertEqual(call["ServerSideEncryption"], "AES256")
        self.assertEqual(call["IfNoneMatch"], "*")
        self.assertNotIn("ACL", call)
        self.assertEqual(call["Key"], f"config-backup/prod/{key}")

    def test_kms_key_is_sent_for_kms_encryption(self):
        client = FakeS3Client()
        storage = self.make_storage(
            client,
            server_side_encryption="aws:kms",
            kms_key_id="alias/netbox-backup",
        )

        storage.put("devices/1/config", b"data")

        self.assertEqual(client.put_calls[0]["SSEKMSKeyId"], "alias/netbox-backup")

    def test_staged_delete_can_be_restored_and_purged(self):
        client = FakeS3Client()
        storage = self.make_storage(client)
        key = "devices/1/revisions/abc/config.txt"
        storage.put(key, b"config")

        staged = storage.stage_delete(key, "cleanup-1")

        self.assertFalse(storage.exists(key))
        self.assertTrue(storage.exists(staged))
        self.assertEqual(client.copy_calls[0]["MetadataDirective"], "COPY")
        self.assertEqual(client.copy_calls[0]["ServerSideEncryption"], "AES256")
        storage.restore_staged_delete(key, staged)
        self.assertEqual(storage.get(key), b"config")
        self.assertFalse(storage.exists(staged))

        staged = storage.stage_delete(key, "cleanup-2")
        storage.purge_staged_delete(staged)
        self.assertFalse(storage.exists(staged))

    def test_missing_and_unsafe_keys_are_handled(self):
        storage = self.make_storage()
        self.assertFalse(storage.exists("devices/1/missing"))
        self.assertIsNone(storage.stage_delete("devices/1/missing", "cleanup"))
        for key in ("../secret", "/absolute", "devices\\1\\config"):
            with self.subTest(key=key), self.assertRaises(StorageError):
                storage.put(key, b"secret")

    def test_rejects_insecure_endpoint_and_invalid_encryption(self):
        with self.assertRaisesRegex(StorageError, "HTTPS"):
            S3ConfigStorage(
                bucket="backups",
                endpoint_url="http://minio:9000",
                client=FakeS3Client(),
            )
        with self.assertRaisesRegex(StorageError, "KMS key"):
            self.make_storage(server_side_encryption="aws:kms")

    def test_healthcheck_is_boolean_and_errors_are_safe(self):
        client = FakeS3Client()
        storage = self.make_storage(client)
        self.assertTrue(storage.healthcheck())
        client.bucket_healthy = False
        self.assertFalse(storage.healthcheck())

        with self.assertRaises(StorageError) as raised:
            storage.get("missing")
        self.assertEqual(str(raised.exception), "S3 configuration read failed.")

    def test_missing_optional_dependency_has_actionable_error(self):
        original_import = __import__

        def import_without_boto(name, *args, **kwargs):
            if name in {"boto3", "botocore.config"}:
                missing = name.split(".", 1)[0]
                raise ModuleNotFoundError(f"No module named '{missing}'", name=missing)
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=import_without_boto),
            self.assertRaisesRegex(StorageError, "optional 's3'"),
        ):
            S3ConfigStorage(bucket="backups")


if __name__ == "__main__":
    unittest.main()
