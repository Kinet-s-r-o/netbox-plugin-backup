# NFS and SMB3 storage

Config Backup can keep an independent immutable copy of every revision on an
NFS share or a current Samba/Windows share using SMB3. SMB1 and NetBIOS-based
access are intentionally unsupported.

## Architecture and safety

The operating system or container runtime mounts the share. The plugin receives
only an absolute directory path and performs file operations below that path.
NetBox therefore needs no mount capability, kernel filesystem tools, or NAS
administrator credential.

The same mount must be available at the same absolute path in:

- the NetBox web container, so administrators can test the storage;
- the normal NetBox worker, which runs replication, audit, and retention jobs;
- the dedicated Config Backup worker when deployment jobs use that queue.

By default, UI-managed paths must be below `/mnt/netbox-config-backup` and the
selected directory must be an active mount. If a share disconnects and leaves
an ordinary local directory behind, operations fail with `MOUNT_NOT_ACTIVE`
instead of silently filling the container filesystem.

```python
PLUGINS_CONFIG = {
    "netbox_config_backup": {
        "network_storage_mount_roots": ["/mnt/netbox-config-backup"],
        "network_storage_require_mountpoint": True,
    }
}
```

Keep `network_storage_require_mountpoint` enabled in production. Add more
allowed roots only when the deployment deliberately uses them. Never allow `/`.

## NFS example for Docker Compose

This example uses NFSv4 and presents the volume at the same path in every
required service. Adapt the server and export path to the NAS.

```yaml
services:
  netbox:
    volumes:
      - config-backup-nfs:/mnt/netbox-config-backup/nfs
  netbox-worker:
    volumes:
      - config-backup-nfs:/mnt/netbox-config-backup/nfs
  netbox-config-backup-worker:
    volumes:
      - config-backup-nfs:/mnt/netbox-config-backup/nfs

volumes:
  config-backup-nfs:
    driver: local
    driver_opts:
      type: nfs
      o: addr=10.0.0.20,nfsvers=4,rw
      device: :/exports/netbox-config-backup
```

Restrict the NFS export to the Docker host or NetBox hosts. Use root squashing
and a dedicated UID/GID with read, create, rename, and delete rights inside the
export. Do not export a broader NAS directory than the plugin needs.

## SMB3/Samba example

Mount the share on the Docker host with SMB 3.1.1 and bind-mount it into the
containers. A host-managed credentials file keeps the password outside the
plugin, NetBox database, image, and repository.

```text
# /etc/netbox-config-backup/smb-credentials (mode 0600, owned by root)
username=netbox-backup
password=<secret>
domain=WORKGROUP
```

```shell
sudo mount -t cifs //10.0.0.30/netbox-config-backup \
  /srv/netbox-config-backup-smb \
  -o credentials=/etc/netbox-config-backup/smb-credentials,vers=3.1.1,rw,nosuid,nodev,noexec
```

```yaml
services:
  netbox:
    volumes:
      - /srv/netbox-config-backup-smb:/mnt/netbox-config-backup/smb:rw
  netbox-worker:
    volumes:
      - /srv/netbox-config-backup-smb:/mnt/netbox-config-backup/smb:rw
  netbox-config-backup-worker:
    volumes:
      - /srv/netbox-config-backup-smb:/mnt/netbox-config-backup/smb:rw
```

Use TCP 445, SMB 3.1.1, signing, and encryption when supported by the NAS.
Disable SMB1 on both the server and client. Grant the dedicated account access
only to this share.

## Create the storage in NetBox

1. Open **Config Backup → Storages → Add**.
2. Select **NFS mount** or **SMB3 / Samba mount**.
3. Enter the exact mounted directory, for example
   `/mnt/netbox-config-backup/nfs`.
4. Enter a base directory such as `netbox-config-backup`.
5. Select an optional remote retention profile. Enable storage enforcement only
   when device-level retention must not override it.
6. Save, open the storage, and select **Test storage**.
7. Select **Copy existing revisions** when the share starts empty.
8. Run **Check stored copies** and review retention preview before enabling
   automatic remote cleanup.

The test creates a random small object, reads it back, verifies its content, and
deletes it. Normal replication then writes immutable revision directories and a
SHA-256 manifest. Integrity audit is read-only. Retention deletes only the exact
recorded file set for an expired revision and stops if it finds an unexpected
entry.

## Failure codes

- `MOUNT_UNAVAILABLE`: the configured directory does not exist in the worker.
- `MOUNT_NOT_ACTIVE`: the directory exists but is not an active mount.
- `MOUNT_PATH_NOT_ALLOWED`: the path is outside the configured allowed roots.
- `DESTINATION_PATH_DENIED`: the worker lacks filesystem permissions.
- `DESTINATION_CONFLICT`: a different file already occupies an immutable path.
- `DESTINATION_VERIFY_FAILED`: a written file failed immediate verification.

After changing a mount or plugin configuration, restart the web and worker
processes and rerun **Test storage** before relying on automatic replication.
