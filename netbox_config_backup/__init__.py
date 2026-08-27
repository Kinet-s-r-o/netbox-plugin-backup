from typing import ClassVar

from .__about__ import __version__

try:
    from netbox.plugins import PluginConfig
except ModuleNotFoundError as exc:
    # Keep the framework-independent core importable for unit tests and tooling
    # outside a NetBox installation. Do not hide failures from any other import.
    if exc.name != "netbox":
        raise
    NetBoxConfigBackupConfig = None
    config = None
else:

    class NetBoxConfigBackupConfig(PluginConfig):
        name = "netbox_config_backup"
        verbose_name = "Config Backup"
        description = "Read-only, auditable configuration backups for NetBox devices."
        version = __version__
        min_version = "4.6.0"
        max_version = "4.6.99"
        base_url = "config-backup"
        queues: ClassVar[list[str]] = ["backup"]
        middleware: ClassVar[list[str]] = [
            "netbox_config_backup.middleware.ConfigBackupLanguageMiddleware"
        ]
        required_settings: ClassVar[list[str]] = []
        default_settings: ClassVar[dict[str, object]] = {
            "storage_root": "/var/lib/netbox-config-backup",
            # NFS and SMB3 shares are mounted by the host/container runtime.
            # UI-managed mount paths must remain below one of these roots.
            "network_storage_mount_roots": ["/mnt/netbox-config-backup"],
            # Refuse writes when the configured share is not an active mount.
            # This prevents a disconnected NFS/SMB share from silently falling
            # back to the container's local filesystem.
            "network_storage_require_mountpoint": True,
            "recovery_package_ttl_minutes": 60,
            "recovery_package_max_bytes": 1024 * 1024 * 1024,
            "storage_backend": "local",
            "s3_bucket": "",
            "s3_prefix": "netbox-config-backup",
            "s3_region": "",
            "s3_endpoint_url": "",
            "s3_addressing_style": "auto",
            "s3_verify_tls": True,
            "s3_ca_bundle": "",
            "s3_allow_insecure_http": False,
            "s3_server_side_encryption": "AES256",
            "s3_kms_key_id": "",
            "s3_request_timeout": 30,
            "s3_max_object_bytes": 1024 * 1024 * 1024,
            "vault_enabled": False,
            "vault_addr": "",
            "vault_namespace": "",
            "vault_auth_method": "token",
            "vault_auth_mount_point": "approle",
            "vault_verify_tls": True,
            "vault_ca_bundle": "",
            "vault_allow_insecure_http": False,
            "vault_timeout": 10,
            "receiver_root": "/var/lib/netbox-config-backup/receiver",
            "receiver_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_ed25519_key",
            "receiver_rsa_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_rsa_key",
            "error_message_max_length": 1000,
            "dispatcher_batch_size": 100,
            "stale_run_minutes": 120,
            "stale_target_grace_minutes": 60,
            "content_preview_max_bytes": 1024 * 1024,
            "diff_input_max_bytes": 25 * 1024 * 1024,
            "diff_max_lines": 20000,
            "events_enabled": True,
            "notify_on_every_failure": False,
            "metrics_enabled": False,
            "nas_backup_enabled": False,
            "nas_backup_status_path": (
                "/var/lib/netbox-config-backup/nas-backup/last-success.json"
            ),
            "nas_backup_stale_hours": 48,
        }

        def ready(self):
            super().ready()
            from . import jobs  # noqa: F401
            from .events import register_event_types
            from .metrics import register_metrics_collector

            register_event_types()
            register_metrics_collector()

    config = NetBoxConfigBackupConfig

__all__ = ["NetBoxConfigBackupConfig", "__version__", "config"]
