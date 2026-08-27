# NetBox Config Backup

`netbox_config_backup` is a NetBox 4.6 plugin for controlled, auditable backups of
network device configurations. The current implementation includes the core
pipeline, authenticated NetBox UI, scheduled RQ dispatcher, and a catalog of
read-only SSH drivers backed by Netmiko.

## Development status

Implemented:

- NetBox `PluginConfig`, dashboard, navigation, and configuration UI
- task-oriented Settings page and permission-aware, read-only Help center
- initial Django models and migration
- `BackupDriver`, `DriverRegistry`, and `FakeDriver`
- `ConfigStorage` and `LocalConfigStorage`
- `SecretProvider`, safe credential material, provider registry, and an environment provider
- shared Netmiko 4 transport with configurable SSH identity verification and stable safe errors
- read-only MikroTik RouterOS driver using a terse, secret-hidden text export
- read-only Cisco IOS and IOS-XE drivers using `show running-config`
- declarative Dell, FS, HP, Huawei, TP-Link, Ubiquiti, and ZTE driver profiles
- external driver discovery through versioned Python entry points
- background `Test connection` action which validates collection without storing a revision
- framework-independent backup pipeline plus Django ORM repository adapter
- manual `Run backup` action and dedicated `netbox_config_backup.backup` RQ queue
- minutely scheduled dispatcher for interval/daily policies and site time zones
- deterministic daily jitter, retry/backoff, deduplication, and stale-run reconciliation
- schedule-aware stale-target detection with separate stuck-run classification
- health dashboard with Healthy, Stale, Failed, and Stuck counts plus recent diagnostics
- native NetBox Event Rule events for first failure, recovery, stale targets, and stuck runs
- opt-in low-cardinality Prometheus metrics backed by current database state
- HashiCorp Vault KV v2 credential provider with token or AppRole authentication
- private, encrypted Amazon S3/S3-compatible artifact storage and migration command
- encrypted Restic recovery snapshots for PostgreSQL, artifacts, receiver keys, and master-key environment
- idempotent least-privilege Reader, Operator, and Administrator NetBox groups
- permission-aware dashboard, nested history, actions, revision content, and diffs
- target/run list filters for status, failure, stuck execution, device, site, and time
- UI-managed storage profiles with a protected default Local storage plus FTP, NFS, and SMB3 storages
- per-storage retention defaults, optional enforcement, and conservative per-storage previews
- protected/latest revision safeguards, confirmed cleanup, and reversible local quarantine
- separately opt-in local and remote retention dispatchers with deduplication and batch limiting
- list/detail views for targets, runs, revisions, policies, mappings, and profiles
- integrity-checked, redacted revision content viewer and same-target unified diff
- NetBox REST endpoints and event serializers for all public plugin models
- safe target removal including runs, revisions, stored artifacts, and unshared Quick profiles
- unit tests for the driver, registry, storage, credentials, and pipeline
- authenticated Docker UI/dispatcher smoke test
- built-in password-only, chrooted SFTP receiver for vendor-native push exports
- direct and loopback-only reverse-tunnel receiver modes for Ceragon IP-50/CeraOS
- UI-managed replication of completed revisions to FTP or pre-mounted NFS/SMB3 storage
- independent replica status, automatic retry, immutable paths, and SHA-256 verification

Deferred by design:

- vendor drivers without a verified transport and read-only backup command
- quarantine housekeeping

## Install in NetBox

The supported production path for release `0.7.x` is NetBox 4.6, local primary
artifact storage, and optional FTP, NFS, or SMB3 replication configured from the UI. Vault,
S3, and external SFTP replication are not required for a standard installation.
The S3 and Vault client libraries are optional package extras; install
`netbox-config-backup[s3]` or `netbox-config-backup[vault]` only when enabling
those advanced backends.

Read the [compatibility matrix](COMPATIBILITY.md) first. Install the package
into the same Python environment as every NetBox process. For a traditional
installation, pin the wheel in `/opt/netbox/local_requirements.txt`; for
netbox-docker, build and use the supplied release image. The complete procedure
is in [docs/INSTALLATION.md](docs/INSTALLATION.md).

