import django_tables2 as tables
from django.utils.html import format_html
from netbox.tables import NetBoxTable, columns

from .models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    PlatformMapping,
    RemoteRetentionPolicy,
    RetentionPolicy,
    RevisionReplica,
    SftpReceiverProfile,
)

RUN_BUTTON = """
{% if perms.netbox_config_backup.add_backuprun %}
<button type="submit" class="btn btn-sm btn-primary" title="Run backup"
        formaction="{% url 'plugins:netbox_config_backup:backuptarget_run' pk=record.pk %}"
        formmethod="post" formnovalidate>
  <i class="mdi mdi-play"></i>
</button>
{% endif %}
"""

TEST_CONNECTION_BUTTON = """
{% if perms.netbox_config_backup.add_backuprun %}
<button type="submit" class="btn btn-sm btn-outline-info" title="Test connection"
        formaction="{% url 'plugins:netbox_config_backup:backuptarget_test_connection' pk=record.pk %}"
        formmethod="post" formnovalidate>
  <i class="mdi mdi-lan-connect"></i>
</button>
{% endif %}
"""

TEST_DESTINATION_BUTTON = """
{% if record.protocol != 'local' and record.protocol != 'sftp' and perms.netbox_config_backup.view_backupdestination and perms.netbox_config_backup.change_backupdestination %}
<button type="submit" class="btn btn-sm btn-outline-info" title="Test storage"
        aria-label="Test storage {{ record.name }}"
        formaction="{% url 'plugins:netbox_config_backup:backupdestination_test_connection' pk=record.pk %}"
        formmethod="post" formnovalidate>
  <i class="mdi mdi-lan-connect"></i>
</button>
{% endif %}
"""

CANCEL_QUEUED_RUN_BUTTON = """
{% if record.status == 'queued' and perms.netbox_config_backup.change_backuptarget %}
<button type="submit" class="btn btn-sm btn-outline-danger" title="Cancel queued backup"
        aria-label="Cancel queued backup {{ record.pk }}"
        formaction="{% url 'plugins:netbox_config_backup:backuprun_cancel' pk=record.pk %}"
        formmethod="post" formnovalidate
        onclick="return confirm('Cancel this queued backup?');">
  <i class="mdi mdi-close-circle-outline" aria-hidden="true"></i>
</button>
{% endif %}
"""

DELETE_REVISION_EVERYWHERE_BUTTON = """
{% if not record.protected and perms.netbox_config_backup.delete_configrevision and perms.netbox_config_backup.delete_configartifact and perms.netbox_config_backup.delete_revisionreplica and perms.netbox_config_backup.view_backupdestination %}
<a class="btn btn-sm btn-danger" title="Delete revision everywhere"
   aria-label="Delete revision {{ record.pk }} everywhere"
   href="{% url 'plugins:netbox_config_backup:configrevision_delete_everywhere' pk=record.pk %}">
  <i class="mdi mdi-delete-forever-outline" aria-hidden="true"></i>
</a>
{% endif %}
"""

STORAGE_ACTION_BUTTONS = (
    TEST_DESTINATION_BUTTON
    + """
{% if perms.netbox_config_backup.change_backupdestination %}
<a class="btn btn-sm btn-warning" title="Edit storage"
   href="{% url 'plugins:netbox_config_backup:backupdestination_edit' pk=record.pk %}">
  <i class="mdi mdi-pencil" aria-hidden="true"></i>
</a>
{% endif %}
{% if record.protocol != 'local' and record.protocol != 'sftp' and not record.is_default and perms.netbox_config_backup.delete_backupdestination %}
<a class="btn btn-sm btn-danger" title="Delete storage"
   href="{% url 'plugins:netbox_config_backup:backupdestination_delete' pk=record.pk %}">
  <i class="mdi mdi-trash-can-outline" aria-hidden="true"></i>
</a>
{% endif %}
"""
)


class RetentionPolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = RetentionPolicy
        fields = (
            "pk",
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "minimum_changed_revisions",
            "max_runs_per_target",
        )
        default_columns = (
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "max_runs_per_target",
        )


class RemoteRetentionPolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = RemoteRetentionPolicy
        fields = (
            "pk",
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "minimum_changed_revisions",
            "max_copies_per_target",
        )
        default_columns = (
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "max_copies_per_target",
        )


class BackupPolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = BackupPolicy
        fields = (
            "pk",
            "name",
            "enabled",
            "schedule_type",
            "interval_minutes",
            "time_of_day",
            "store_mode",
            "retention_policy",
        )
        default_columns = (
            "name",
            "enabled",
            "schedule_type",
            "interval_minutes",
            "time_of_day",
            "store_mode",
            "retention_policy",
        )


class ConnectionProfileTable(NetBoxTable):
    name = tables.Column(linkify=True)
    host_key_policy = tables.Column(
        accessor="host_key_policy_label",
        verbose_name="SSH identity verification",
        orderable=False,
    )

    class Meta(NetBoxTable.Meta):
        model = ConnectionProfile
        fields = (
            "pk",
            "name",
            "protocol",
            "address_preference",
            "port",
            "connect_timeout",
            "command_timeout",
            "host_key_policy",
        )
        default_columns = (
            "name",
            "protocol",
            "address_preference",
            "port",
            "connect_timeout",
            "command_timeout",
            "host_key_policy",
        )


class CredentialProfileTable(NetBoxTable):
    name = tables.Column(linkify=True)
    username = tables.Column(accessor="stored_username", orderable=False)
    password = tables.Column(empty_values=(), orderable=False)

    def render_password(self, record):
        return "Configured" if record.has_stored_password else "Not stored"

    class Meta(NetBoxTable.Meta):
        model = CredentialProfile
        fields = (
            "pk",
            "name",
            "provider_id",
            "secret_reference",
            "auth_type",
            "username",
            "password",
        )
        default_columns = (
            "name",
            "provider_id",
            "username",
            "password",
            "auth_type",
        )


class BackupDestinationTable(NetBoxTable):
    name = tables.Column(linkify=True)
    storage_type = tables.Column(empty_values=(), verbose_name="Type", orderable=False)
    retention_policy = tables.TemplateColumn(
        template_code="""
        {% load helpers %}
        {% if record.protocol == 'local' %}
          {{ record.local_retention_policy|linkify|placeholder }}
        {% else %}
          {{ record.remote_retention_policy|linkify|placeholder }}
        {% endif %}
        """,
        verbose_name="Retention",
        orderable=False,
    )
    precedence = tables.Column(empty_values=(), verbose_name="Precedence", orderable=False)
    enabled = columns.BooleanColumn()
    auto_replicate = columns.BooleanColumn()
    integrity_audit_enabled = columns.BooleanColumn(verbose_name="Automatic audit")
    next_integrity_audit_at = columns.DateTimeColumn(verbose_name="Next audit")
    actions = columns.ActionsColumn(actions=(), extra_buttons=STORAGE_ACTION_BUTTONS)

    @staticmethod
    def render_storage_type(record):
        if record.protocol == "local":
            return format_html(
                '<span class="badge text-bg-primary">{}</span> '
                '<span class="badge text-bg-secondary"><i class="mdi mdi-lock-outline"></i> '
                "{}</span>",
                "Local",
                "Default",
            )
        labels = {
            "ftp": ("FTP", "info"),
            "nfs": ("NFS", "success"),
            "smb": ("SMB3", "success"),
        }
        label, color = labels.get(record.protocol, (record.get_protocol_display(), "secondary"))
        return format_html('<span class="badge text-bg-{}">{}</span>', color, label)

    @staticmethod
    def render_precedence(record):
        if record.enforce_retention_policy:
            return format_html(
                '<span class="badge text-bg-warning">{}</span>',
                "Storage policy enforced",
            )
        return format_html('<span class="text-secondary">{}</span>', "Device may override")

    class Meta(NetBoxTable.Meta):
        model = BackupDestination
        fields = (
            "pk",
            "name",
            "storage_type",
            "enabled",
            "auto_replicate",
            "retention_policy",
            "precedence",
            "integrity_audit_enabled",
            "host",
            "port",
            "base_path",
            "mount_path",
            "credential_profile",
            "last_success_at",
            "last_integrity_audit_status",
            "next_integrity_audit_at",
            "last_error_code",
        )
        default_columns = (
            "name",
            "storage_type",
            "enabled",
            "auto_replicate",
            "retention_policy",
            "precedence",
            "integrity_audit_enabled",
            "last_success_at",
            "last_integrity_audit_status",
            "last_error_code",
        )


