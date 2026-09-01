# RBAC, notifications, and Prometheus

## Least-privilege groups

Create or reconcile the three built-in groups with:

```console
python manage.py config_backup_create_rbac_groups
```

- **Config Backup Readers** can view targets, runs, revisions, artifacts,
  redacted content, diffs, and the Backup tab attached to permitted NetBox
  devices. The View action requires revision and artifact visibility; Download
  returns the original unredacted artifact under the same permissions.
- **Config Backup Operators** additionally manage operational configuration,
  test connections, and start backup runs.
- **Config Backup Administrators** have full plugin model permissions.

The command never assigns users. Add real people in **Admin → Authentication →
Groups**. Do not use a superuser account to validate Reader or Operator access,
because superusers bypass object permissions. The Device Backup tab, direct
revision content, download, diff, protection, target-run, and nested list
endpoints all apply NetBox object restrictions.

## In-app notifications

The following command is read-only unless `--apply` is supplied:

```console
python manage.py config_backup_configure_notifications --user admin
python manage.py config_backup_configure_notifications --user admin --apply
```

It creates **Config Backup Notifications** and eight enabled Event Rules:

- Config Backup - Failed
- Config Backup - Recovered
- Config Backup - Stale target
- Config Backup - Stuck run
- Config Backup - External copy failed
- Config Backup - External copy recovered
- Config Backup - FTP integrity audit failed
- Config Backup - FTP integrity audit recovered

Repeat `--user` or `--group` to add recipients. Inactive or unknown users are
rejected. Existing notification-group members are not silently removed. The
rules use NetBox in-app notifications; no SMTP, webhook, or chat secret is
stored by this command.

NetBox stores at most one current notification per user and source object. A
new event for the same run updates that object's notification, which prevents
unbounded duplicates while the full audit trail remains in Backup Runs.

## Prometheus

The supplied configuration scrapes `http://netbox:8080/metrics` every 30
seconds. The Docker overlay starts Prometheus 3.13.1 with 15-day and 1 GiB TSDB
retention, a persistent volume, a read-only root filesystem, and the web UI
bound only to `127.0.0.1:9090`.

```console
docker compose -f docker-compose.yml \
  -f docker/docker-compose.monitoring.yml up -d prometheus
```

Open `http://127.0.0.1:9090/targets` and confirm the `netbox` target is **UP**.
The included rules alert on scrape failure, failed targets, stale targets,
stuck runs, and no successful backup for 26 hours. Change the last threshold
if every target uses a schedule longer than one day.

Prometheus evaluates and displays these alerts, but external delivery requires
an Alertmanager or another system which consumes Prometheus alerts. The NetBox
Event Rules remain the immediate in-app notification path and work even when
Prometheus is unavailable.