Review [SECURITY.md](SECURITY.md) before a production deployment, especially
the host-key, master-key, RBAC, FTP-network, and dependency requirements.
Maintainers should use the repeatable checklist in
[docs/RELEASING.md](docs/RELEASING.md) before publishing a package or image.

Enable the plugin:

```python
PLUGINS = ["netbox_config_backup"]

PLUGINS_CONFIG = {
    "netbox_config_backup": {
        "storage_root": "/var/lib/netbox-config-backup",
        "storage_backend": "local",
        "network_storage_mount_roots": ["/mnt/netbox-config-backup"],
        "network_storage_require_mountpoint": True,
        "receiver_root": "/var/lib/netbox-config-backup/receiver",
        "receiver_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_ed25519_key",
        "receiver_rsa_host_key_path": "/var/lib/netbox-config-backup/receiver/ssh_host_rsa_key",
        "events_enabled": True,
        "notify_on_every_failure": False,
        "metrics_enabled": False,
    }
}
```

Then apply migrations with NetBox's `manage.py`:

```text
python manage.py migrate netbox_config_backup
python manage.py collectstatic --no-input
python manage.py check
```

Run a dedicated worker for the custom queue so device backups cannot be starved
by a busy default queue:

```text
python manage.py rqworker netbox_config_backup.backup
```

The development Docker Compose override defines this process as the
`netbox-config-backup-worker` service.

Before creating credentials in the UI, generate and securely inject one
`NETBOX_CONFIG_BACKUP_MASTER_KEY` plus version `1` into the web and all worker
processes. Never commit the key or store it in PostgreSQL. See the installation
guide for the exact command and required volume permissions.

For a clean Docker or systemd deployment, including the optional SFTP receiver,
follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The receiver runs from this
same Python package; no third-party SFTP image with bootstrap passwords is
required.

For external secret and artifact services, including least-privilege policy
examples and the required local-to-S3 migration order, follow
[docs/VAULT_AND_S3.md](docs/VAULT_AND_S3.md).
`docker/config-backup.env.example` contains every related environment variable
without any usable credential and can be copied into the deployment's protected
environment directory.

For encrypted NAS recovery snapshots and least-privilege Reader/Operator/Admin
groups, follow [docs/NAS_BACKUP_AND_RBAC.md](docs/NAS_BACKUP_AND_RBAC.md).

For a secondary copy of every completed revision to an internal FTP server,
follow [docs/FTP_DESTINATION.md](docs/FTP_DESTINATION.md).

For NFS or current Samba/SMB3 storage mounted by the host or container runtime,
follow [docs/NFS_AND_SMB3_STORAGE.md](docs/NFS_AND_SMB3_STORAGE.md).

For transactional credential master-key rotation and rollback, follow
[docs/MASTER_KEY_ROTATION.md](docs/MASTER_KEY_ROTATION.md).

For least-privilege assignment, in-app Event Rules, and the optional Prometheus
service, follow
[docs/RBAC_NOTIFICATIONS_MONITORING.md](docs/RBAC_NOTIFICATIONS_MONITORING.md).

## Health monitoring

The minutely backup dispatcher also refreshes target health. For an enabled
target with an enabled policy, the current schedule determines the next
expected successful backup. The target becomes `Stale` after that deadline plus
`stale_target_grace_minutes`, which defaults to 60 minutes. A latest failed
attempt remains `Failed` and takes precedence over stale classification.

A stuck `BackupRun` is a separate execution condition. Queued or running jobs
older than `stale_run_minutes` (120 minutes by default) are exposed by the
dashboard and run filters. The dispatcher reconciles an abandoned run to
`Errored` with error code `STALE_RUN` only when its NetBox background job is no
longer active.

The overview links each health count to a filtered target or run list and shows
the latest safe failure codes/messages. The same filters are available in the
REST API for backup targets and runs.

## Notifications and metrics

The plugin registers eight event types for NetBox Event Rules:

