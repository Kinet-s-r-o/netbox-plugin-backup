# Security policy

## Supported release line

Security fixes are applied to the current `0.4.x` release line. The supported
NetBox and Python versions are listed in [COMPATIBILITY.md](COMPATIBILITY.md).

## Reporting a vulnerability

Do not place credentials, configuration backups, master keys, private keys,
FTP passwords, host-key material, or production logs in a public issue. Report
the problem privately to the maintainer responsible for the deployment and
include only sanitized reproduction steps until a private project security
contact is published.

## Deployment baseline

- Keep **Verify host key** enabled and approve fingerprints out of band.
- Treat any legacy SSH algorithm exception as device-specific. The default
  transport disables RSA/SHA-1 signatures; compatibility exceptions retain
  strict `known_hosts` verification.
- Use a least-privilege, backup-only account on each device. Drivers collect
  configuration and never restore it automatically.
- Store `NETBOX_CONFIG_BACKUP_MASTER_KEY` outside PostgreSQL and outside the
  artifact volume. Inject the same version into the web and worker processes.
- Restrict the local artifact volume to the NetBox service account and use the
  NetBox Reader, Operator, and Administrator permission groups.
- FTP is plaintext. Use it only on an isolated trusted management network and
  never expose it to the Internet.
- Keep NetBox, the base container, and plugin dependencies updated. Run
  `python -m pip check`, a dependency vulnerability audit, migrations, system
  checks, and the plugin regression tests before each release.

## Current dependency notes

As of 2026-08-25, Netmiko 4.7 requires Paramiko `<5`, while the public PyPI
release containing the fix for CVE-2026-44405 is Paramiko 5.0.0. The plugin
mitigates this low-severity RSA/SHA-1 issue by disabling `ssh-rsa` signatures
by default.
Explicit legacy device exceptions remain restricted to the server host key and
still require a previously approved fingerprint.

PyJWT is supplied by NetBox rather than this plugin. Follow the supported
NetBox image for its security update instead of overriding PyJWT independently;
an incompatible override can break NetBox's authentication dependencies.
