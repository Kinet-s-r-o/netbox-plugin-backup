# Master-key rotation

The encrypted-database credential provider uses AES-256-GCM. Each credential is
bound to its UUID and `key_version` through authenticated additional data. The
active key encrypts all new passwords; a temporary previous-key keyring allows
old rows to remain readable while rotation is in progress.

## Environment

```dotenv
NETBOX_CONFIG_BACKUP_MASTER_KEY=<new 32-byte base64url key>
NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION=2
NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS={"1":"<old 32-byte base64url key>"}
```

Key versions may contain letters, numbers, `_`, `-`, and `.`, up to 50
characters. Keys must decode to exactly 32 bytes and every version must use
different key material. At most 16 previous keys are accepted. Do not put the
key values in tickets, shell history, application logs, screenshots, or the
Restic repository password file.

Store a protected recovery copy of the current environment and PostgreSQL
backup before rotating. The recovery copy must be encrypted and access-limited;
without the corresponding key, an older PostgreSQL backup containing encrypted
credentials is not fully recoverable.

## Safe Docker procedure

1. Verify the current state. This is read-only and decrypts every credential in
   memory without printing secret values:

   ```console
   docker compose exec -T netbox python /opt/netbox/netbox/manage.py \
     config_backup_rotate_master_key
   ```

2. Generate a new cryptographically random 32-byte key in the deployment's
   secret manager. Give it a new, never-reused version.
3. Move the old version and key into
   `NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS`, make the new key active, and
   restart every web and worker container which can resolve credentials.
4. Run the read-only command again. It must report the expected number in
   `pending_rotation` and no error.
5. Apply one atomic database transaction, explicitly confirming the active
   version:

   ```console
   docker compose exec -T netbox python /opt/netbox/netbox/manage.py \
     config_backup_rotate_master_key --apply --expected-active-version 2
   ```

6. Run the dry-run again. It must report `pending_rotation=0`. Test one device
   connection and one real read-only backup.
7. Create and verify a new encrypted recovery snapshot containing the new
   master-key environment.
8. After the rollback window, remove the old entry from
   `NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS`, restart web and workers, and run
   the dry-run one final time.

For a Windows Docker host using the supplied protected env file, preparation
can generate the key and update the keyring without displaying secret material:

```powershell
.\docker\prepare-windows-master-key-rotation.ps1 `
  -EnvFile C:\path\to\netbox-docker\env\config-backup.env `
  -NewVersion 2
```

After successful apply, verification, and recovery backup, remove the previous
runtime key with:

```powershell
.\docker\finalize-windows-master-key-rotation.ps1 `
  -EnvFile C:\path\to\netbox-docker\env\config-backup.env `
  -ExpectedActiveVersion 2 -RemoveVersion 1
```

Run `protect-windows-secrets.ps1` after either operation and recreate the web
and worker containers so they receive the updated environment.

The Settings page reports the active version and counts of current, pending,
and unavailable credentials. It never displays a key, password, ciphertext,
nonce, username, or credential UUID.

## Failure and rollback

The apply command locks credential rows, verifies every password, and updates
all outdated rows in one transaction. A missing key, invalid authentication
tag, corrupt ciphertext, or process failure rolls the transaction back.

Before the previous key is removed, rollback is possible:

1. Configure the old key as active and retain the new key in the previous-key
   keyring.
2. Restart web and workers.
3. Run dry-run.
4. If database rollback is also required, apply rotation toward the old active
   version using its exact version in `--expected-active-version`.

Never restore only PostgreSQL without restoring the matching master-key
environment. Never delete the previous key merely because the apply command
started; remove it only after verification and a successful recovery snapshot.