- **Configuration backup failed** for the first failed attempt in an incident
- **Configuration backup recovered** for the first later successful backup
- **Configuration backup target is stale** when a target crosses its expected deadline
- **Configuration backup run is stuck** when an abandoned run is reconciled
- **Configuration backup replica failed** when an FTP storage first fails
- **Configuration backup replica recovered** after the next successful copy
- **FTP integrity audit found problems** when a scheduled audit fails or detects damage
- **FTP integrity audit recovered** after the storage passes a later audit

Create Event Rules in NetBox and select the matching Config Backup **Backup run**,
**Backup target**, **Revision replica**, or **Storage** object type. NetBox can deliver matching events to a
Notification Group, webhook, or custom script. The event payload is an explicit
allowlist: IDs, device display name, state, error code, failure count, and
timestamps. It never includes an address, credential, driver option, or
configuration content. Repeated failures are suppressed by default; set
`notify_on_every_failure` to `True` only when every retry must generate an event.

Prometheus metrics are disabled by default. Enable both NetBox
`METRICS_ENABLED` and plugin `metrics_enabled`, restart the web process, and
scrape NetBox's `/metrics` endpoint. Exported gauges cover target/run states,
bounded failure codes, stuck runs, revision count, logical artifact bytes, and
the latest successful backup timestamp. Per-device, address, site, username,
and arbitrary driver labels are deliberately excluded to protect topology and
avoid unbounded time-series cardinality.

## Environment credential provider

The built-in provider ID is `environment`. A credential profile with reference
`env://ROUTER_1` reads `ROUTER_1_USERNAME` and exactly one of
`ROUTER_1_PASSWORD` or `ROUTER_1_PRIVATE_KEY` from the NetBox/worker process
environment. `ROUTER_1_ENABLE_SECRET` is optional. Put only the reference in
NetBox; never put a password or private key in a database field.

## Netmiko transport

Vendor drivers can use the shared transport to inherit connection-profile
settings, in-memory password/private-key authentication, the selected SSH
server-identity policy, command timeouts, and guaranteed disconnect handling. Transport
failures are converted to stable codes such as `AUTH_FAILED`, `TIMEOUT`,
`HOST_KEY_FAILED`, and `CONNECTION_FAILED`; raw exception messages are not
persisted.

The registered `mikrotik_routeros` driver runs only the read-only command
`/export terse hide-sensitive`. It rejects empty exports, RouterOS partial
export errors, non-text output, and output above 5 MiB by default. A platform
mapping can lower or raise the limit (up to the 50 MiB safety ceiling) with
`{"max_output_bytes": 5242880}`.

The `cisco_ios` and `cisco_xe` drivers run only `show running-config` through
their corresponding Netmiko platform type. They reject command authorization
errors, incomplete or non-text output, and configurations above 10 MiB by
default. Their normalizer removes volatile `show` headers and last-change
timestamps while preserving configuration lines. Browser previews and diffs
mask common password, secret, community, ISAKMP, TACACS, RADIUS, and
pre-shared-key values. The raw configuration remains protected in storage.

Cisco accounts with privilege 15 need only the normal username/password. When
a separate enable secret is required, use the environment provider and set the
optional `<REFERENCE>_ENABLE_SECRET` variable. Driver options accept
`{"max_output_bytes": 10485760}` with a 50 MiB absolute safety ceiling.

### Built-in driver catalog

The registry currently includes these real network profiles:

- Cisco IOS and IOS-XE. The IOS driver supports independently trusted legacy
  `ssh-rsa` appliance host keys for older Catalyst hardware while retaining
  host-key verification when it is enabled and disabling legacy user-key authentication.
- Dell OS6, OS9/Force10, SmartFabric OS10, and PowerConnect
- FS/Fiberstore FSOS and FSOS v2
- HP/HPE Comware and HP/Aruba ProCurve
- Huawei VRP and VRP v8
- MikroTik RouterOS
- RACOM RipEX2, RAy2, and RAy3
- Ceragon IP-20 and IP-50/CeraOS native SFTP exports
- One user-facing SIAE SM-OS automatic driver. It selects SSH or legacy Telnet
  from the connection profile, validates `show running-config`, and falls back
  to the isolated WebLCT/FTP native workflow for first-generation ALFOplus when
  a legacy receiver profile has been explicitly approved.