class RevisionReplicaTable(NetBoxTable):
    created = columns.DateTimeColumn()
    revision = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    remote_available = columns.BooleanColumn(verbose_name="Available remotely")
    remote_deleted_at = columns.DateTimeColumn(verbose_name="Remote copy expired at")
    actions = columns.ActionsColumn(actions=())

    class Meta(NetBoxTable.Meta):
        model = RevisionReplica
        fields = (
            "created",
            "revision",
            "status",
            "remote_available",
            "remote_deleted_at",
            "attempts",
            "bytes_transferred",
            "finished_at",
            "next_retry_at",
            "remote_path",
            "error_code",
        )
        default_columns = (
            "created",
            "revision",
            "status",
            "remote_available",
            "remote_deleted_at",
            "attempts",
            "bytes_transferred",
            "finished_at",
            "error_code",
        )


class SftpReceiverProfileTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = SftpReceiverProfile
        fields = (
            "pk",
            "name",
            "enabled",
            "protocol",
            "mode",
            "listen_host",
            "listen_port",
            "advertised_host",
            "advertised_port",
            "credential_profile",
        )
        default_columns = (
            "name",
            "enabled",
            "protocol",
            "mode",
            "listen_host",
            "listen_port",
            "advertised_host",
            "advertised_port",
            "credential_profile",
        )


class PlatformMappingTable(NetBoxTable):
    platform = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = PlatformMapping
        fields = (
            "pk",
            "platform",
            "driver_id",
            "enabled",
            "connection_profile",
            "credential_profile",
            "receiver_profile",
        )
        default_columns = (
            "platform",
            "driver_id",
            "enabled",
            "connection_profile",
            "credential_profile",
            "receiver_profile",
        )


class BackupTargetTable(NetBoxTable):
    device = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    status = columns.ChoiceFieldColumn()
    retention_override = tables.Column(verbose_name="Local retention", linkify=True)
    remote_retention_policy = tables.Column(verbose_name="Remote retention", linkify=True)
    last_revision = tables.Column(linkify=True)
    actions = columns.ActionsColumn(extra_buttons=TEST_CONNECTION_BUTTON + RUN_BUTTON)

    class Meta(NetBoxTable.Meta):
        model = BackupTarget
        fields = (
            "pk",
            "device",
            "enabled",
            "status",
            "driver_override",
            "driver_options_override",
            "retention_override",
            "remote_retention_policy",
            "last_success_at",
            "last_change_at",
            "consecutive_failures",
            "last_revision",
        )
        default_columns = (
            "device",
            "enabled",
            "status",
            "driver_override",
            "last_success_at",
            "last_change_at",
            "consecutive_failures",
            "last_revision",
        )


class BackupRunTable(NetBoxTable):
    queued_at = columns.DateTimeColumn(linkify=True)
    target = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    stuck = columns.BooleanColumn(accessor="is_stuck", orderable=False)
    revision = tables.Column(linkify=True)
    actions = columns.ActionsColumn(actions=(), extra_buttons=CANCEL_QUEUED_RUN_BUTTON)

    class Meta(NetBoxTable.Meta):
        model = BackupRun
        fields = (
            "pk",
            "queued_at",
            "target",
            "source",
            "status",
            "stuck",
            "changed",
            "raw_changed",
            "finished_at",
            "revision",
            "error_code",
        )
        default_columns = (
            "queued_at",
            "target",
            "source",
            "status",
            "stuck",
            "changed",
            "finished_at",
            "revision",
            "error_code",
        )


class ConfigRevisionTable(NetBoxTable):
    created = columns.DateTimeColumn(linkify=True)
    target = tables.Column(linkify=True)
    protected = columns.BooleanColumn()
    actions = columns.ActionsColumn(
        actions=(),
        extra_buttons=DELETE_REVISION_EVERYWHERE_BUTTON,
    )

    class Meta(NetBoxTable.Meta):
        model = ConfigRevision
        fields = (
            "pk",
            "created",
            "target",
            "driver_id",
            "content_changed",
            "normalized_hash",
            "protected",
            "label",
        )
        default_columns = (
            "created",
            "target",
            "driver_id",
            "content_changed",
            "protected",
            "label",
        )
