"""Protocol-level smoke test for disposable Vault and S3 emulator containers."""

import os

import boto3
import hvac

from netbox_config_backup.credentials.vault import VaultKV2SecretProvider
from netbox_config_backup.storage.base import StorageError
from netbox_config_backup.storage.s3 import S3ConfigStorage

vault_addr = os.environ["TEST_VAULT_ADDR"]
vault_token = os.environ["TEST_VAULT_TOKEN"]
s3_endpoint = os.environ["TEST_S3_ENDPOINT"]
bucket = "netbox-config-backup-integration"

provider = VaultKV2SecretProvider(
    {
        "vault_enabled": True,
        "vault_addr": vault_addr,
        "vault_auth_method": "token",
        "vault_verify_tls": False,
        "vault_allow_insecure_http": True,
        "vault_timeout": 5,
    },
    {"NETBOX_CONFIG_BACKUP_VAULT_TOKEN": vault_token},
)
material = provider.resolve("vault://secret/network/devices/router-1")
assert material.username == "integration-backup"
assert material.password == "integration-password"

# Exercise the production-oriented AppRole flow against a real Vault API.
admin = hvac.Client(url=vault_addr, token=vault_token)
admin.sys.enable_auth_method(method_type="approle")
admin.sys.create_or_update_policy(
    name="netbox-config-backup-integration",
    policy='path "secret/data/network/devices/*" { capabilities = ["read"] }',
)
admin.auth.approle.create_or_update_approle(
    role_name="netbox-config-backup",
    token_policies=["netbox-config-backup-integration"],
    token_ttl="5m",
    token_max_ttl="10m",
)
role_id = admin.auth.approle.read_role_id("netbox-config-backup")["data"]["role_id"]
secret_id = admin.auth.approle.generate_secret_id("netbox-config-backup")["data"]["secret_id"]
approle_provider = VaultKV2SecretProvider(
    {
        "vault_enabled": True,
        "vault_addr": vault_addr,
        "vault_auth_method": "approle",
        "vault_auth_mount_point": "approle",
        "vault_verify_tls": False,
        "vault_allow_insecure_http": True,
        "vault_timeout": 5,
    },
    {
        "NETBOX_CONFIG_BACKUP_VAULT_ROLE_ID": role_id,
        "NETBOX_CONFIG_BACKUP_VAULT_SECRET_ID": secret_id,
    },
)
approle_material = approle_provider.resolve("vault://secret/network/devices/router-1")
assert approle_material.username == "integration-backup"
assert approle_material.password == "integration-password"

client = boto3.client(
    "s3",
    endpoint_url=s3_endpoint,
    region_name="eu-central-1",
    aws_access_key_id="integration-access",
    aws_secret_access_key="integration-secret",
)
client.create_bucket(
    Bucket=bucket,
    CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
)
storage = S3ConfigStorage(
    bucket=bucket,
    prefix="integration",
    endpoint_url=s3_endpoint,
    region="eu-central-1",
    allow_insecure_http=True,
    server_side_encryption="AES256",
    client=client,
)
key = "devices/1/revisions/integration/running-config.txt"
content = b"hostname integration-router\n"
storage.put(key, content, {"driver_id": "integration"})
assert storage.get(key) == content
head = client.head_object(Bucket=bucket, Key=f"integration/{key}")
assert head["ServerSideEncryption"] == "AES256"

try:
    storage.put(key, b"must-not-overwrite")
except StorageError:
    pass
else:
    raise AssertionError("S3 put unexpectedly overwrote an existing artifact")

staged = storage.stage_delete(key, "integration-cleanup")
assert staged is not None
assert not storage.exists(key)
assert storage.exists(staged)
storage.restore_staged_delete(key, staged)
assert storage.get(key) == content
storage.delete(key)
assert not storage.exists(key)

print("EXTERNAL_SERVICES_SMOKE_OK")
