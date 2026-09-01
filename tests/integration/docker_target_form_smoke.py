"""Verify that target creation and editing use the same model form."""

from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_config_backup.forms import BackupTargetForm
from netbox_config_backup.models import BackupTarget

prefix = f"ncb-target-form-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(
    name=f"{prefix}-manufacturer",
    slug=f"{prefix}-manufacturer",
)
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
platform = Platform.objects.create(name=f"{prefix}-platform", slug=f"{prefix}-platform")
configured_device = Device.objects.create(
    name=f"{prefix}-configured",
    site=site,
    role=role,
    device_type=device_type,
    platform=platform,
)
available_device = Device.objects.create(
    name=f"{prefix}-available",
    site=site,
    role=role,
    device_type=device_type,
    platform=platform,
)
existing_target = BackupTarget.objects.create(
    device=configured_device,
    enabled=True,
    driver_override="fake",
)

user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert user is not None
client = Client()
client.force_login(user)

add_form = BackupTargetForm()
assert configured_device not in add_form.fields["device"].queryset
assert available_device in add_form.fields["device"].queryset

edit_form = BackupTargetForm(instance=existing_target)
assert configured_device in edit_form.fields["device"].queryset
assert available_device in edit_form.fields["device"].queryset

add_url = reverse("plugins:netbox_config_backup:backuptarget_add")
edit_url = reverse(
    "plugins:netbox_config_backup:backuptarget_edit",
    kwargs={"pk": existing_target.pk},
)
expected_fields = (
    b"device",
    b"enabled",
    b"policy_override",
    b"retention_override",
    b"remote_retention_policy",
    b"credential_override",
    b"connection_override",
    b"receiver_override",
    b"driver_override",
    b"driver_options_override",
)
for url in (add_url, edit_url):
    response = client.get(url)
    assert response.status_code == 200, response.status_code
    for field_name in expected_fields:
        assert b'name="' + field_name + b'"' in response.content, (url, field_name)

response = client.post(
    add_url,
    {
        "device": available_device.pk,
        "enabled": "on",
        "policy_override": "",
        "retention_override": "",
        "remote_retention_policy": "",
        "credential_override": "",
        "connection_override": "",
        "receiver_override": "",
        "driver_override": "fake",
        "driver_options_override": "{}",
    },
)
assert response.status_code == 302, response.context and response.context.get("form").errors
created_target = BackupTarget.objects.get(device=available_device)
assert created_target.driver_override == "fake"

print(
    {
        "add": add_url,
        "edit": edit_url,
        "field_count": len(expected_fields),
        "created_target": created_target.pk,
    }
)
