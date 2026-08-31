# Deployment guide

This plugin is installed into every NetBox process which imports it: web,
background worker, housekeeping/dispatcher, and the optional SFTP receiver.
Use the same package version and configuration in all of them.

Supported runtime: NetBox 4.6.x and Python 3.12-3.14. PostgreSQL and Redis are
provided by NetBox; they are not bundled with this plugin.

## 1. Install the package

Install the wheel into NetBox's virtual environment. A source checkout also
works, but a pinned wheel is recommended for production.

```shell
/opt/netbox/venv/bin/pip install netbox_config_backup-0.7.1-py3-none-any.whl
```

The package installs its runtime dependencies, including Netmiko, Paramiko,
Cryptography, and AsyncSSH. Do not install a separate SFTP server image.

For netbox-docker, build a non-editable image directly from the repository:

```shell
docker build -f docker/Dockerfile.release \
  --build-arg NETBOX_IMAGE=netboxcommunity/netbox:v4.6-5.0.2 \
  -t netbox-config-backup:0.7.1 .
```

Use that exact same image for the NetBox web, normal worker, housekeeping,
dedicated backup worker, and receiver services.
`docker/docker-compose.receiver.yml` is an overlay for the standard
netbox-docker Compose file which does this wiring.

## 2. Configure NetBox

```python
PLUGINS = ["netbox_config_backup"]

PLUGINS_CONFIG = {
    "netbox_config_backup": {
        "storage_root": "/var/lib/netbox-config-backup",
        "storage_backend": "local",
        # Allowed roots for host/runtime-mounted NFS and SMB3 shares.
        "network_storage_mount_roots": ["/mnt/netbox-config-backup"],
        "network_storage_require_mountpoint": True,
        # Temporary, verified ZIPs prepared from successful FTP replicas.
        "recovery_package_ttl_minutes": 60,
        "recovery_package_max_bytes": 1024 * 1024 * 1024,
        "receiver_root": "/var/lib/netbox-config-backup/receiver",
        "receiver_host_key_path": (
            "/var/lib/netbox-config-backup/receiver/ssh_host_ed25519_key"
        ),
        "receiver_rsa_host_key_path": (
            "/var/lib/netbox-config-backup/receiver/ssh_host_rsa_key"
        ),
        "events_enabled": True,
        "notify_on_every_failure": False,
        "metrics_enabled": False,
    }
}
```

For HashiCorp Vault KV v2 or S3-compatible artifact storage, apply the
configuration and migration procedure in [VAULT_AND_S3.md](VAULT_AND_S3.md).
Never change an existing installation to S3 until the verification/copy command
has completed successfully.

Set `NETBOX_CONFIG_BACKUP_MASTER_KEY` and
`NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION` through the platform's secret store.
The web, worker, and receiver must receive the same values. Never put the key in
the database, Git, a driver option, or a Compose file committed to source
control.

Generate a new 256-bit key once (do not reuse this example output):

