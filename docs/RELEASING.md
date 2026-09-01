# Release procedure

Use this checklist for every published NetBox Config Backup version. Release
artifacts must be produced from a clean, tagged commit; never publish a wheel
built from a working tree with uncommitted changes.

## 1. Prepare

1. Update `CHANGELOG.md`, `pyproject.toml`,
   `netbox_config_backup/__about__.py`, and `COMPATIBILITY.md`.
2. Confirm that user and deployment documentation describes the current UI.
3. Never edit or delete an existing migration. Add a new migration instead.
4. Run the RBAC command after adding a model or changing plugin permissions.

## 2. Local release gate

```shell
python -m ruff check netbox_config_backup tests docker/plugins-ci.py
python -m coverage run -m unittest discover -s tests
python -m coverage report --fail-under=45
python -m build
python -m twine check dist/*
```

Use the Docker integration environment to run:

```shell
python manage.py migrate
python manage.py makemigrations netbox_config_backup --check --dry-run
python manage.py check
python manage.py config_backup_create_rbac_groups
python manage.py showmigrations netbox_config_backup
```

Also verify one real changed backup, one unchanged backup, remote replication,
an integrity audit, a retention preview, and revision access with a non-superuser
Reader account. From the native NetBox Device page, verify that the Backup tab
opens the redacted preview and downloads the integrity-checked original artifact.

## 3. Automated gate

The GitHub `CI` workflow must pass before tagging. It checks Python 3.12–3.14,
builds and inspects the wheel, and builds the NetBox release image by installing
that wheel rather than importing a source checkout.

The NetBox 4.6 release-image gate exercises both supported database paths:

- **Clean install:** applies all migrations, collects packaged static assets,
  checks migration drift and NetBox configuration, creates the three RBAC
  groups, and executes the complete FakeDriver pipeline. The pipeline verifies
  changed, unchanged, and changed-again runs together with stored revisions and
  artifacts.
- **Upgrade:** migrates to the previous release schema, inserts representative
  settings, policies, profiles, device, target, run, revision, and artifact
  data, then migrates to the current schema and verifies that every value and
  relationship was preserved.

Do not replace these checks with a migration-only smoke test: successful SQL
migrations do not by themselves prove that packaged templates, static files,
defaults, relations, or the backup pipeline work after installation.

## 4. Publish

1. Commit all release changes.
2. Create an annotated `vX.Y.Z` tag on the reviewed commit.
3. Build the wheel and source distribution from that tag.
4. Publish the immutable artifacts and their SHA256 checksums.
5. Deploy the same exact package to NetBox web, normal worker, backup worker,
   and optional receiver processes.

## 5. Post-upgrade verification

Follow `docs/INSTALLATION.md` section **Verification after every install or
upgrade**. Do not enable automatic remote deletion until every configured
remote storage has passed a read-only integrity audit.
