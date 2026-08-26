# Compatibility matrix

| Plugin release | Minimum NetBox | Maximum NetBox | Python |
| --- | --- | --- | --- |
| 0.5.x | 4.6.0 | 4.6.x | 3.12–3.14 |

The limits above are enforced by the plugin's `PluginConfig`. Do not bypass the
maximum version check: a new NetBox minor or major release can change plugin
views, jobs, permissions, and model APIs.

Release `0.5.x` is tested against the `netboxcommunity/netbox:v4.6-5.0.2`
container image. Test a newer NetBox 4.6 container with the full migration,
system-check, unit-test, and smoke-test procedure before using it in
production.
