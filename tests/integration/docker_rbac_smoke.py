"""Exercise NetBox object permissions and constrained direct URLs."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import Client
from users.models import Group, ObjectPermission, User

from netbox_config_backup.models import (
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    RemoteRetentionPolicy,
    RevisionReplica,
)
from netbox_config_backup.views import _assert_target_retention_assignment_permissions

prefix = "[RBAC smoke]"
User.objects.filter(username__startswith="ncb-rbac-smoke-").delete()
ObjectPermission.objects.filter(name__startswith=prefix).delete()


def user(name):
    value = User.objects.create(username=f"ncb-rbac-smoke-{name}", is_active=True)
    value.set_unusable_password()
    value.save(update_fields=("password",))
    return value


def permission(name, *, model, actions, constraints=None):
    value = ObjectPermission.objects.create(
        name=f"{prefix} {name}",
        actions=list(actions),
        constraints=constraints,
    )
    value.object_types.add(ContentType.objects.get_for_model(model))
    return value


revisions = list(
    ConfigRevision.objects.filter(artifacts__is_primary=True).distinct().order_by("-pk")[:2]
)
assert len(revisions) == 2, "RBAC smoke requires two revisions"
allowed_revision, denied_revision = revisions
allowed_target = allowed_revision.target
denied_target = BackupTarget.objects.exclude(pk=allowed_target.pk).order_by("pk").first()
assert denied_target is not None, "RBAC smoke requires two targets"

try:
    no_access = user("none")
    client = Client()
    client.force_login(no_access)
    assert client.get("/plugins/config-backup/").status_code == 403
    assert client.get("/plugins/config-backup/settings/").status_code == 403
    assert client.get("/plugins/config-backup/ftp-retention-policies/").status_code == 403
    assert client.get("/plugins/config-backup/ssh-host-keys/").status_code == 403
    assert (
        client.get(f"/plugins/config-backup/revisions/{allowed_revision.pk}/content/").status_code
        == 403
    )

    reader = user("reader")
    reader.groups.add(Group.objects.get(name="Config Backup Readers"))
    client = Client()
    client.force_login(reader)
    assert client.get("/plugins/config-backup/").status_code == 200
    assert client.get("/plugins/config-backup/settings/").status_code == 403
    assert client.get("/plugins/config-backup/ftp-retention-policies/").status_code == 403
    assert client.get("/plugins/config-backup/ssh-host-keys/").status_code == 403
    assert (
        client.get(f"/plugins/config-backup/revisions/{allowed_revision.pk}/content/").status_code
        == 200
    )

    operator = user("operator")
    operator.groups.add(Group.objects.get(name="Config Backup Operators"))
    client = Client()
    client.force_login(operator)
    assert b"Add device" not in client.get("/plugins/config-backup/").content
    assert client.get("/plugins/config-backup/ssh-host-keys/").status_code == 200
    assert client.get("/plugins/config-backup/ftp-retention-policies/").status_code == 200
    assert operator.has_perm("netbox_config_backup.view_remoteretentionpolicy")
    assert not operator.has_perm("netbox_config_backup.add_remoteretentionpolicy")
    assert client.post("/plugins/config-backup/ssh-host-keys/scan/").status_code == 403
    for retention_field in ("retention_override", "remote_retention_policy"):
        try:
            _assert_target_retention_assignment_permissions(
                operator,
                local_retention_changed=retention_field == "retention_override",
                remote_retention_changed=retention_field == "remote_retention_policy",
            )
        except PermissionDenied:
            pass
        else:
            raise AssertionError(
                f"Operator unexpectedly changed privileged field {retention_field}."
            )

    delete_only = user("delete-only")
    for model in (BackupRun, ConfigArtifact, ConfigRevision, RevisionReplica):
        delete_only.object_permissions.add(
            permission(
                f"delete-only {model._meta.model_name}",
                model=model,
                actions=("delete",),
            )
        )
    for retention_field in ("retention_override", "remote_retention_policy"):
        try:
            _assert_target_retention_assignment_permissions(
                delete_only,
                local_retention_changed=retention_field == "retention_override",
                remote_retention_changed=retention_field == "remote_retention_policy",
            )
        except PermissionDenied:
            pass
        else:
            raise AssertionError(
                "Delete permissions without runtime-settings authority unexpectedly "
                f"changed {retention_field}."
            )

    administrator = user("administrator")
    administrator.groups.add(Group.objects.get(name="Config Backup Administrators"))
    client = Client()
    client.force_login(administrator)
    assert b"Add device" in client.get("/plugins/config-backup/").content
    assert client.get("/plugins/config-backup/ssh-host-keys/").status_code == 200
    assert client.get("/plugins/config-backup/ftp-retention-policies/").status_code == 200
    assert administrator.has_perm("netbox_config_backup.add_remoteretentionpolicy")
    _assert_target_retention_assignment_permissions(
        administrator,
        local_retention_changed=True,
        remote_retention_changed=True,
    )
    assert (
        Group.objects.get(name="Config Backup Administrators")
        .object_permissions.filter(
            object_types=ContentType.objects.get_for_model(RemoteRetentionPolicy)
        )
        .exists()
    )

    constrained = user("constrained")
    constrained.object_permissions.add(
        permission(
            "target view",
            model=BackupTarget,
            actions=("view",),
            constraints={"pk": allowed_target.pk},
        ),
        permission(
            "revision view",
            model=ConfigRevision,
            actions=("view",),
            constraints={"pk": allowed_revision.pk},
        ),
        permission(
            "artifact view",
            model=ConfigArtifact,
            actions=("view",),
            constraints={"revision_id": allowed_revision.pk},
        ),
        permission(
            "revision change",
            model=ConfigRevision,
            actions=("change",),
            constraints={"pk": allowed_revision.pk},
        ),
        permission(
            "run create",
            model=BackupRun,
            actions=("add",),
        ),
    )
    client = Client()
    client.force_login(constrained)
    home = client.get("/plugins/config-backup/")
    assert home.status_code == 200
    assert allowed_revision.get_absolute_url().encode() in home.content
    assert denied_revision.get_absolute_url().encode() not in home.content
    assert (
        client.get(f"/plugins/config-backup/revisions/{allowed_revision.pk}/content/").status_code
        == 200
    )
    assert (
        client.get(f"/plugins/config-backup/revisions/{denied_revision.pk}/content/").status_code
        == 404
    )
    assert (
        client.get(f"/plugins/config-backup/revisions/{denied_revision.pk}/diff/").status_code
        == 404
    )
    assert (
        client.post(
            f"/plugins/config-backup/revisions/{denied_revision.pk}/set-protection/",
            {"protected": "true"},
        ).status_code
        == 404
    )
    assert client.post(f"/plugins/config-backup/targets/{denied_target.pk}/run/").status_code == 404
finally:
    User.objects.filter(username__startswith="ncb-rbac-smoke-").delete()
    ObjectPermission.objects.filter(name__startswith=prefix).delete()

print("RBAC_SMOKE_OK")