```shell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Set the version to `1` for a new installation. Back up the key separately from
PostgreSQL and the artifact volume; losing it makes database-encrypted device
passwords unrecoverable.

Create the data root once and make it writable only by the NetBox service user:

```shell
install -d -o netbox -g netbox -m 0700 /var/lib/netbox-config-backup
```

For Docker, mount one named volume at this path into the worker and receiver.
The web process needs the storage volume only when users are allowed to view
revision content.

The web and dedicated backup worker must share the same `storage_root`. Verified
FTP recovery ZIPs are created under its private `.recovery-packages` directory,
use file mode `0600`, expire after `recovery_package_ttl_minutes`, and are
removed by an hourly system job. `recovery_package_max_bytes` limits the total
uncompressed FTP source data accepted for one package.

Optional NFS and SMB3 secondary storage is mounted by the host or container
runtime, never by the NetBox process. Present the same absolute mount path to
the web and worker services and keep active-mount verification enabled. See
[NFS_AND_SMB3_STORAGE.md](NFS_AND_SMB3_STORAGE.md) for NFSv4 and SMB 3.1.1
examples and the required permissions.

## 3. Initialize or upgrade the database and static files

This step is mandatory on a clean installation as well as on an upgrade. The
migration command creates missing plugin tables or applies only pending schema
changes, so it can be run safely in either case.

```shell
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py migrate netbox_config_backup
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
```

Restart the NetBox web and worker processes. Run a dedicated queue worker:

```shell
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py rqworker netbox_config_backup.backup
```

RQ workers and the receiver are long-running Python processes. Restart the web,
normal worker, dedicated backup worker, and receiver after every plugin upgrade,
including development deployments which bind-mount changed Python source.

### Retention workers and safe defaults

Local and remote retention use the same dedicated backup queue but are dispatched
independently. In **Config Backup > Settings**, enable the local scheduler and
remote scheduler separately only after reviewing representative device previews.
Both schedulers are disabled by default.

The storage-profile migration creates exactly one protected **Local storage**
row representing the deployment's configured `storage_root`. It is always
enabled and cannot be deleted, disabled, or changed to a remote type. Existing FTP rows
remain FTP storages with their transport and replication behavior unchanged.
Their new retention-policy fields are empty and policy enforcement is disabled,
so the migration alone cannot make existing history eligible for deletion.

Before enabling remote cleanup for the first time on an upgraded installation,
run the read-only integrity audit for every remote storage. Resolve each missing
object, size/hash mismatch, and unexpected path among historical replica rows
recorded as successful before assigning/enabling FTP cleanup.

Retention is resolved separately for the Local storage and for every remote
storage. A storage policy is normally a fallback; **Always use this storage's
retention profile** makes it mandatory and prevents device overrides. The exact
precedence is:

1. Local: enforced storage policy, device Local override, backup-policy
   retention, Local-storage fallback, then keep indefinitely.
2. Each remote storage: enforced storage policy, device remote override, that
   storage's fallback, then keep indefinitely.

The Local policy controls primary artifacts, revision history, and completed
run records. Each remote plan controls only copies on that remote storage. The
`max_copies_per_target` limit therefore applies per device per remote storage: one
revision on two storages consumes one slot on each, while multiple artifact
files in the revision still consume one slot. A protected revision and the
latest usable copy are retained in every scope.

FTP cleanup requires the storage account to delete files and remove the
unique revision directory in addition to the upload, download, listing, rename,
and directory-creation operations used by normal replication and testing.
Grant these rights only inside the storage's dedicated chroot/base path.
The plugin validates the recorded path before deletion and never deletes the
storage base, device directory, or unrelated objects.

FTP deletion has no quarantine and is irreversible. Failed or partial deletion
is recorded and retried safely; it never changes a successful BackupRun to
failed. A successfully expired remote copy is retained as an audit state so
that integrity repair, unchanged backups, and ordinary backfill do not upload
it again. A later increase of the retention period does not resurrect deleted
copies automatically. This state is not a permanent audit ledger: after the
local revision and all corresponding FTP copies have fully expired, cleanup may
remove the revision and its replica/deletion metadata as well.

## 4. Create a device upload receiver in the UI

This section is required only for drivers where the device pushes a native file,
currently Ceragon IP-50/CeraOS and first-generation SIAE ALFOplus.

1. In **Config Backup > Settings > Credential profiles**, create a dedicated
   encrypted database credential. Use a unique username and password. CeraOS
   CLI-safe values contain letters, numbers and `._@%+,:=-`; the password must
   be at least eight characters.
2. In **Config Backup > Settings > Security and vendor-specific setup > Device upload receivers**, create a profile named
   `default` and select that credential.
3. Prefer **Direct from device** when devices can reach the receiver. Set
   `advertised_host` to the stable DNS name or IP visible from the management
   network and expose `advertised_port` through the firewall.
4. Use **Reverse SSH tunnel** when the device cannot initiate a connection to
   the NetBox host. The worker opens a temporary remote forward bound to
   `127.0.0.1` on the device and bridges it to `bridge_host:bridge_port`.
5. Restrict the exposed receiver port at the network firewall to management
   subnets or individual devices.

Start the receiver after the profile exists. `--wait` is convenient for a
container which may start before migrations or UI configuration are ready:

```shell
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py \
  config_backup_receiver --profile default --wait
