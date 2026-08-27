# Internal FTP storage

An FTP storage creates an additional immutable copy of configuration
artifacts after the primary backup has completed. It is different from a
**native backup receiver**: a receiver accepts an export pushed by a device,
while the FTP storage receives an already completed Config Backup revision
from the worker.

An unavailable FTP server never changes a successful device backup to failed.
Each copy has its own `Pending`, `Queued`, `Running`, `Successful`, or `Failed`
state and retry schedule.

FTP is unencrypted. Use this feature only on an isolated, trusted internal
management network.

## Storages in the UI

**Config Backup → Storages** always contains one system-managed **Local
storage**. It represents the deployment's configured `storage_root`, is always
enabled, and cannot be deleted, disabled, or converted to FTP. Administrators
can add one or more FTP storages as independent secondary locations.

The Local storage and each FTP storage may have their own typed retention
policy. A selected policy is normally a fallback for devices which do not have
an override. **Always use this storage's retention profile** makes it mandatory
for that storage and ignores the corresponding device override.

## Configure an FTP storage

1. Create a dedicated password account on the FTP server. Restrict it to one
   directory and do not grant administrator access.
2. In **Config Backup → Settings → Credential profiles**, create an encrypted database
   password credential or a supported environment reference.
3. Open **Config Backup → Storages → Add**. The Add form creates an FTP
   storage; the protected Local storage is created only by the plugin.
4. Enter the server, port, remote base directory, credential profile, timeouts,
   retry limits, and artifact size limit. Port 21 is the normal FTP default;
   use a different port when the server is configured for it.
5. Confirm that the storage is on a trusted internal network. FTP sends
   credentials and configuration data without transport encryption.
6. Optionally choose this storage's FTP retention fallback and decide whether
   it must be enforced for every device.
7. Leave **Copy new revisions automatically** enabled for normal operation.
8. Save and select **Test FTP storage**.

The test creates a unique probe file, reads it back, verifies its content, and
deletes it. A successful test therefore confirms TCP connectivity,
authentication, directory creation, upload, download, verification, and
deletion.

Use **Copy existing revisions** once when historical revisions must also be
sent to a new or emptied storage. This action queues only revisions which do
not yet have a replica record for the storage.

After the first copy is recorded, the storage host, port, base path, and
credential-profile assignment are locked. This prevents retention from
following an edited record to a different server or directory. Rotate the
password inside the assigned credential profile; create a new storage when
moving to another FTP endpoint.

## Remote layout and integrity

The worker writes revisions below:

```text
/<base path>/devices/<device hostname>/backups/<UTC creation time>-r<NetBox revision ID>/
```

The hostname comes from the NetBox device `name` field and is sanitized for use
as a safe directory name. If it is empty or contains no usable characters, the
plugin falls back to `device-<NetBox ID>`. Existing replicas created by older
versions remain in their original directories.

The primary backup file uses the same readable device-and-time stem, for
example `core-router-01_2026-08-26_12-35-08.txt`. Supporting artifacts append
their artifact type before the original extension. Timestamps use UTC and are
shown to whole-second precision. The globally unique NetBox revision ID is
appended to the same directory segment, not the artifact filename. It prevents
same-second collisions without a long nested UUID path. The revision UUID
remains in the integrity manifest.

The recorded remote path is immutable. Renaming a NetBox device does not move
or rename its existing FTP copies: audit, repair, recovery, and retention keep
using the path stored on the replica record. Both historical layouts remain
supported: the older readable timestamp-only directory
`.../backups/<UTC creation time>/`, the nested UUID layout
`.../backups/<UTC creation time>/<revision UUID>/`, and the original
`.../revisions/<revision UUID>/` layout.

A `_netbox_manifest.json` file records the device name and ID, remote device
directory, revision UUID, driver, sizes, and SHA-256 hashes. Uploads use a
deterministic short `.part` name in the revision's unique directory, are
downloaded again for hash verification, and are
renamed only after verification. A different file at the final path is
reported as `DESTINATION_CONFLICT` and is never overwritten.

When a successful backup finds no configuration change, the latest revision is
reused. The plugin verifies its successful FTP copy read-only. If the copy is
missing, unreadable, or no longer matches the recorded size/hash, the normal
replication workflow is queued again without reconnecting to the device or
creating a duplicate revision.

## Retry and repair

- Failed copies retain a safe error code and message on the FTP storage
  detail page.
- Automatic retries follow the configured retry count and delay.
- An operator can select **Retry** for a failed revision copy.
- **Copy existing revisions** backfills revisions which have no replica record.
- A later unchanged device backup detects and repairs a missing or damaged copy
  of the latest revision.

## FTP retention

FTP retention is independent from Local retention and is planned separately for
every FTP storage. The effective profile for one device on one FTP storage is
selected in this exact order:

1. the FTP storage policy when **Always use this storage's retention profile**
   is enabled;
2. the device's FTP retention override;
3. the FTP storage's retention fallback;
4. no policy, which means keep copies indefinitely.

The Local or backup-policy retention is never reused implicitly for FTP. An FTP
profile's `max_copies_per_target` is the maximum number of remote **revisions
for one device on one FTP storage**. Artifact files inside a revision do not
count separately. If a revision is copied to two FTP storages, it consumes one
position in each storage's independent plan.

