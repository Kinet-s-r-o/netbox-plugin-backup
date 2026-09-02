"""Check the Device tab after revision removal without keeping test data or files."""

from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import Client, RequestFactory
from django.urls import reverse
from users.models import ObjectPermission

from netbox_config_backup.choices import RunStatusChoices, TargetStatusChoices
from netbox_config_backup.models import BackupRun, BackupTarget, ConfigArtifact, ConfigRevision
from netbox_config_backup.services.revision_deletion import delete_config_revision_everywhere
from netbox_config_backup.storage.local import LocalConfigStorage
from netbox_config_backup.views import DeviceConfigBackupView

prefix = f"ncb-device-history-{uuid4().hex[:8]}"
with transaction.atomic(), TemporaryDirectory(prefix=f"{prefix}-") as storage_root:
    site = Site.objects.create(name=prefix, slug=prefix)
    manufacturer = Manufacturer.objects.create(name=prefix, slug=prefix)
    device_type = DeviceType.objects.create(manufacturer=manufacturer, model=prefix, slug=prefix)
    role = DeviceRole.objects.create(name=prefix, slug=prefix)
    device = Device.objects.create(name=prefix, site=site, role=role, device_type=device_type)
    target = BackupTarget.objects.create(device=device, enabled=True, driver_override="fake")
    user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
    client = Client(HTTP_ACCEPT_LANGUAGE="en")
    client.force_login(user)
    url = reverse("dcim:device_config_backup", kwargs={"pk": device.pk})
    storage = LocalConfigStorage(storage_root)

    def context_for(viewer):
        request = RequestFactory().get(url)
        request.user = viewer
        return DeviceConfigBackupView().get_extra_context(request, device)

    def create_revision(label, *, previous=None):
        payload = f"hostname example\nrevision {label}\n".encode()
        digest = sha256(payload).hexdigest()
        revision = ConfigRevision.objects.create(
            target=target,
            normalized_hash=digest,
            normalizer_version="test",
            driver_id="fake",
            content_changed=True,
            previous_revision=previous,
        )
        key = f"{revision.revision_uuid}/configuration.txt"
        storage.put(key, payload)
        ConfigArtifact.objects.create(
            revision=revision,
            artifact_type="running_config",
            format="text",
            storage_key=key,
            size=len(payload),
            raw_hash=digest,
            normalized_hash=digest,
            is_primary=True,
        )
        target.last_revision = revision
        target.status = TargetStatusChoices.HEALTHY
        target.last_success_at = revision.created
        target.last_change_at = revision.created
        target.next_run_at = revision.created + timedelta(days=1)
        target.save()
        run = BackupRun.objects.create(
            target=target,
            status=RunStatusChoices.SUCCESS_CHANGED,
            revision=revision,
            changed=True,
            finished_at=revision.created,
        )
        return revision, run

    # A device which has never run also gets an honest empty state.
    fresh = client.get(url)
    assert fresh.status_code == 200
    assert b"No stored backup" in fresh.content
    assert b"Revision removed" not in fresh.content
    assert context_for(user)["no_stored_revisions"] is True

    older, older_run = create_revision("older")
    latest, latest_run = create_revision("latest", previous=older)
    unchanged_run = BackupRun.objects.create(
        target=target, status=RunStatusChoices.SUCCESS_UNCHANGED, revision=latest
    )
    failed_run = BackupRun.objects.create(target=target, status=RunStatusChoices.FAILED)
    assert b"No stored backup" not in client.get(url).content

    # A hidden revision must never leak via a visible BackupRun relationship.
    limited = get_user_model().objects.create_user(username=f"{prefix}-limited")
    for model in (Device, BackupTarget, BackupRun, ConfigRevision):
        permission = ObjectPermission.objects.create(
            name=f"{prefix}-{model._meta.model_name}",
            actions=["view"],
            constraints={"pk": older.pk} if model is ConfigRevision else None,
        )
        permission.object_types.add(ContentType.objects.get_for_model(model))
        permission.users.add(limited)
    limited_client = Client(HTTP_ACCEPT_LANGUAGE="en")
    limited_client.force_login(limited)
    restricted = limited_client.get(url)
    assert restricted.status_code == 200
    assert older.get_absolute_url().encode() in restricted.content
    assert latest.get_absolute_url().encode() not in restricted.content
    assert str(latest.revision_uuid).encode() not in restricted.content
    assert b"Revision removed" not in restricted.content
    assert b"No access" in restricted.content

    # Deleting an older revision preserves the current one and its download links.
    delete_config_revision_everywhere(older.pk, storage=storage)
    remaining = client.get(url)
    assert b"No stored backup" not in remaining.content
    assert remaining.content.count(b"Revision removed") == 1
    assert latest.get_absolute_url().encode() in remaining.content
    assert context_for(user)["revision_count"] == 1
    restricted = limited_client.get(url)
    assert b"No accessible revisions" in restricted.content
    assert b"No stored backup" not in restricted.content

    # Deleting the final revision changes availability, not audit facts or scheduling.
    original_audit = (target.last_success_at, target.last_change_at, target.next_run_at)
    delete_config_revision_everywhere(latest.pk, storage=storage)
    empty = client.get(url)
    assert empty.status_code == 200
    assert b"No stored backup" in empty.content
    assert b"Backup activity" in empty.content
    assert b"Last successful run" in empty.content
    assert b"Last detected change" in empty.content
    assert b"No revisions have been created" not in empty.content
    assert b"No configuration revision is available yet" not in empty.content
    assert empty.content.count(b"Revision removed") == 3
    assert latest.get_absolute_url().encode() not in empty.content
    target.refresh_from_db()
    assert target.last_revision_id is None
    assert target.status == TargetStatusChoices.HEALTHY
    assert (target.last_success_at, target.last_change_at, target.next_run_at) == original_audit
    assert BackupRun.objects.filter(target=target).count() == 4
    for run in (older_run, latest_run, unchanged_run):
        run.refresh_from_db()
        assert run.revision_id is None
        assert run.status in {RunStatusChoices.SUCCESS_CHANGED, RunStatusChoices.SUCCESS_UNCHANGED}
    runs = {run.pk: run for run in context_for(user)["recent_runs"]}
    assert runs[failed_run.pk].device_backup_revision_removed is False
    assert not ConfigArtifact.objects.filter(revision__target=target).exists()
    assert not list(Path(storage_root).rglob("configuration.txt"))

    # A later backup restores the normal summary; it does not rewrite old runs.
    restored, _ = create_revision("restored")
    after_new_backup = client.get(url)
    assert b"No stored backup" not in after_new_backup.content
    assert restored.get_absolute_url().encode() in after_new_backup.content
    assert after_new_backup.content.count(b"Revision removed") == 3
    transaction.set_rollback(True)

assert not BackupTarget.objects.filter(device__name=prefix).exists()
assert not get_user_model().objects.filter(username__startswith=prefix).exists()
assert not Path(storage_root).exists()
print({"device_backup_history": "passed", "audit_preserved": True, "test_data_rolled_back": True})