```

The process generates persistent Ed25519 and RSA host keys. RSA is included for
older embedded SFTP clients such as deployed CeraOS releases. Back up both keys and keep the receiver volume across
container recreation. The endpoint accepts password-authenticated SFTP uploads
only: shell, command execution, public-key login, reads, and symbolic links are
not enabled.

For netbox-docker, merge `docker/docker-compose.receiver.yml` with the normal
Compose files. Put the two master-key variables into the existing protected
`env/netbox.env` (or inject them from a container secret), set `NETBOX_IMAGE`,
and adjust the published receiver port when needed:

```shell
NETBOX_IMAGE=netbox-config-backup:0.7.1 docker compose \
  -f docker-compose.yml \
  -f /path/to/netbox-plugin-backup/docker/docker-compose.receiver.yml \
  up -d
```

The web, workers, and receiver share `netbox-config-backup-data`. Keep the SFTP
receiver profile's listen port at 2022 when using the supplied health check, or
adjust that check together with the profile.

### Optional legacy ALFOplus FTP receiver

First-generation ALFOplus cannot upload its documented native backup over SFTP.
Create a second receiver profile named `legacy-alfoplus` with protocol **Legacy
FTP**, direct mode, listen port `2121`, advertised port `21`, and a dedicated
credential whose username and password are each at most eight characters. Set
the advertised host to the address which the radio sees as the WebLCT client.

Start the optional Compose service explicitly:

```shell
docker compose --profile legacy-alfoplus up -d \
  config-backup-legacy-ftp-receiver
