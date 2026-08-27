# Encrypted NAS recovery and NetBox RBAC

## Security model

The NAS repository is a recovery copy, not the primary artifact backend. NetBox
continues writing configuration artifacts to its local protected volume. A
dedicated sidecar periodically creates one encrypted Restic snapshot containing:

- a PostgreSQL custom-format dump;
- configuration artifacts and receiver host keys;
- the protected `config-backup.env` file, including the plugin master key;
- NetBox configuration files;
- a small component manifest.

`config-backup-nas.env` and its `RESTIC_PASSWORD` are deliberately excluded.
Keep that password in a separate password manager and in an offline recovery
escrow. Without it, the NAS snapshots cannot be restored. Anyone who obtains it
and the repository can recover the included master key and database credentials.

The NAS should provide TLS, a dedicated service identity, snapshots or immutable
retention, and a separate administrator account. Restrict network access to the
Docker host. A permanently writable repository without NAS snapshots is not a
complete ransomware boundary.

## NAS setup

From the `netbox-docker` directory, copy the protected template:

```powershell
Copy-Item env/config-backup-nas.env.example env/config-backup-nas.env
```

Set at least:

```text
RESTIC_REPOSITORY=s3:https://nas.example.com/netbox-backups
RESTIC_PASSWORD=<long unique recovery password>
AWS_ACCESS_KEY_ID=<dedicated backup identity>
AWS_SECRET_ACCESS_KEY=<dedicated backup secret>
```

On a Windows Docker host, protect both env files after creation:

```powershell
../netbox-plugin-backup/docker/protect-windows-secrets.ps1 `
  -NetBoxDockerPath C:\dev\netbox-docker
```

Restic repository URLs may also point at a supported REST server or another
S3-compatible endpoint. Do not reuse a NAS administrator account.

For the first run only, set `RESTIC_AUTO_INIT=true`, then execute:

```powershell
docker compose `
  -f docker-compose.yml `
  -f ../netbox-plugin-backup/docker/docker-compose.nas-backup.yml `
  --profile nas-backup `
  run --rm netbox-config-backup-nas backup-now
```

After the first successful snapshot, set `RESTIC_AUTO_INIT=false`. Enable the
status card in `env/config-backup.env`:

```text
NETBOX_CONFIG_BACKUP_NAS_ENABLED=true
```

Start the daily scheduler and restart the NetBox web process so it reads the
status setting:

```powershell
docker compose `
  -f docker-compose.yml `
  -f ../netbox-plugin-backup/docker/docker-compose.nas-backup.yml `
  --profile nas-backup `
  up -d --build netbox-config-backup-nas

docker compose up -d --force-recreate netbox
```

Defaults retain 7 daily, 4 weekly, and 12 monthly snapshots. The sidecar runs
`restic check` after every backup and writes only a safe timestamp and snapshot
ID to `/var/lib/netbox-config-backup/nas-backup/`. It never writes a repository
URL or credential to NetBox.

Run a non-destructive recovery validation regularly:

```powershell
docker compose `
  -f docker-compose.yml `
  -f ../netbox-plugin-backup/docker/docker-compose.nas-backup.yml `
  --profile nas-backup `
  run --rm netbox-config-backup-nas restore-check
```

This restores the latest snapshot into a temporary container directory, checks
the PostgreSQL archive, verifies required components, and deletes the temporary
copy. It does not overwrite the running database or artifact volume.

## NetBox RBAC groups

Create or refresh the standard groups and their object permissions:

```shell
python manage.py config_backup_create_rbac_groups
```

The command is idempotent and never assigns users automatically:

- **Config Backup Readers** can view targets, runs, revisions, redacted content,
  and diffs.
- **Config Backup Operators** can additionally view plugin configuration, run
  and test backups, reschedule targets, and protect revisions.
- **Config Backup Administrators** have full CRUD access to plugin models.

Assign people under **Admin → Authentication → Groups**. Prefer group assignment
over direct user permissions. Effective permissions are the union of all user
and group assignments, so review other group memberships as well.

Object constraints remain supported. The plugin dashboard, nested target
history, manual actions, retention actions, revision content, and diffs all use
NetBox restricted querysets so a guessed object ID cannot bypass a constraint.
Superusers remain unrestricted by design.
