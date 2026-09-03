"""Driver selection/settings UI regression checks; roll back all DB test changes.

No device/FTP connections or backup artifacts are created. Set
NCB_SETTINGS_PREVIEW_DIR to export synthetic HTML pages for browser QA.
"""

import os
import re
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client
from django.urls import reverse
from users.models import ObjectPermission

from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.fake import FakeDriver
from netbox_config_backup.forms import BackupTargetForm, PlatformMappingForm, QuickSetupForm
from netbox_config_backup.forms_setup import DriverSettingsForm
from netbox_config_backup.models import BackupTarget, OperationalSettings, PlatformMapping
from netbox_config_backup.services.driver_selection import driver_usage_counts, selectable_drivers
from netbox_config_backup.services.runtime import build_backup_pipeline, build_connection_tester


class ExternalTestDriver(FakeDriver):
    driver_id = "settings_smoke_external"
    display_name = 'External <test> "driver"'


def rejected(action):
    try:
        action()
    except ValidationError:
        return
    raise AssertionError("A disabled driver was accepted")


prefix = f"ncb-driver-settings-{uuid4().hex[:8]}"
with transaction.atomic():
    driver_registry.register(ExternalTestDriver)
    try:
        settings_row, _ = OperationalSettings.objects.get_or_create(singleton=True)
        OperationalSettings.objects.filter(pk=settings_row.pk).update(
            disabled_driver_ids=[], ui_language="en",
        )
        settings_row.refresh_from_db()
        before = OperationalSettings.objects.filter(pk=settings_row.pk).values().get()
        user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
        client = Client(HTTP_ACCEPT_LANGUAGE="en")
        client.force_login(user)
        url = reverse("plugins:netbox_config_backup:advanced_settings")
        all_ids = {driver.driver_id for driver in selectable_drivers()}
        assert "siae_alfoplus" not in all_ids and "siae_smos_auto" in all_ids
        assert ExternalTestDriver.driver_id in all_ids
        unused_id = ExternalTestDriver.driver_id

        def post_drivers(ids):
            return client.post(url, {"settings_action": "drivers", "enabled_drivers": sorted(ids)})

        assert client.get(url).status_code == 200
        # A browser submits checked, locked In-use entries through hidden inputs.
        enabled = all_ids - {unused_id}
        assert post_drivers(enabled).status_code == 302
        settings_row.refresh_from_db()
        assert settings_row.disabled_driver_ids == [unused_id]

        # Language saves preserve driver choices in both directions, and invalid
        # input remains a form error rather than resetting the saved language.
        assert client.post(url, {"settings_action": "language", "ui_language": "sk"}).status_code == 302
        settings_row.refresh_from_db()
        assert settings_row.ui_language == "sk" and settings_row.disabled_driver_ids == [unused_id]
        assert client.post(url, {"settings_action": "language", "ui_language": "invalid"}).status_code == 400
        settings_row.refresh_from_db()
        assert settings_row.ui_language == "sk"
        assert client.post(url, {"settings_action": "language", "ui_language": "en"}).status_code == 302
        after = OperationalSettings.objects.filter(pk=settings_row.pk).values().get()
        for field, value in before.items():
            if field not in {"disabled_driver_ids", "last_updated"}:
                assert after[field] == value, field

        for form_class, field in (
            (BackupTargetForm, "driver_override"),
            (PlatformMappingForm, "driver_id"),
            (QuickSetupForm, "driver_id"),
        ):
            assert unused_id not in dict(form_class().fields[field].choices)

        # A disabled driver is rejected before network access, even if DB edits
        # bypassed normal model/form validation. Historical registry stays intact.
        pipeline = build_backup_pipeline()
        pipeline.repository = Mock()
        pipeline.repository.get_execution_context.return_value.driver_id = unused_id
        pipeline.storage = Mock()
        with patch.object(ExternalTestDriver, "collect") as collect:
            outcome = pipeline.execute(123)
            assert outcome.error_code == "DRIVER_DISABLED", outcome
            pipeline.storage.put.assert_not_called()
            collect.assert_not_called()
            tester = build_connection_tester()
            tester.repository = Mock()
            tester.repository.get_target_execution_context.return_value.driver_id = unused_id
            result = tester.execute(123)
            assert result.error_code == "DRIVER_DISABLED", result
            collect.assert_not_called()
        assert isinstance(driver_registry.create(unused_id), ExternalTestDriver)

        site = Site.objects.create(name=prefix, slug=prefix)
        manufacturer = Manufacturer.objects.create(name=prefix, slug=prefix)
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model=prefix, slug=prefix)
        role = DeviceRole.objects.create(name=prefix, slug=prefix)
        platform = Platform.objects.create(name=prefix, slug=prefix)
        device = Device.objects.create(
            name=prefix, site=site, role=role, device_type=device_type, platform=platform,
        )
        target = BackupTarget(device=device, driver_override=unused_id)
        mapping = PlatformMapping(platform=platform, driver_id=unused_id)
        rejected(target.clean)
        rejected(target.save)
        rejected(mapping.clean)
        rejected(mapping.save)
        assert post_drivers(all_ids).status_code == 302
        target.save()
        assert post_drivers(enabled).status_code == 400
        settings_row.refresh_from_db()
        assert settings_row.disabled_driver_ids == []
        def disable_in_use():
            edited = OperationalSettings.objects.get(pk=settings_row.pk)
            edited.disabled_driver_ids = [unused_id]
            edited.save()

        rejected(disable_in_use)
        target.driver_override = ""
        target.save()
        # Disabled mappings still protect their driver assignment.
        mapping.enabled = False
        mapping.save()
        assert post_drivers(enabled).status_code == 400
        # Legacy SIAE assignments lock the single public SIAE checkbox.
        mapping.driver_id = "siae_alfoplus"
        mapping.save()
        assert post_drivers(all_ids - {"siae_smos_auto"}).status_code == 400
        assert driver_usage_counts()["siae_smos_auto"] > 0
        assert post_drivers(enabled).status_code == 302

        assert client.post(url, {"settings_action": "language", "ui_language": "en"}).status_code == 302
        assert client.post(url, {"settings_action": "notifications", "events_enabled": "on"}).status_code == 302
        settings_row.refresh_from_db()
        assert settings_row.disabled_driver_ids == [unused_id]

        response = post_drivers(enabled | {"unknown_driver"})
        assert response.status_code == 400
        assert re.search(rb'id="device-drivers"\s+open', response.content)
        # In-use checks remain checked after an invalid submission.
        in_use_row = next(row for row in DriverSettingsForm({}, instance=settings_row).rows
                          if row["value"] == "siae_smos_auto")
        assert in_use_row["in_use"] and in_use_row["checked"]

        # Independent saves and validation expand only the affected group.
        invalid_retention = client.post(url, {
            "settings_action": "retention", "retention_scheduler_enabled": "on",
            "retention_scheduler_batch_size": 25,
        })
        if not before["retention_scheduler_enabled"]:
            assert invalid_retention.status_code == 400
            assert re.search(rb'id="automation"\s+open', invalid_retention.content)
        invalid_download = client.post(url, {
            "settings_action": "download_encryption", "download_zip_password": "x",
            "download_zip_password_confirm": "different",
        })
        assert invalid_download.status_code == 400
        assert re.search(rb'id="security"\s+open', invalid_download.content)

        limited = get_user_model().objects.create_user(username=f"{prefix}-reader")
        permission = ObjectPermission.objects.create(name=prefix, actions=["view"])
        permission.object_types.add(ContentType.objects.get_for_model(OperationalSettings))
        permission.users.add(limited)
        readonly = Client(HTTP_ACCEPT_LANGUAGE="en")
        readonly.force_login(limited)
        page = readonly.get(url)
        assert page.status_code == 200
        assert b"> Save drivers</button>" not in page.content
        assert readonly.post(url, {"settings_action": "drivers"}).status_code == 403

        # English/Slovak and light/dark fixtures use the actual Settings template.
        output_dir = os.environ.get("NCB_SETTINGS_PREVIEW_DIR")
        for language, heading in (("en", "Device drivers"), ("sk", "Ovládače zariadení")):
            OperationalSettings.objects.filter(pk=settings_row.pk).update(ui_language=language)
            page = client.get(url)
            assert page.status_code == 200
            html = page.content.decode()
            assert heading in html
            assert 'External &lt;test&gt; &quot;driver&quot;' in html
            assert "{{" not in html and "{%" not in html
            assert re.search(r'id="device-drivers"\s*>', html)
            for theme in ("light", "dark"):
                if output_dir:
                    fixture = re.sub(r'data-bs-theme="[^"]*"', f'data-bs-theme="{theme}"', html)
                    if f'data-bs-theme="{theme}"' not in fixture:
                        fixture = fixture.replace("<html", f'<html data-bs-theme="{theme}"', 1)
                    # Prevent NetBox's saved-browser-theme code overriding the fixture.
                    fixture = re.sub(r'<script\b[^>]*>.*?</script>', '', fixture, flags=re.DOTALL)
                    fixture = fixture.replace("<head>", '<head><base href="http://localhost:8000/">')
                    directory = Path(output_dir)
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / f"settings-{language}-{theme}.html").write_text(fixture, encoding="utf-8")

        # Preserve disabled settings for a package temporarily absent at startup.
        settings_row.refresh_from_db()
        settings_row.disabled_driver_ids = ["temporarily_uninstalled"]
        form = DriverSettingsForm({"enabled_drivers": sorted(all_ids)}, instance=settings_row)
        assert form.is_valid(), form.errors
        assert form.disabled_ids == ["temporarily_uninstalled"]
        transaction.set_rollback(True)
    finally:
        driver_registry.unregister(ExternalTestDriver.driver_id)

assert not Device.objects.filter(name=prefix).exists()
print({"driver_settings": "passed", "test_rows_rolled_back": True, "device_connections": 0})