- TP-Link JetStream
- Ubiquiti EdgeRouter and EdgeSwitch
- ZTE ZXROS

RACOM does not use a guessed Netmiko alias: RipEX2 uses its documented HTTPS
API, while RAy2/RAy3 load the device CLI environment, execute
`cli_cnf_backup_get`, and download the generated `cnf_backup.tgz` over SCP.
Ceragon IP-50 creates a restore point and pushes its
native ZIP to the plugin-managed SFTP receiver. Use `siae_smos_auto`; the
connection profile determines SSH or Telnet. Legacy SIAE driver IDs remain
registered as hidden compatibility backends and are not offered in normal UI
forms. The default method runs only `show running-config`; the result is a text snapshot of the SM-OS configuration
and not a full restorable `.bku`/`.bak` package. Prefer SSH. Its compatibility
exception permits the old
`ssh-rsa` appliance host key only for this driver, retains the configured
server-identity policy, disables SSH agent use, and does not enable legacy `ssh-rsa`
user-key authentication. The Telnet profile sends credentials and configuration
without transport encryption and should be limited to a trusted management
network or protected VPN.
First-generation ALFOplus firmware which returns `C interp: unknown symbol name
'running'` does not support this CLI snapshot. The plugin can run its documented
WebLCT `.bku` backup action and receive the file on a dedicated upload-only FTP
receiver. This legacy path is plaintext, direct-mode only, and must be isolated
to a trusted management network. It never runs WebLCT restore or revert actions.
The WebLCT login must have SYSTEM or Station Operator privileges.

The automatic SIAE driver's advanced native fallback and the Ceragon IP-20 profile download
native vendor backups from the device over SFTP. Their firmware-specific
`remote_path` is mandatory; an
optional `export_command` runs only when `allow_export_command` is explicitly
set to `true` in platform mapping options.

Fibrain, Hutke, IMCO Power, and Microsoft are not registered because the
installed Netmiko release has no corresponding backend. Ericsson IPOS and
MINI-LINK transports exist, but remain disabled until a
single safe configuration-export command and representative device output are
verified for the deployed product families.

Example driver options:

```json
{"remote_path": "exports/configuration.zip"}
```

```json
{
  "backup_method": "native",
  "native_model": "alfoplus2",
  "remote_path": "backup/configuration.bku",
  "export_command": "vendor-documented export command",
  "allow_export_command": true
}
```

Ceragon IP-50/CeraOS uses a receiver profile instead of `remote_path`:

```json
{
  "allow_device_export": true,
  "restore_point": "restore-point-1",
  "restore_sftp_port": true,
  "backup_settle_seconds": 15
}
```

The explicit flag acknowledges that CeraOS updates restore point 1 and its
configuration-transfer channel before exporting. The driver never runs import,
restore, delete, reset, or software-management commands.

Legacy ALFOplus is selected automatically when `siae_smos_auto` receives an
incomplete/unsupported CLI response and its selected receiver uses protocol
`ftp`. Quick Setup stores these explicit approvals:

```json
{
  "allow_device_export": true,
  "allow_legacy_ftp_setup": true,
  "sync_receiver_credentials": false
}
```

The receiver login must be at most eight characters for both username and
password. During a backup the driver temporarily selects FTP for the WebLCT
session, starts backup action `1`, polls status, and restores the previous
FTP/SFTP mode. Optional credential synchronization is a separate explicit UI
choice because it writes the receiver login into the device file-transfer
settings. It never calls restore action `2` or revert action `3`.

### External driver packages

Trusted packages can register a driver without changing this repository:

```toml
[project.entry-points."netbox_config_backup.drivers"]
arista_eos = "netbox_backup_arista:AristaEOSDriver"
```

The class must inherit `BackupDriver`, use `driver_api_version = 1`, and have a
`driver_id` matching the entry-point name. Duplicate IDs, incompatible API
versions, failed imports, and non-driver classes stop startup with an explicit
configuration error. Install the same package in the NetBox web and worker
images, then restart both processes.

