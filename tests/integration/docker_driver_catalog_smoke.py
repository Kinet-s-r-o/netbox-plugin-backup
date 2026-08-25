"""Read-only deployment check for built-in and external driver discovery UI."""

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.forms import PlatformMappingForm, QuickSetupForm

expected_ids = {
    "cisco_ios",
    "cisco_xe",
    "ceragon_ip20",
    "ceragon_ip50",
    "dell_os6",
    "dell_os9",
    "dell_os10",
    "dell_powerconnect",
    "fake",
    "fiberstore_fsos",
    "fiberstore_fsosv2",
    "hp_comware",
    "hp_procurve",
    "huawei_vrp",
    "huawei_vrpv8",
    "mikrotik_routeros",
    "racom_ray2",
    "racom_ray3",
    "racom_ripex2",
    "siae_ags20",
    "siae_alfoplus",
    "siae_alfoplus2",
    "siae_alfoplus80hd",
    "siae_smos_auto",
    "siae_smos_cli",
    "siae_smos_ssh",
    "tplink_jetstream",
    "ubiquiti_edgerouter",
    "ubiquiti_edgeswitch",
    "zte_zxros",
}
assert expected_ids <= set(driver_registry.ids())

quick_values = {value for value, _label in QuickSetupForm().fields["driver_id"].choices}
mapping_values = {value for value, _label in PlatformMappingForm().fields["driver_id"].choices}
hidden_siae_ids = {
    "siae_ags20",
    "siae_alfoplus",
    "siae_alfoplus2",
    "siae_alfoplus80hd",
    "siae_smos_cli",
    "siae_smos_ssh",
}
selectable_ids = expected_ids - hidden_siae_ids
assert selectable_ids <= quick_values
assert selectable_ids <= mapping_values
assert hidden_siae_ids.isdisjoint(quick_values)
assert hidden_siae_ids.isdisjoint(mapping_values)

user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert user is not None
client = Client()
client.force_login(user)

quick_setup = client.get(reverse("plugins:netbox_config_backup:backuptarget_add"))
mapping_add = client.get(reverse("plugins:netbox_config_backup:platformmapping_add"))
examples = client.get(reverse("plugins:netbox_config_backup:examples"))
for response in (quick_setup, mapping_add, examples):
    assert response.status_code == 200

for label in (
    b"Dell SmartFabric OS10 (Netmiko)",
    b"FS / Fiberstore FSOS (Netmiko)",
    b"HP/HPE Comware (Netmiko)",
    b"Huawei VRP (Netmiko)",
    b"TP-Link JetStream (Netmiko)",
    b"Ubiquiti EdgeRouter (Netmiko)",
    b"ZTE ZXROS (Netmiko)",
    b"RACOM RipEX2 (HTTPS API)",
    b"RACOM RAy2 (SSH/SCP native backup)",
    b"Ceragon IP-50 / CeraOS (SFTP native backup)",
    b"SIAE SM-OS (automatic backup)",
):
    assert label in quick_setup.content
    assert label in mapping_add.content

for hidden_label in (
    b"SIAE ALFOplus2 (SFTP native backup)",
    b"SIAE SM-OS CLI snapshot (read-only SSH)",
    b"SIAE SM-OS CLI snapshot (read-only Telnet)",
):
    assert hidden_label not in quick_setup.content
    assert hidden_label not in mapping_add.content

assert b"Built-in network driver catalog" in examples.content
assert b"show running-configuration" in examples.content
assert b"display current-configuration" in examples.content
assert b"Native radio backup examples" in examples.content

print(
    {
        "registered_driver_count": len(driver_registry.ids()),
        "required_driver_count": len(expected_ids),
        "quick_setup": quick_setup.status_code,
        "platform_mapping": mapping_add.status_code,
        "examples": examples.status_code,
    }
)
