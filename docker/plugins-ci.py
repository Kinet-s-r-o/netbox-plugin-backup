"""Minimal NetBox plugin configuration used only by the release CI smoke test."""

PLUGINS = ["netbox_config_backup"]

PLUGINS_CONFIG = {
    "netbox_config_backup": {
        "storage_backend": "local",
        "storage_root": "/var/lib/netbox-config-backup",
        "network_storage_mount_roots": ["/mnt/netbox-config-backup"],
        "network_storage_require_mountpoint": True,
        "receiver_root": "/var/lib/netbox-config-backup/receiver",
        "events_enabled": True,
        "metrics_enabled": False,
    }
}