`Test connection` queues a standard NetBox background job associated with the
selected backup target. It exercises secret resolution, SSH transport, driver
collection, validation, and normalization, but does not create a `BackupRun`,
write an artifact, or create a `ConfigRevision`. Only safe status codes and
artifact counts/sizes are recorded in the job log.

For the local Docker stack, copy
`C:\dev\netbox-docker\env\config-backup.env.example` to
`C:\dev\netbox-docker\env\config-backup.env`. Both the web and worker services
load this ignored file. SSH identities are normally managed in PostgreSQL by
the plugin and do not require a manually configured file in a Connection
Profile. The onboarding workflow is available in **Config Backup > Settings > Security and vendor-specific setup >
SSH host keys**. Under manual approval the plugin scans the pre-authentication
SSH handshake, presents the SHA256 fingerprint for administrator approval, and
stores approved public host keys in PostgreSQL. TOFU stores and trusts only the
first identity ever seen for one target, address, and port. A later identity
always remains pending until an administrator verifies and approves it. Profiles
with identity verification disabled neither require nor use this trust store.
Do not populate the environment file from chat history or commit it to Git.

The `encrypted_database` credential provider exposes a Username plus write-only
Password and Confirm password fields in the Credential Profile form. Passwords
are encrypted with AES-256-GCM using `NETBOX_CONFIG_BACKUP_MASTER_KEY`; the
username, nonce, ciphertext, reference, and key version are stored in PostgreSQL.
Editing a profile with blank password fields preserves the current password. The
plaintext password is never rendered, exported, or placed in a backup job log.

Create these profiles in **Config Backup > Settings > Device defaults >
Credential profiles**, select **Add**, and then choose **Encrypted database
(write-only password)**. The
master key must be the same in the NetBox web and worker processes and must stay
stable across restarts. Store and back it up separately from PostgreSQL; losing
it makes the stored passwords unrecoverable. Access to encrypted material is
additionally guarded by the `add/change/delete_storedcredential` permissions.

## Quick Setup

The normal workflow starts from **Overview** or **Devices > Add**. One form
selects the NetBox device and backup driver, stores an encrypted
username/password, and selects a simple schedule plus a required Local history
profile and an optional FTP retention override. Local retention is selected in
this order: device override, backup policy, then Local storage profile. FTP
retention is selected independently for each FTP storage: device override, then
that storage's profile. If no effective profile exists, that history is kept
indefinitely. A storage whose **Always use this storage's retention profile**
checkbox is enabled ignores the device override.
**Save & test connection** creates the complete configuration and immediately
queues the non-persistent connection test.

Quick Setup creates the target, per-device connection and credential profiles,
and reusable `[Quick]` backup policies and Local retention profiles in a single transaction. It
can resolve the driver automatically from an enabled platform mapping. The
individual profile and policy pages remain available from **Settings**.
Because it assigns a Local retention profile, Quick Setup requires the
Administrator retention and runtime permissions; Operators can run, test, and
reschedule existing targets but cannot indirectly authorize future deletion.

The **Settings** page groups reusable defaults by task: device defaults,
scheduling and retention, exceptional vendor/security setup, and global
automation. **Config Backup > Help** provides the recommended setup order,
Local-versus-remote retention precedence, storage/receiver differences, and safe
first checks for common error codes. Help is read-only and is available to the
Reader role without granting access to Settings or secret values.

Administrators can select the default plugin language under **Config Backup >
Settings > Plugin language**. English and Slovak are included. The setting is
applied only to Config Backup URLs and does not change the language of the rest
of NetBox. A user can temporarily override the default from **Config Backup >
Help**; the personal choice is stored in that user's browser session and can be
changed back at any time.

