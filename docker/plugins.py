from os import environ

PLUGINS = [
    "netbox_snmp_sync",
    "netbox_zabbix_status",
    "netbox_config_backup",
]

PLUGINS_CONFIG = {
    "netbox_snmp_sync": {
        "write_vlans": True,
        "create_vlans": True,
    },
    "netbox_zabbix_status": {
        "api_url": environ.get("ZABBIX_API_URL", ""),
        "api_token": environ.get("ZABBIX_API_TOKEN", ""),
        "web_url": environ.get("ZABBIX_WEB_URL", ""),
        "verify_ssl": environ.get("ZABBIX_VERIFY_SSL", "true").lower() == "true",
        "matching_enabled": environ.get("ZABBIX_MATCHING_ENABLED", "true").lower() == "true",
        "dashboard_matched_only": environ.get("ZABBIX_DASHBOARD_MATCHED_ONLY", "true").lower()
        == "true",
        "dashboard_refresh": int(environ.get("ZABBIX_DASHBOARD_REFRESH", "60")),
    },
    "netbox_config_backup": {
        "storage_root": "/var/lib/netbox-config-backup",
        "recovery_package_ttl_minutes": int(
            environ.get("NETBOX_CONFIG_BACKUP_RECOVERY_PACKAGE_TTL_MINUTES", "60")
        ),
        "recovery_package_max_bytes": int(
            environ.get(
                "NETBOX_CONFIG_BACKUP_RECOVERY_PACKAGE_MAX_BYTES",
                str(1024 * 1024 * 1024),
            )
        ),
        "storage_backend": environ.get("NETBOX_CONFIG_BACKUP_STORAGE_BACKEND", "local"),
        "s3_bucket": environ.get("NETBOX_CONFIG_BACKUP_S3_BUCKET", ""),
        "s3_prefix": environ.get(
            "NETBOX_CONFIG_BACKUP_S3_PREFIX", "netbox-config-backup"
        ),
        "s3_region": environ.get("NETBOX_CONFIG_BACKUP_S3_REGION", ""),
        "s3_endpoint_url": environ.get("NETBOX_CONFIG_BACKUP_S3_ENDPOINT_URL", ""),
        "s3_addressing_style": environ.get(
            "NETBOX_CONFIG_BACKUP_S3_ADDRESSING_STYLE", "auto"
        ),
        "s3_verify_tls": environ.get(
            "NETBOX_CONFIG_BACKUP_S3_VERIFY_TLS", "true"
        ).lower() == "true",
        "s3_ca_bundle": environ.get("NETBOX_CONFIG_BACKUP_S3_CA_BUNDLE", ""),
        "s3_allow_insecure_http": environ.get(
            "NETBOX_CONFIG_BACKUP_S3_ALLOW_INSECURE_HTTP", "false"
        ).lower() == "true",
        "s3_server_side_encryption": environ.get(
            "NETBOX_CONFIG_BACKUP_S3_SERVER_SIDE_ENCRYPTION", "AES256"
        ),
        "s3_kms_key_id": environ.get("NETBOX_CONFIG_BACKUP_S3_KMS_KEY_ID", ""),
        "s3_request_timeout": int(
            environ.get("NETBOX_CONFIG_BACKUP_S3_REQUEST_TIMEOUT", "30")
        ),
        "s3_max_object_bytes": int(
            environ.get("NETBOX_CONFIG_BACKUP_S3_MAX_OBJECT_BYTES", str(1024 * 1024 * 1024))
        ),
        "vault_enabled": environ.get(
            "NETBOX_CONFIG_BACKUP_VAULT_ENABLED", "false"
        ).lower() == "true",
        "vault_addr": environ.get("VAULT_ADDR", ""),
        "vault_namespace": environ.get("VAULT_NAMESPACE", ""),
        "vault_auth_method": environ.get(
            "NETBOX_CONFIG_BACKUP_VAULT_AUTH_METHOD", "token"
        ),
        "vault_auth_mount_point": environ.get(
            "NETBOX_CONFIG_BACKUP_VAULT_AUTH_MOUNT_POINT", "approle"
        ),
        "vault_verify_tls": environ.get(
            "NETBOX_CONFIG_BACKUP_VAULT_VERIFY_TLS", "true"
        ).lower() == "true",
        "vault_ca_bundle": environ.get("NETBOX_CONFIG_BACKUP_VAULT_CA_BUNDLE", ""),
        "vault_allow_insecure_http": environ.get(
            "NETBOX_CONFIG_BACKUP_VAULT_ALLOW_INSECURE_HTTP", "false"
        ).lower() == "true",
        "vault_timeout": int(environ.get("NETBOX_CONFIG_BACKUP_VAULT_TIMEOUT", "10")),
        "receiver_root": "/var/lib/netbox-config-backup/receiver",
        "receiver_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_ed25519_key",
        "receiver_rsa_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_rsa_key",
        "error_message_max_length": 1000,
        "events_enabled": environ.get(
            "NETBOX_CONFIG_BACKUP_EVENTS_ENABLED", "true"
        ).lower() == "true",
        "notify_on_every_failure": environ.get(
            "NETBOX_CONFIG_BACKUP_NOTIFY_ON_EVERY_FAILURE", "false"
        ).lower() == "true",
        "metrics_enabled": environ.get(
            "NETBOX_CONFIG_BACKUP_METRICS_ENABLED", "false"
        ).lower() == "true",
        "nas_backup_enabled": environ.get(
            "NETBOX_CONFIG_BACKUP_NAS_ENABLED", "false"
        ).lower() == "true",
        "nas_backup_status_path": environ.get(
            "NETBOX_CONFIG_BACKUP_NAS_STATUS_PATH",
            "/var/lib/netbox-config-backup/nas-backup/last-success.json",
        ),
        "nas_backup_stale_hours": int(
            environ.get("NETBOX_CONFIG_BACKUP_NAS_STALE_HOURS", "48")
        ),
    },
}