```

Publish TCP 21 and the configured passive range (the supplied file uses
30000-30009). Restrict all of these ports to the radio management network. The
FTP service is chrooted and upload-only: it does not permit anonymous login,
file download, listing, deletion, rename, append, or directory creation. FTP and
the old WebLCT HTTP login are nevertheless plaintext legacy protocols.

In Quick Setup select the automatic SIAE driver and this receiver, then confirm
device-side export. The device account must be SYSTEM or Station Operator; a
Read/Write or Monitor role may log in successfully but is not allowed to create
a native backup. Prefer configuring the receiver profile with the FTP login
already stored on the radios. The optional **Configure the legacy FTP login on
ALFOplus** checkbox is an explicit device write and should be used only on
firmware which permits WebLCT to update those transfer credentials.

If the run reports `DEVICE_EXPORT_FAILED`, inspect the legacy receiver log. No
connection entry means the radio cannot open TCP 21 to the WebLCT client address
it sees; correct routing, firewall, or destination NAT and make
`advertised_host` match that address. A connection followed by failed login
means the receiver credential does not match the radio's FTP transfer login.

## 5. Configure Ceragon IP-50

Create or edit the platform mapping:

- Driver: `ceragon_ip50`
- Connection profile: device SSH profile
- Credential profile: device login
- Device upload receiver: the receiver created above, using the SFTP protocol
- Driver options:

```json
{
  "allow_device_export": true,
  "restore_point": "restore-point-1",
  "restore_sftp_port": true,
  "backup_settle_seconds": 15
}
```

The driver performs only the vendor's backup/export workflow. It configures the
SFTP transfer channel, creates or replaces the selected restore point, exports
the ZIP, validates it, extracts `config_dump.txt` for deterministic change
detection, stores both artifacts, and removes the transient inbox copy. It never
imports, restores, deletes, resets, or installs software. Because
the CeraOS SFTP port is global, `restore_sftp_port` should normally remain true;
the detected previous port is restored in `finally` even if export fails.

After upgrading an installation which already contains Ceragon IP-50 revisions,
run the backfill first without and then with `--apply`:

```bash
python manage.py config_backup_backfill_ceragon_content
python manage.py config_backup_backfill_ceragon_content --apply
```

The command validates every native ZIP, adds the extracted primary text artifact,
and recomputes semantic change flags. It does not delete or overwrite native ZIPs.

## 6. Verification and upgrade procedure

Before enabling schedules:

1. Run Django's `check` and `showmigrations netbox_config_backup`.
2. Open the plugin overview and verify permissions.
3. Use **Save & test connection** on one non-critical device.
4. Verify that the resulting ZIP revision is downloadable/viewable only by the
   intended NetBox roles.
5. Enable schedules gradually and monitor Failed, Stale, and Stuck counts.
6. Verify **Config Backup > Storages** contains exactly one enabled default Local
   storage which cannot be deleted. Configure a fallback policy on a test FTP
   storage and inspect its retention preview.
7. Test both storage modes: with enforcement disabled, confirm the device
   override wins; with **Always use this storage's retention profile** enabled,
   confirm the storage policy wins. Verify protected/latest revisions are kept.
8. If two FTP storages are available, verify their plans and
   `max_copies_per_target` counts are independent for the same device.
9. Enable local and FTP automatic cleanup separately. Confirm that a
   device/storage pair with no effective FTP policy keeps its copies and that an
   expired test copy is not recreated by an unchanged backup.

For upgrades, back up PostgreSQL, the backup-storage volume, the receiver host
key, and the master key. Install the same new wheel everywhere, run migrations
and `collectstatic`, then run:

```bash
python manage.py config_backup_create_rbac_groups
```

This idempotently refreshes the plugin-managed object permissions for models
introduced by the new version. It does not assign users or remove existing
group membership. Restart web, worker, dispatcher, and receiver processes only
after these upgrade steps succeed.

Before enabling or re-enabling automatic FTP cleanup, run the read-only FTP
integrity audit and investigate every missing object, hash mismatch, and
unexpected remote path. Keep FTP cleanup disabled until the audit is clean;
cleanup permanently removes eligible remote copies and is not a repair tool.

### Receiver failure codes

- `DEVICE_DID_NOT_CONNECT_RECEIVER`: CeraOS never opened the SFTP connection;
  check the receiver profile, reverse tunnel, export state, and device settings.
- `RECEIVER_BRIDGE_FAILED`: the tunnel opened but the backup worker could not
  reach the receiver service at its configured bridge host and port.
- `RECEIVER_UPLOAD_TIMEOUT`: CeraOS connected, but the expected uniquely named
  file was not completed before `export_timeout`. Check the run detail message,
  receiver health, and whether a previous export is still active on the device.

Failed runs are audit records and can remain in NetBox. After correcting the
cause, create a new manual run rather than editing the failed record.

## 7. Notifications and Prometheus

The plugin uses NetBox Event Rules rather than storing SMTP or webhook secrets.
Create a Notification Group first, then create separate Event Rules for the
desired Config Backup object and event type:

| Object type | Event type |
| --- | --- |
| Backup run | Configuration backup failed |
| Backup run | Configuration backup recovered |
| Backup run | Configuration backup run is stuck |
| Backup target | Configuration backup target is stale |

Select a Notification Group, webhook, or script as the action. A rule may
filter on allowlisted payload fields such as `status`, `error_code`,
`consecutive_failures`, or `device`. Notification delivery errors are logged
but never roll back or alter a completed backup transaction.

To expose plugin metrics, enable NetBox's Prometheus endpoint and the plugin
collector:

```python
METRICS_ENABLED = True
PLUGINS_CONFIG["netbox_config_backup"]["metrics_enabled"] = True
```

Restart the web service and verify `/metrics` from the Prometheus network.
Protect this endpoint at the reverse proxy or firewall: it contains aggregate
operational state, even though the plugin intentionally exports no per-device,
IP, site, user, error-message, or configuration labels.

## 8. Dependency security baseline

The package requires `sqlparse>=0.6,<1` so an installation also replaces the
vulnerable 0.5.x release currently present in some NetBox base images. Run a
dependency audit again whenever the NetBox image or this plugin is upgraded.

Netmiko 4.7 currently requires Paramiko `<5`. Paramiko 4.0 is reported for
CVE-2026-44405 because its RSA implementation still contains SHA-1 support.
The plugin mitigates this for all Paramiko/Netmiko client sessions by disabling
the `ssh-rsa` key and public-key algorithms while retaining RSA keys through
`rsa-sha2-256/512`. Do not remove this restriction to accommodate an obsolete
device; isolate or upgrade that device instead. Upgrade Netmiko and Paramiko as
soon as a compatible patched release is available.
