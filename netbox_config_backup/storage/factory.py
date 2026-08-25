from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import ConfigStorage, StorageError
from .local import LocalConfigStorage


def build_config_storage(config: Mapping[str, Any] | None = None) -> ConfigStorage:
    if config is None:
        from django.conf import settings

        config = settings.PLUGINS_CONFIG["netbox_config_backup"]

    backend = str(config.get("storage_backend", "local")).strip().lower()
    if backend == "local":
        return LocalConfigStorage(config["storage_root"])
    if backend == "s3":
        from .s3 import S3ConfigStorage

        return S3ConfigStorage(
            bucket=config.get("s3_bucket", ""),
            prefix=config.get("s3_prefix", "netbox-config-backup"),
            region=config.get("s3_region", ""),
            endpoint_url=config.get("s3_endpoint_url", ""),
            addressing_style=config.get("s3_addressing_style", "auto"),
            verify_tls=config.get("s3_verify_tls", True),
            ca_bundle=config.get("s3_ca_bundle", ""),
            allow_insecure_http=config.get("s3_allow_insecure_http", False),
            server_side_encryption=config.get("s3_server_side_encryption", "AES256"),
            kms_key_id=config.get("s3_kms_key_id", ""),
            request_timeout=config.get("s3_request_timeout", 30),
            max_object_bytes=config.get("s3_max_object_bytes", 1024 * 1024 * 1024),
        )
    raise StorageError("Unknown configuration storage backend.")