Connection profiles expose three SSH server-identity modes. **Require manual
approval** is the secure default. **Trust first key automatically** implements
TOFU: it accepts only the first identity ever observed for the target endpoint
and blocks every later change. **Do not verify SSH identity** is an explicit
opt-out intended only for a separately trusted management network. Approving a
replacement marks every older trusted key for the same target, address, and
port as rejected; the rows are retained for audit but are no longer accepted.
Switching verification off does not delete existing rows: they are ignored while
the profile is disabled and can be audited later. If verification is enabled
again after the device identity changed, the old trusted identity causes the
connection to fail until the replacement fingerprint is independently verified.

Deleting a backup device is an explicit cascade within this plugin: the
confirmation page lists its runs, revisions, and artifacts, then removes their
stored files and any now-unused per-device `[Quick]` connection and credential
profiles. The underlying NetBox `Device` is never deleted. Active backup runs
and storage failures abort the operation.

## Revision content and diff

Revision detail pages provide a redacted configuration viewer and a unified
diff against another revision of the same target. Before content is displayed,
the plugin verifies the stored artifact size and SHA-256 digest, decodes it as
strict UTF-8, and runs the owning driver redactor. It fails closed when any of
these checks cannot be completed. Viewing requires permissions for both the
revision and its artifact.

Ceragon IP-50 revisions extract `config_dump.txt` from the validated native ZIP
as the primary text artifact. Change detection and diffs remove only the export
timestamp, signature, and the CeraOS file-transfer configuration/log tables
modified by the backup operation. The original ZIP and manifest remain stored
as secondary artifacts. Authorized users can download the integrity-checked
native ZIP for vendor restore workflows; responses are attachments with private,
no-store caching and MIME sniffing disabled.

The content preview is limited to 1 MiB by default and the rendered diff to
20,000 lines. Oversized text is redacted before the browser preview is truncated.
Diff input is limited to 25 MiB by default. These limits can be adjusted with
`content_preview_max_bytes`, `diff_input_max_bytes`, and `diff_max_lines` in the
plugin configuration.

Existing Ceragon revisions created by older plugin versions can be upgraded
without deleting their ZIP files. The command is a dry run by default:

```bash
python manage.py config_backup_backfill_ceragon_content
python manage.py config_backup_backfill_ceragon_content --apply
```

## Retention preview and cleanup

**Config Backup > Storages** contains exactly one system-managed **Local
storage** plus any FTP, NFS, or SMB3 storages created by administrators. The Local row
represents the primary `storage_root` configured for the deployment. It is
created by the migration, is always enabled, and cannot be deleted, disabled,
or converted to a remote type. Remote storages are independent secondary copies and retain
their own connection, audit, replication, and retention settings.

Each storage can provide a retention profile as a fallback. Enabling **Always
use this storage's retention profile** makes that policy mandatory for the
storage and prevents a device-specific override. Effective retention is
resolved in this exact order:

1. **Local storage:** enforced Local-storage policy; device Local override;
   the device's backup-policy retention; Local-storage fallback; otherwise
   keep indefinitely.
2. **Each remote storage independently:** enforced storage policy; device remote
   override; that storage's fallback; otherwise keep indefinitely.

The local policy controls primary artifacts, revision history, and completed
backup runs. Each remote policy controls only copies on that one remote storage.
Installing the migration creates the protected Local row and leaves existing
FTP storages, device overrides, and their behavior intact. New storage policy
fields start empty and enforcement starts disabled, so an upgrade cannot by
itself delete existing history.

Assigning or changing any effective retention profile requires retention
runtime administration plus the matching delete permissions. Managed Operators
can adjust backup scheduling, but cannot indirectly enable more aggressive
history deletion.

The remote profile's `max_copies_per_target` ceiling is evaluated **per device, per
remote storage**. Physical artifact files inside one revision do not consume
separate positions. If the same revision is copied to two FTP storages, it
consumes one position on each storage's independent retention plan.

Each backup device provides a permission-gated **Retention preview** action.
The dry-run reports the Local decision and every FTP storage decision
separately, including the effective policy source, what would be kept or
deleted, and why. It also estimates the local artifact and remote-copy space
affected by a future cleanup. A preview never mutates the database or any
storage location.

