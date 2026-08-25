# Installation

This is the shortest supported path from a clean NetBox 4.6 installation to a
working Config Backup deployment. The standard deployment uses local primary
storage and can optionally replicate revisions to an internal FTP server.

## 1. Check compatibility

- NetBox: 4.6.x
- Python: 3.12, 3.13, or 3.14
- PostgreSQL and Redis: the services already used by NetBox
- Plugin package: the same exact wheel or image in the web process and every
  worker process

See [../COMPATIBILITY.md](../COMPATIBILITY.md).

## 2. Install the package

Build a wheel from a tagged source tree:

```shell
python -m pip install build
python -m build --wheel
```

For a traditional NetBox installation, add the exact wheel path or published
version to `/opt/netbox/local_requirements.txt` and run NetBox's upgrade
procedure. A direct installation is also possible:

```shell
sudo /opt/netbox/venv/bin/python -m pip install \
  /path/to/netbox_config_backup-0.4.0-py3-none-any.whl
```

For netbox-docker, build the non-editable release image:

```shell
docker build -f docker/Dockerfile.release \
  --build-arg NETBOX_IMAGE=netboxcommunity/netbox:v4.6-5.0.2 \
  -t netbox-config-backup:0.4.0 .
```

Use this same image for `netbox`, the normal NetBox worker, and the dedicated
Config Backup worker.

## 3. Create the master key

Generate a unique 256-bit key once:

```shell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Inject these variables through the deployment's secret mechanism into the web
process and all workers:

```text
NETBOX_CONFIG_BACKUP_MASTER_KEY=<generated value>
NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION=1
NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS={}
```

Back up the master key separately from PostgreSQL and the artifact volume. If
the key is lost, encrypted device credentials cannot be recovered.

## 4. Configure NetBox

Add the plugin to `configuration.py`:

```python
PLUGINS = [
    # Existing plugins...
    "netbox_config_backup",
]

PLUGINS_CONFIG = {
    "netbox_config_backup": {
        "storage_backend": "local",
        "storage_root": "/var/lib/netbox-config-backup",
        "receiver_root": "/var/lib/netbox-config-backup/receiver",
        "events_enabled": True,
        "notify_on_every_failure": False,
        "metrics_enabled": False,
    }
}
```

Create the persistent storage directory and restrict it to the NetBox service
account:

```shell
sudo install -d -o netbox -g netbox -m 0700 /var/lib/netbox-config-backup
```

In Docker, mount one persistent volume at this path into the web process and
the dedicated backup worker. Vendor-native push receivers must share the same
volume when enabled.

## 5. Upgrade the database and start workers

```shell
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py migrate
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py check
```

Restart the NetBox web process and its normal worker. Start one dedicated
worker for device backup jobs:

```shell
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py \
  rqworker netbox_config_backup.backup
```

The normal NetBox worker must remain running because dispatch, retention, FTP
replication, audit, and recovery jobs use NetBox's background job system.

## 6. Initial UI setup

1. Open **Config Backup → Settings**.
2. Create the Reader, Operator, and Administrator groups with
   `python manage.py config_backup_create_rbac_groups`, then assign
   users deliberately.
3. Create a credential profile and verify that the master-key status is
   current.
4. Create connection and platform mappings, or use **Add device** for one test
   device.
5. Use **Save & test connection** before enabling automatic scheduling.
6. Run one manual backup and verify the revision content and download
   permissions with a non-superuser account.
7. If required, add an internal FTP destination, test it, and only then enable
   automatic replication and integrity audits.
8. Enable automatic retention only after reviewing the retention preview. The
   default per-device completed-run ceiling is 500.

## 7. Verification after every install or upgrade

```shell
python manage.py showmigrations netbox_config_backup
python manage.py check
python -m pip check
```

Confirm that:

- every plugin migration is marked `[X]`;
- the web process and both worker types use the same plugin version;
- the artifact directory is writable only by the service account;
- a connection test and a real backup both finish successfully;
- only authorized NetBox users can view or download revision content;
- an FTP test creates and removes its probe object;
- a real revision is copied to FTP and passes the integrity audit.

For vendor-native receivers, upgrade procedures, notifications, and detailed
failure codes, continue with [DEPLOYMENT.md](DEPLOYMENT.md).
