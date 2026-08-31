# Changelog

All notable changes to NetBox Config Backup are documented here. The project
uses semantic versioning; database migrations are forward-only and must never
be deleted from an installation which may already have applied them.

## [0.7.1] - 2026-08-31

### Fixed

- The netbox-docker receiver overlay now applies the same plugin image and
  persistent backup volume to the web, worker, housekeeping, backup-worker,
  and optional receiver processes.
- Clean-install documentation now states explicitly that database migrations
  and static-file collection are required for both new installations and
  upgrades.
- Overview no longer remains highlighted on every plugin page, and backup
  device bulk deletion now completes synchronously instead of depending on a
  compatible background worker.
- Manual backups now fail fast when the dedicated worker is unavailable;
  queued runs can be cancelled from the UI, and stale reconciliation verifies
  the Redis job and a live queue worker instead of trusting a pending database
  row indefinitely.
- Settings and Help now load their stylesheet from a plugin-owned endpoint, so
  an unrelated `collectstatic` failure no longer leaves these pages unstyled.

## [0.7.0] - 2026-08-27

### Added

- UI-managed Local, FTP, NFS, and SMB3 storage profiles.
- Independent Local and remote retention profiles, previews, schedulers, and
  hard per-device history ceilings.
- FTP replication reconciliation, integrity audit, recovery downloads, and
  hostname-based immutable revision paths.
- English and Slovak plugin UI with English as the deployment default.
- Three explicit SSH identity policies: manual approval, trust on first use,
  and disabled verification.
- Unified SIAE workflow plus native backup support for Ceragon and RACOM
  device families.

### Changed

- Settings, Help, Quick Setup, and device forms use consistent terminology and
  hide deployment-only implementation details.
- The OOB address label now explains that it is a dedicated management IP.
- Remote storage retention is resolved independently for every destination.
- Release checks now cover supported Python versions, package contents,
  clean and upgrade migrations, default storage creation, and managed RBAC
  groups.

### Security

- Changed SSH identities are never trusted automatically. Replaced identities
  remain available as rejected audit records.
- Revision content and downloads remain protected by NetBox object
  permissions; credentials continue to be encrypted with AES-256-GCM.

### Upgrade notes

- Run all migrations through `0028` and run
  `config_backup_create_rbac_groups` again after upgrading.
- Review every connection profile's SSH identity policy before enabling
  automatic backups.
- Run a read-only integrity audit before enabling remote retention cleanup.

## [0.6.0] - 2026-08-26

- Initial packaged release of the backup pipeline, drivers, scheduling,
  retention, FTP replication, RBAC, notifications, and recovery tooling.