Protected revisions and the latest usable revision are retained in every
applicable storage scope. The configured minimum number of changed revisions is
also retained.
Active runs and runs with an unknown future status are always kept locally;
pending, queued, running, or failed remote transfers are not candidates for remote
deletion. Disabled remote storages are excluded from automatic and manual remote
retention plans.

Each local retention profile defines a hard per-target ceiling for completed
backup runs (500 by default). Time-based retention remains the primary rule;
when it would keep more completed runs than the ceiling, the oldest excess
runs are expired. Active runs and unknown future statuses do not consume the
ceiling and are kept safely.

Users with the relevant change permission can protect or unprotect a revision
from its detail page. Applying local or remote retention is explicitly confirmed
and permission-gated. Local cleanup stages files in an internal quarantine and
restores them if its database transaction fails. Remote deletion is irreversible:
it removes only the recorded immutable revision directory below the configured
storage path and records failures for a safe retry.

Local cleanup and remote cleanup have separate opt-in schedulers under
**Config Backup → Settings**. Both are disabled by default and require an
explicit acknowledgement before the first enable. Each dispatcher skips
conflicting active work and already queued/running cleanup jobs. Enabling local
cleanup never enables remote deletion, and a failed remote cleanup does not turn a
successful device backup into a failure.

Before enabling remote cleanup for the first time on an existing installation, run
the read-only integrity audit for every remote storage and resolve all missing
or mismatched historical replicas recorded as successful. Cleanup must not be
used to discover or repair an unverified legacy inventory.

After a storage contains a recorded copy, its host, port, base path, and
credential-profile assignment or mounted directory are immutable. Rotate an FTP
password inside its credential profile, or create a new storage when moving to another
endpoint. This keeps historical retention deletes bound to the server or mount and path
where the copy was originally written.

An expired remote copy is not recreated automatically by an unchanged backup,
integrity repair, or ordinary historical backfill. Increasing a profile later
affects retained and future copies; it does not restore copies which were
already deleted. Restore such history manually from another trusted copy when
required. The deletion marker prevents accidental recreation while the revision
remains in plugin history; after both the local revision and all of its FTP
copies have fully expired, cleanup may also remove the revision and its
replica/deletion audit metadata. These markers are not a permanent audit log.

## Run the core tests

The core test suite does not require a running NetBox instance:

```text
python -m unittest discover -s tests -v
```

Model and migration checks should additionally be run inside the pinned NetBox
4.6 test environment before deployment.

## Docker integration smoke test

The repository contains a development overlay for a sibling checkout at
`C:\dev\netbox-docker`. It uses the existing development image as its base but
runs under the separate Compose project `netbox-config-backup-test`, with its
own PostgreSQL, Redis, media, and backup-storage volumes. The existing
`netbox-docker` database is not used.

Build and start the isolated stack from this repository:

```powershell
docker compose -p netbox-config-backup-test `
  -f C:\dev\netbox-docker\docker-compose.yml `
  -f C:\dev\netbox-docker\docker-compose.override.yml `
  -f .\docker\docker-compose.integration.yml `
  up -d --build postgres redis redis-cache netbox netbox-worker
```

Run the database/storage pipeline smoke test:

```powershell
docker exec netbox-config-backup-test-netbox-1 sh -c `
  '/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < /opt/netbox-plugin-backup/tests/integration/docker_smoke.py'
```

The expected sequence is `success_changed`, `success_unchanged`, then
`success_changed`, with two revisions and two stored artifacts. The test UI is
available at <http://localhost:8001/plugins/config-backup/>.

Run the authenticated UI and RQ dispatcher smoke test:

```powershell
docker exec netbox-config-backup-test-netbox-1 sh -c `
  '/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < /opt/netbox-plugin-backup/tests/integration/docker_ui_dispatcher_smoke.py'
```

Stop the isolated stack while preserving its test volumes:

```powershell
docker compose -p netbox-config-backup-test `
  -f C:\dev\netbox-docker\docker-compose.yml `
  -f C:\dev\netbox-docker\docker-compose.override.yml `
  -f .\docker\docker-compose.integration.yml `
  down
```

## License

NetBox Config Backup is licensed under the
[Apache License 2.0](LICENSE).
