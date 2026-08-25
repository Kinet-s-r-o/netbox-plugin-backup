"""Verify permission-gated, redacted revision previews and diffs in NetBox."""

import hashlib
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from netbox_config_backup.models import BackupTarget, ConfigArtifact, ConfigRevision
from netbox_config_backup.storage.local import LocalConfigStorage

prefix = f"ncb-viewer-{uuid4().hex[:8]}"
site = Site.objects.create(name=f"{prefix}-site", slug=f"{prefix}-site")
manufacturer = Manufacturer.objects.create(name=f"{prefix}-mfr", slug=f"{prefix}-mfr")
device_type = DeviceType.objects.create(
    manufacturer=manufacturer,
    model=f"{prefix}-type",
    slug=f"{prefix}-type",
)
role = DeviceRole.objects.create(name=f"{prefix}-role", slug=f"{prefix}-role")
device = Device.objects.create(
    name=f"{prefix}-device",
    site=site,
    role=role,
    device_type=device_type,
)
target = BackupTarget.objects.create(device=device, driver_override="mikrotik_routeros")
storage = LocalConfigStorage(settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"])


def create_revision(content, *, previous=None):
    raw_hash = hashlib.sha256(content).hexdigest()
    revision = ConfigRevision.objects.create(
        target=target,
        normalized_hash=raw_hash,
        normalizer_version="1",
        driver_id="mikrotik_routeros",
        previous_revision=previous,
    )
    storage_key = f"viewer-smoke/{revision.revision_uuid}/running-config.rsc"
    storage.put(storage_key, content)
    ConfigArtifact.objects.create(
        revision=revision,
        artifact_type="running_config",
        format="routeros_script",
        storage_key=storage_key,
        size=len(content),
        raw_hash=raw_hash,
        normalized_hash=raw_hash,
        is_primary=True,
    )
    return revision


before = create_revision(
    b"/system identity set name=old\n/ppp secret add name=user password=old-secret\n"
)
after = create_revision(
    b"/system identity set name=new\n"
    b"/ppp secret add name=user password=new-secret\n"
    b"/system note set note=<script>alert(1)</script>\n",
    previous=before,
)

limited = get_user_model().objects.create_user(username=f"{prefix}-limited")
limited.user_permissions.add(
    Permission.objects.get(
        content_type__app_label="netbox_config_backup",
        codename="view_configrevision",
    )
)
limited_client = Client()
limited_client.force_login(limited)
content_url = reverse(
    "plugins:netbox_config_backup:configrevision_content", kwargs={"pk": after.pk}
)
assert limited_client.get(content_url).status_code == 403

admin = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
admin_client = Client()
admin_client.force_login(admin)
content_response = admin_client.get(content_url)
assert content_response.status_code == 200
assert b"new-secret" not in content_response.content
assert b"&lt;redacted&gt;" in content_response.content
assert b"<script>alert(1)</script>" not in content_response.content
assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in content_response.content

diff_url = reverse("plugins:netbox_config_backup:configrevision_diff", kwargs={"pk": after.pk})
diff_response = admin_client.get(diff_url)
assert diff_response.status_code == 200
assert b"name=old" in diff_response.content
assert b"name=new" in diff_response.content
assert b"old-secret" not in diff_response.content
assert b"new-secret" not in diff_response.content

print(
    {
        "content_status": content_response.status_code,
        "diff_status": diff_response.status_code,
        "permission_denied_without_artifact_access": True,
        "secrets_redacted": True,
        "html_escaped": True,
    }
)
