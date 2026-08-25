# Internal FTP destination

An FTP destination creates an additional immutable copy of configuration
artifacts after the primary backup has completed. It is different from a
**native backup receiver**: a receiver accepts an export pushed by a device,
while the FTP destination receives an already completed Config Backup revision
from the worker.

An unavailable FTP server never changes a successful device backup to failed.
Each copy has its own `Pending`, `Queued`, `Running`, `Successful`, or `Failed`
state and retry schedule.

FTP is unencrypted. Use this feature only on an isolated, trusted internal
management network.

## Configure the destination

1. Create a dedicated password account on the FTP server. Restrict it to one
   directory and do not grant administrator access.
2. In **Config Backup → Settings → Credentials**, create an encrypted database
   password credential or a supported environment reference.
3. Open **Settings → FTP destination → Add**.
4. Enter the server, port, remote base directory, credential profile, timeouts,
   retry limits, and artifact size limit. Port 21 is the normal FTP default;
   use a different port when the server is configured for it.
5. Confirm that the destination is on a trusted internal network. FTP sends
   credentials and configuration data without transport encryption.
6. Leave **Copy new revisions automatically** enabled for normal operation.
7. Save and select **Test FTP destination**.

The test creates a unique probe file, reads it back, verifies its content, and
deletes it. A successful test therefore confirms TCP connectivity,
authentication, directory creation, upload, download, verification, and
deletion.

Use **Copy existing revisions** once when historical revisions must also be
sent to a new or emptied destination. This action queues only revisions which
do not yet have a replica record for the destination.

## Remote layout and integrity

The worker writes revisions below:

```text
/<base path>/devices/<device hostname>/revisions/<revision UUID>/
```

The hostname comes from the NetBox device `name` field and is sanitized for use
as a safe directory name. If it is empty or contains no usable characters, the
plugin falls back to `device-<NetBox ID>`. Existing replicas created by older
versions remain in their original directories.

Every artifact is copied under its original filename. A
`_netbox_manifest.json` file records the device name and ID, remote device
directory, revision UUID, driver, sizes, and SHA-256 hashes. Uploads use a
random `.part-*` name, are downloaded again for hash verification, and are
renamed only after verification. A different file at the final path is
reported as `DESTINATION_CONFLICT` and is never overwritten.

When a successful backup finds no configuration change, the latest revision is
reused. The plugin verifies its successful FTP copy read-only. If the copy is
missing, unreadable, or no longer matches the recorded size/hash, the normal
replication workflow is queued again without reconnecting to the device or
creating a duplicate revision.

## Retry and repair

- Failed copies retain a safe error code and message on the FTP destination
  detail page.
- Automatic retries follow the configured retry count and delay.
- An operator can select **Retry** for a failed revision copy.
- **Copy existing revisions** backfills revisions which have no replica record.
- A later unchanged device backup detects and repairs a missing or damaged copy
  of the latest revision.

The plugin does not delete FTP revision directories when local retention
removes a revision. Configure long-term retention and deletion protection on
the FTP server or NAS.

## Automatic FTP integrity audit

Open an FTP destination and enable **Run integrity audits automatically**.
Choose a daily or weekly schedule and a start time. The time is interpreted in
the NetBox server timezone displayed by the UI.

The scheduler queues at most one active audit per destination. The backup
worker downloads every artifact belonging to a successful replica and compares
its size and SHA-256 hash with the NetBox record. The audit is read-only: it
does not upload, rename, or delete remote files.

The last result and the next scheduled run are shown on the destination detail
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
replica, and the FTP destination. It never connects to a device and has no
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
destination. Run it from the `netbox-docker` directory in PowerShell:

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
