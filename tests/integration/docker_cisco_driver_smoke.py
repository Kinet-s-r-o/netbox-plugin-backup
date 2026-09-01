"""Read-only deployment check for Cisco IOS and IOS-XE driver UI registration."""

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.cisco_ios import CiscoIOSDriver, CiscoIOSXEDriver
from netbox_config_backup.forms import PlatformMappingForm, QuickSetupForm

assert isinstance(driver_registry.create("cisco_ios"), CiscoIOSDriver)
assert isinstance(driver_registry.create("cisco_xe"), CiscoIOSXEDriver)

expected_values = {"cisco_ios", "cisco_xe"}
quick_values = {value for value, _label in QuickSetupForm().fields["driver_id"].choices}
mapping_values = {value for value, _label in PlatformMappingForm().fields["driver_id"].choices}
assert expected_values <= quick_values
assert expected_values <= mapping_values

user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert user is not None
client = Client()
client.force_login(user)

quick_setup = client.get(reverse("plugins:netbox_config_backup:backuptarget_quick_setup"))
assert quick_setup.status_code == 200
assert b"Cisco IOS (Netmiko)" in quick_setup.content
assert b"Cisco IOS-XE (Netmiko)" in quick_setup.content

mapping_add = client.get(reverse("plugins:netbox_config_backup:platformmapping_add"))
assert mapping_add.status_code == 200
assert b"Cisco IOS (Netmiko)" in mapping_add.content
assert b"Cisco IOS-XE (Netmiko)" in mapping_add.content

examples = client.get(reverse("plugins:netbox_config_backup:examples"))
assert examples.status_code == 200
assert b"show running-config" in examples.content
assert b"cisco_ios" in examples.content
assert b"cisco_xe" in examples.content

print(
    {
        "registry": sorted(expected_values),
        "quick_setup": quick_setup.status_code,
        "platform_mapping": mapping_add.status_code,
        "examples": examples.status_code,
    }
)