Use **Retention preview** on the backup device before applying either scope.
The preview lists Local artifacts/runs and every FTP storage plan separately,
including whether the effective policy came from storage enforcement, a device
override, or the storage fallback. A protected revision and the latest usable
revision remain protected in every location. Pending, queued, running, and
retryable failed transfers are not remote cleanup candidates. An exhausted
failed transfer which still has a recorded remote path is included: a previous
copy or partial repair may still exist at that exact path and must not become
an untracked orphan. Copies on a disabled FTP storage are left untouched until
that storage is enabled again; disabling it is therefore a deletion kill
switch.

FTP cleanup deletes only the recorded immutable revision directory below the
storage's configured base path. It does not connect to the network device
and it does not alter the local artifact. The operation is irreversible because
FTP has no quarantine or recycle bin. A partial or unavailable-server failure
is retained as an auditable, retryable cleanup result; it does not change the
status of the original device backup.

Automatic FTP cleanup has its own opt-in switch under **Config Backup →
Settings** and is disabled by default. Enabling automatic local cleanup does
not enable FTP cleanup. After an upgrade, existing FTP storages have no storage
policy and enforcement is disabled; existing device overrides keep their
behavior. A device/storage pair with no effective FTP policy keeps all remote
copies indefinitely.

Before the first FTP cleanup on an existing installation, run the read-only
integrity audit on every FTP storage and resolve every missing or mismatched
historical replica recorded as successful. Do this before assigning/enabling a
cleanup schedule; retention is not a substitute for inventory verification.

Once an FTP copy has expired successfully, ordinary backfill, integrity repair,
and an unchanged later backup do not recreate it automatically. Increasing the
retention period changes the treatment of retained and future copies only. To
recover deleted history, copy it back manually from another trusted backup. Its
deletion state is retained while the revision remains in plugin history. After
the local revision and every FTP copy have fully expired, cleanup may remove the
revision together with its replica/deletion audit metadata; it is not a
permanent audit log.

## Automatic FTP integrity audit

Open an FTP storage and enable **Run integrity audits automatically**.
Choose a daily or weekly schedule and a start time. The time is interpreted in
the NetBox server timezone displayed by the UI.

The scheduler queues at most one active audit per storage. The backup
worker downloads every artifact belonging to a successful replica and compares
its size and SHA-256 hash with the NetBox record. The audit is read-only: it
does not upload, rename, or delete remote files.

The last result and the next scheduled run are shown on the storage detail
and list pages. Automatic auditing is disabled by default. A manual audit is
available through **Check stored copies**.

## Verified recovery ZIP

On a revision detail page, **Verified FTP copies** lists successful FTP
replicas visible to the current user. Select **Prepare verified ZIP** to queue a
read-only download.

The worker streams every artifact and `_netbox_manifest.json` from that exact
FTP revision, verifies each recorded size and SHA-256, and publishes a
temporary ZIP only if every check succeeds. The ZIP also contains
`RECOVERY_README.txt` and expires after 60 minutes by default. Each issued
download is recorded in the NetBox Job data with user and time.

The action requires view permission for the revision, all its artifacts, the
replica, and the FTP storage. It never connects to a device and has no
restore/import/apply operation. Native recovery remains vendor-specific and is
performed manually by an authorized operator.

## Security checklist

- Use FTP only on an isolated, trusted internal management network.
- Restrict the firewall source to the NetBox and backup worker addresses.
- Use a dedicated least-privilege password account and directory.
- Prevent interactive or administrative access for the FTP account.
- Encrypt the server/NAS volume and protect its backups.
- Monitor failed replica and integrity-audit states.
- Test a verified recovery ZIP and the manual vendor restore procedure
  periodically.

## End-to-end verification

The repository includes an integration smoke test which creates an isolated
NetBox device backed by `FakeDriver`, runs the real backup pipeline, waits for
the asynchronous FTP worker, and downloads the manifest and every artifact
again. It succeeds only when all recorded sizes and SHA-256 hashes match. It
removes its database objects, local artifacts, and unique remote test directory
afterwards.

The test requires the backup worker and exactly one enabled automatic FTP
storage. Run it from the `netbox-docker` directory in PowerShell:

```powershell
Get-Content C:\dev\netbox-plugin-backup\tests\integration\docker_ftp_e2e_smoke.py -Raw |
  docker compose exec -T netbox /opt/netbox/netbox/manage.py shell
```

A successful run prints a JSON object containing
`"marker": "FTP_E2E_SMOKE_OK"`. No credential or configuration content is
printed.

To verify the separate recovery download path against an existing successful
FTP replica, run:

```powershell
Get-Content C:\dev\netbox-plugin-backup\tests\integration\docker_ftp_recovery_smoke.py -Raw |
  docker compose exec -T netbox /opt/netbox/netbox/manage.py shell
```

Success prints `"marker": "FTP_RECOVERY_SMOKE_OK"`. The test verifies the UI
status and download endpoints, ZIP and artifact hashes, the download audit
entry, and that no `BackupRun` or device connection was created.
