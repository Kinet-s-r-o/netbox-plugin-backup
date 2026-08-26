import django_tables2 as tables
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
{% if perms.netbox_config_backup.view_backupdestination and perms.netbox_config_backup.change_backupdestination %}
<button type="submit" class="btn btn-sm btn-outline-info" title="Test destination"
        aria-label="Test destination {{ record.name }}"
        formaction="{% url 'plugins:netbox_config_backup:backupdestination_test_connection' pk=record.pk %}"
        formmethod="post" formnovalidate>
  <i class="mdi mdi-lan-connect"></i>
</button>
{% endif %}
"""


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
    verify_host_key = columns.BooleanColumn()

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
            "verify_host_key",
        )
        default_columns = (
            "name",
            "protocol",
            "address_preference",
            "port",
            "connect_timeout",
            "command_timeout",
            "verify_host_key",
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
    enabled = columns.BooleanColumn()
    auto_replicate = columns.BooleanColumn()
    integrity_audit_enabled = columns.BooleanColumn(verbose_name="Automatic audit")
    next_integrity_audit_at = columns.DateTimeColumn(verbose_name="Next audit")
    actions = columns.ActionsColumn(extra_buttons=TEST_DESTINATION_BUTTON)

    class Meta(NetBoxTable.Meta):
        model = BackupDestination
        fields = (
            "pk",
            "name",
            "enabled",
            "auto_replicate",
            "integrity_audit_enabled",
            "host",
            "port",
            "base_path",
            "credential_profile",
            "last_success_at",
            "last_integrity_audit_status",
            "next_integrity_audit_at",
            "last_error_code",
        )
        default_columns = (
            "name",
            "enabled",
            "auto_replicate",
            "integrity_audit_enabled",
            "host",
            "port",
            "base_path",
            "last_success_at",
            "last_integrity_audit_status",
            "next_integrity_audit_at",
            "last_error_code",
        )


class RevisionReplicaTable(NetBoxTable):
    created = columns.DateTimeColumn()
    revision = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    remote_available = columns.BooleanColumn(verbose_name="Available on FTP")
    remote_deleted_at = columns.DateTimeColumn(verbose_name="FTP expired at")
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
    remote_retention_policy = tables.Column(verbose_name="FTP retention", linkify=True)
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
    actions = columns.ActionsColumn(actions=())

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
    actions = columns.ActionsColumn(actions=())

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
