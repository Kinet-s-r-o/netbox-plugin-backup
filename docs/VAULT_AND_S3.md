# HashiCorp Vault and S3 storage

Both integrations are optional. Local artifact storage remains the default and
the Vault provider remains disabled until explicitly configured. Apply the same
settings and environment to every NetBox web and worker process.

Install the matching package extra before enabling either backend:

```shell
python -m pip install 'netbox-config-backup[vault]'
python -m pip install 'netbox-config-backup[s3]'
```

The standard local-storage plus FTP deployment needs neither extra.

## HashiCorp Vault KV v2

Enable the provider in `PLUGINS_CONFIG`:

```python
PLUGINS_CONFIG["netbox_config_backup"].update({
    "vault_enabled": True,
    "vault_addr": "https://vault.example.com:8200",
    "vault_namespace": "",
    "vault_auth_method": "approle",
    "vault_auth_mount_point": "approle",
    "vault_verify_tls": True,
    "vault_ca_bundle": "/etc/ssl/certs/internal-ca.pem",
})
```

Pass AppRole material only through the process environment or container secret:

```text
NETBOX_CONFIG_BACKUP_VAULT_ROLE_ID=...
NETBOX_CONFIG_BACKUP_VAULT_SECRET_ID=...
```

Token authentication is also supported with
`NETBOX_CONFIG_BACKUP_VAULT_TOKEN` (or the standard `VAULT_TOKEN`) and
`vault_auth_method="token"`. AppRole with a short-lived token and a narrowly
scoped policy is recommended for a persistent worker.

Create a KV v2 secret whose data contains exactly the fields needed by the
device account:

```json
{
  "username": "netbox-backup",
  "password": "write-only-secret",
  "enable_secret": "optional-enable-secret"
}
```

Use `private_key` instead of `password` for key authentication. Exactly one of
`password` or `private_key` must be present.

Vault is a backend-only advanced integration in the streamlined, FTP-focused
UI. The normal Credential Profile form offers only **Environment variables**
and **Encrypted database**. Provision a Vault credential profile through the
controlled REST API at `/api/plugins/config-backup/credential-profiles/` (or
equivalent deployment automation) with `provider_id="vault_kv2"`, the required
`auth_type`, and a `secret_reference` in this format:

```text
vault://secret/network/devices/router-1
```

Here `secret` is the KV v2 mount and `network/devices/router-1` is its path.
Assign the resulting profile through the platform-mapping or backup-target API.
Vault profiles are intentionally hidden from the normal Settings and Add device
forms. NetBox stores only this reference. The provider performs only the KV v2
read operation; it never lists, creates, modifies, deletes, or logs a Vault
secret.

Example least-privilege Vault policy:

```hcl
path "secret/data/network/devices/*" {
  capabilities = ["read"]
}
```

Use HTTPS and certificate verification. `vault_allow_insecure_http=True` exists
only for isolated local development and must not be used in production.

## Amazon S3 or S3-compatible storage

The SDK uses the standard AWS credential provider chain. Prefer an EC2/ECS IAM
role, EKS workload identity, or an equivalent short-lived identity. Do not put
AWS access keys into NetBox database fields or `PLUGINS_CONFIG`.

```python
PLUGINS_CONFIG["netbox_config_backup"].update({
    "storage_backend": "s3",
    "s3_bucket": "company-netbox-config-backups",
    "s3_prefix": "production/netbox-config-backup",
    "s3_region": "eu-central-1",
    "s3_endpoint_url": "",  # Set only for an S3-compatible service.
    "s3_addressing_style": "auto",
    "s3_verify_tls": True,
    "s3_ca_bundle": "",
    "s3_allow_insecure_http": False,
    "s3_server_side_encryption": "aws:kms",
    "s3_kms_key_id": "alias/netbox-config-backup",
})
```

`AES256` (SSE-S3) is the default and works with most compatible services.
`aws:kms` requires `s3_kms_key_id` plus the corresponding KMS permissions.
Every upload and quarantine copy explicitly requests the configured encryption.
The plugin sends no object ACL and assumes a private bucket with Bucket Owner
Enforced ownership and Block Public Access.

The runtime identity needs only these S3 actions, scoped to the configured
bucket and prefix:

- `s3:GetObject`
- `s3:PutObject`
- `s3:DeleteObject`
- `s3:ListBucket` for the health check

For SSE-KMS, also grant the minimum necessary `kms:Encrypt`, `kms:Decrypt`, and
`kms:GenerateDataKey` permissions on the selected key. Enable S3 Versioning as
an additional recovery layer and apply lifecycle rules according to policy.

## Migrating an existing local installation

Do not switch `storage_backend` before copying existing artifacts. Configure
all `s3_*` values while keeping `storage_backend="local"`, then run:

```shell
python manage.py config_backup_migrate_to_s3
python manage.py config_backup_migrate_to_s3 --commit
```

The first command verifies local files and plans the operation. `--commit`
copies every missing object, verifies its size and SHA-256 after upload, and is
safe to rerun. Source files are never deleted. If any database artifact is
missing or fails integrity validation, the command stops and instructs you not
to switch.

After a fully successful committed run:

1. set `storage_backend="s3"`;
2. restart NetBox web and every backup worker;
3. open an older revision and verify its redacted preview;
4. run a new backup and retention dry-run;
5. keep the local volume until the S3 deployment has been backed up and observed.

There is intentionally no silent fallback from S3 to local storage: falling
back could split one audit history across two untracked backends.
