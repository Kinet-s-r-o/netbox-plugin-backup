from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .base import ConfigStorage, StorageError, StorageObject


class S3ConfigStorage(ConfigStorage):
    """Private, encrypted S3-compatible object storage."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "netbox-config-backup",
        region: str = "",
        endpoint_url: str = "",
        addressing_style: str = "auto",
        verify_tls: bool = True,
        ca_bundle: str = "",
        allow_insecure_http: bool = False,
        server_side_encryption: str = "AES256",
        kms_key_id: str = "",
        request_timeout: int = 30,
        max_object_bytes: int = 1024 * 1024 * 1024,
        client=None,
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise StorageError("S3 storage bucket is not configured.")
        if addressing_style not in {"auto", "path", "virtual"}:
            raise StorageError("S3 addressing style is invalid.")
        if server_side_encryption not in {"AES256", "aws:kms"}:
            raise StorageError("S3 server-side encryption must be AES256 or aws:kms.")
        if server_side_encryption == "aws:kms" and not kms_key_id:
            raise StorageError("S3 KMS encryption requires a KMS key ID.")
        if request_timeout <= 0 or max_object_bytes <= 0:
            raise StorageError("S3 storage limits must be positive.")
        if endpoint_url:
            parsed = urlparse(endpoint_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise StorageError("S3 endpoint URL is invalid.")
            if parsed.scheme != "https" and not allow_insecure_http:
                raise StorageError("S3 endpoint must use HTTPS.")

        self.bucket = bucket.strip()
        self.prefix = self._normalize_prefix(prefix)
        self.server_side_encryption = server_side_encryption
        self.kms_key_id = kms_key_id
        self.max_object_bytes = max_object_bytes
        if client is None:
            self.client = self._build_client(
                region=region,
                endpoint_url=endpoint_url,
                addressing_style=addressing_style,
                verify_tls=verify_tls,
                ca_bundle=ca_bundle,
                request_timeout=request_timeout,
            )
        else:
            self.client = client

    @staticmethod
    def _build_client(
        *,
        region: str,
        endpoint_url: str,
        addressing_style: str,
        verify_tls: bool,
        ca_bundle: str,
        request_timeout: int,
    ):
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:
            if exc.name not in {"boto3", "botocore"}:
                raise
            raise StorageError("S3 storage requires the optional 's3' package extra.") from exc

        verify: bool | str = ca_bundle or verify_tls
        return boto3.client(
            "s3",
            region_name=region or None,
            endpoint_url=endpoint_url or None,
            verify=verify,
            config=Config(
                signature_version="s3v4",
                connect_timeout=request_timeout,
                read_timeout=request_timeout,
                retries={"mode": "standard", "max_attempts": 3},
                s3={"addressing_style": addressing_style},
            ),
        )

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        if not isinstance(prefix, str):
            raise StorageError("S3 storage prefix is invalid.")
        value = prefix.strip("/")
        if not value:
            return ""
        pure = PurePosixPath(value)
        if "\\" in value or any(part in {"", ".", ".."} for part in pure.parts):
            raise StorageError("S3 storage prefix is invalid.")
        return value

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str) or not key or "\\" in key:
            raise StorageError("Invalid storage key.")
        pure = PurePosixPath(key)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise StorageError("Invalid storage key.")
        return str(pure)

    def _object_key(self, key: str) -> str:
        logical = self._validate_key(key)
        return f"{self.prefix}/{logical}" if self.prefix else logical

    def _encryption_parameters(self) -> dict[str, str]:
        values = {"ServerSideEncryption": self.server_side_encryption}
        if self.server_side_encryption == "aws:kms":
            values["SSEKMSKeyId"] = self.kms_key_id
        return values

    def put(
        self,
        key: str,
        content: bytes,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObject:
        if not isinstance(content, bytes):
            raise TypeError("Storage content must be bytes.")
        if len(content) > self.max_object_bytes:
            raise StorageError("Configuration object exceeds the S3 size limit.")
        safe_metadata = {str(name): str(value) for name, value in dict(metadata or {}).items()}
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
                Body=content,
                ContentLength=len(content),
                ContentType="application/octet-stream",
                Metadata=safe_metadata,
                IfNoneMatch="*",
                **self._encryption_parameters(),
            )
        except Exception as exc:
            raise StorageError("S3 configuration write failed.") from exc
        return StorageObject(key=key, size=len(content), metadata=safe_metadata)

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
            length = int(response.get("ContentLength", 0))
            if length < 0 or length > self.max_object_bytes:
                raise StorageError("S3 configuration object exceeds the download limit.")
            content = response["Body"].read(self.max_object_bytes + 1)
            if len(content) > self.max_object_bytes or (length and len(content) != length):
                raise StorageError("S3 configuration download was incomplete.")
            return content
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("S3 configuration read failed.") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise StorageError("S3 configuration existence check failed.") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))
        except Exception as exc:
            raise StorageError("S3 configuration delete failed.") from exc

    def stage_delete(self, key: str, namespace: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", namespace):
            raise StorageError("Invalid storage quarantine namespace.")
        if not self.exists(key):
            return None
        staged_key = f".retention-trash/{namespace}/{self._validate_key(key)}"
        if self.exists(staged_key):
            raise StorageError("Storage quarantine object already exists.")
        self._copy(key, staged_key)
        try:
            self.delete(key)
        except StorageError:
            try:
                self.delete(staged_key)
            except StorageError:
                pass
            raise
        return staged_key

    def restore_staged_delete(self, key: str, staged_key: str) -> None:
        if not self.exists(staged_key):
            raise StorageError("Quarantined configuration object is missing.")
        if self.exists(key):
            raise StorageError("Configuration restore destination already exists.")
        self._copy(staged_key, key)
        self.delete(staged_key)

    def purge_staged_delete(self, staged_key: str) -> None:
        self.delete(staged_key)

    def _copy(self, source_key: str, destination_key: str) -> None:
        try:
            source = self.client.head_object(
                Bucket=self.bucket,
                Key=self._object_key(source_key),
            )
            self.client.copy_object(
                Bucket=self.bucket,
                Key=self._object_key(destination_key),
                CopySource={"Bucket": self.bucket, "Key": self._object_key(source_key)},
                CopySourceIfMatch=source.get("ETag", ""),
                MetadataDirective="COPY",
                **self._encryption_parameters(),
            )
            copied = self.client.head_object(
                Bucket=self.bucket,
                Key=self._object_key(destination_key),
            )
            if int(copied.get("ContentLength", -1)) != int(source.get("ContentLength", -2)):
                raise StorageError("S3 quarantine copy failed its size check.")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("S3 configuration copy failed.") from exc

    def healthcheck(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:  # noqa: BLE001 - healthcheck is intentionally boolean
            return False

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(response.get("Error", {}).get("Code", ""))
        return status == 404 or code in {"404", "NoSuchKey", "NotFound"}
