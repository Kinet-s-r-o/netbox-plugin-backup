from __future__ import annotations

import datetime
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from netbox.models import NetBoxModel
from netbox.models.features import JobsMixin

from .choices import (
    AddressPreferenceChoices,
    AuthTypeChoices,
    ConnectionProtocolChoices,
    DestinationProtocolChoices,
    FtpAuditFrequencyChoices,
    FtpAuditStatusChoices,
    ReceiverModeChoices,
    ReceiverProtocolChoices,
    ReplicaStatusChoices,
    RunSourceChoices,
    RunStatusChoices,
    ScheduleTypeChoices,
    SSHHostKeyStatusChoices,
    StoreModeChoices,
    TargetStatusChoices,
    WeekdayChoices,
)


class OperationalSettings(NetBoxModel):
    singleton = models.BooleanField(default=True, unique=True, editable=False)
    retention_scheduler_enabled = models.BooleanField(default=False)
    remote_retention_scheduler_enabled = models.BooleanField(default=False)
    retention_scheduler_batch_size = models.PositiveSmallIntegerField(
        default=25,
        validators=(MinValueValidator(1), MaxValueValidator(1000)),
    )
    events_enabled = models.BooleanField(default=True)
    notify_on_every_failure = models.BooleanField(default=False)

    class Meta:
        verbose_name = "operational settings"
        verbose_name_plural = "operational settings"

    def __str__(self) -> str:
        return "Config Backup operational settings"

    def get_absolute_url(self):
        return reverse("plugins:netbox_config_backup:advanced_settings")


class RetentionPolicy(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    keep_all_days = models.PositiveIntegerField(default=7)
    daily_days = models.PositiveIntegerField(default=30)
    weekly_weeks = models.PositiveIntegerField(default=12)
    monthly_months = models.PositiveIntegerField(default=12)
    minimum_changed_revisions = models.PositiveIntegerField(default=10)
    unchanged_run_days = models.PositiveIntegerField(default=90)
    changed_run_days = models.PositiveIntegerField(default=180)
    failed_run_days = models.PositiveIntegerField(default=180)
    max_runs_per_target = models.PositiveIntegerField(
        default=500,
        validators=(MinValueValidator(1), MaxValueValidator(100000)),
        help_text=(
            "Hard safety limit for completed backup runs retained per device. "
            "Queued and running backups are never removed by this limit."
        ),
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class RemoteRetentionPolicy(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    keep_all_days = models.PositiveIntegerField(default=30)
    daily_days = models.PositiveIntegerField(default=365)
    weekly_weeks = models.PositiveIntegerField(default=104)
    monthly_months = models.PositiveIntegerField(default=60)
    minimum_changed_revisions = models.PositiveIntegerField(default=12)
    max_copies_per_target = models.PositiveIntegerField(
        verbose_name="maximum remote revisions per device",
        default=1000,
        validators=(MinValueValidator(1), MaxValueValidator(100000)),
        help_text=(
            "Maximum number of revisions retained for one backup device on each FTP storage."
        ),
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class BackupPolicy(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)
    schedule_type = models.CharField(
        max_length=16,
        choices=ScheduleTypeChoices.choices,
        default=ScheduleTypeChoices.DAILY,
    )
    interval_minutes = models.PositiveIntegerField(null=True, blank=True)
    time_of_day = models.TimeField(null=True, blank=True)
    timezone_mode = models.CharField(max_length=16, default="site")
    jitter_minutes = models.PositiveIntegerField(default=0)
    connection_timeout = models.PositiveIntegerField(default=15)
    command_timeout = models.PositiveIntegerField(default=60)
    max_retries = models.PositiveSmallIntegerField(default=3)
    retry_backoff_minutes = models.JSONField(default=list, blank=True)
    store_mode = models.CharField(
        max_length=24,
        choices=StoreModeChoices.choices,
        default=StoreModeChoices.CHANGED_ONLY,
    )
    retention_policy = models.ForeignKey(
        RetentionPolicy,
        on_delete=models.PROTECT,
        related_name="backup_policies",
    )

    class Meta:
        ordering = ("name",)
        constraints = (
            models.CheckConstraint(
                condition=(
                    Q(
                        schedule_type=ScheduleTypeChoices.INTERVAL,
                        interval_minutes__isnull=False,
                        time_of_day__isnull=True,
                    )
                    | Q(
                        schedule_type=ScheduleTypeChoices.DAILY,
                        interval_minutes__isnull=True,
                        time_of_day__isnull=False,
                    )
                ),
                name="ncb_policy_schedule_fields",
            ),
        )

    def clean(self) -> None:
        super().clean()
        if self.timezone_mode not in {"site", "plugin_default"}:
            raise ValidationError({"timezone_mode": "Unsupported timezone mode."})
        if not isinstance(self.retry_backoff_minutes, list) or any(
            not isinstance(value, int) or value < 0 for value in self.retry_backoff_minutes
        ):
            raise ValidationError(
                {"retry_backoff_minutes": "Enter a list of non-negative integers."}
            )

    def __str__(self) -> str:
        return self.name


class ConnectionProfile(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    protocol = models.CharField(
        max_length=16,
        choices=ConnectionProtocolChoices.choices,
        default=ConnectionProtocolChoices.AUTOMATIC,
        help_text=("Select SSH or Telnet when the same device family supports both transports."),
    )
    address_preference = models.CharField(
        max_length=24,
        choices=AddressPreferenceChoices.choices,
        default=AddressPreferenceChoices.OOB_FIRST,
    )
    port = models.PositiveIntegerField(
        default=22,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    connect_timeout = models.PositiveIntegerField(default=15)
    command_timeout = models.PositiveIntegerField(default=60)
    keepalive = models.PositiveIntegerField(default=30)
    verify_host_key = models.BooleanField(default=True)
    known_hosts_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class CredentialProfile(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    provider_id = models.CharField(max_length=100)
    secret_reference = models.CharField(max_length=500)
    auth_type = models.CharField(
        max_length=16,
        choices=AuthTypeChoices.choices,
        default=AuthTypeChoices.PASSWORD,
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def stored_username(self) -> str:
        try:
            return self.stored_credential.username
        except StoredCredential.DoesNotExist:
            return ""

    @property
    def has_stored_password(self) -> bool:
        try:
            return bool(self.stored_credential.ciphertext)
        except StoredCredential.DoesNotExist:
            return False


class BackupDestination(JobsMixin, NetBoxModel):
    """A primary local storage or an external completed-revision destination."""

    name = models.CharField(max_length=100, unique=True)
    is_default = models.BooleanField(default=False, editable=False)
    enabled = models.BooleanField(default=True)
    auto_replicate = models.BooleanField(
        default=True,
        help_text="Queue every new revision for this destination.",
    )
    integrity_audit_enabled = models.BooleanField(
        default=False,
        help_text="Automatically verify successful FTP revision copies without changing them.",
    )
    integrity_audit_frequency = models.CharField(
        max_length=8,
        choices=FtpAuditFrequencyChoices.choices,
        default=FtpAuditFrequencyChoices.DAILY,
    )
    integrity_audit_time = models.TimeField(default=datetime.time(4, 0))
    integrity_audit_weekday = models.PositiveSmallIntegerField(
        choices=WeekdayChoices.choices,
        default=WeekdayChoices.MONDAY,
    )
    next_integrity_audit_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
    )
    last_integrity_audit_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_integrity_audit_status = models.CharField(
        max_length=16,
        choices=FtpAuditStatusChoices.choices,
        blank=True,
        editable=False,
    )
    last_integrity_audit_problem_count = models.PositiveIntegerField(
        default=0,
        editable=False,
    )
    protocol = models.CharField(
        max_length=8,
        choices=DestinationProtocolChoices.choices,
        default=DestinationProtocolChoices.SFTP,
    )
    allow_insecure_ftp = models.BooleanField(
        default=False,
        help_text=(
            "Required for FTP. FTP sends credentials and configuration data "
            "without transport encryption."
        ),
    )
    host = models.CharField(max_length=255, blank=True, default="")
    port = models.PositiveIntegerField(
        default=22,
        null=True,
        blank=True,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    base_path = models.CharField(
        max_length=500,
        default="netbox-config-backup",
        blank=True,
        help_text="Remote directory below which immutable revision copies are stored.",
    )
    credential_profile = models.ForeignKey(
        CredentialProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="backup_destinations",
    )
    connect_timeout = models.PositiveIntegerField(
        default=15,
        null=True,
        blank=True,
        validators=(MinValueValidator(1), MaxValueValidator(300)),
    )
    max_retries = models.PositiveSmallIntegerField(
        default=3,
        null=True,
        blank=True,
        validators=(MinValueValidator(0), MaxValueValidator(20)),
    )
    retry_delay_minutes = models.PositiveIntegerField(
        default=15,
        null=True,
        blank=True,
        validators=(MinValueValidator(1), MaxValueValidator(10080)),
    )
    max_artifact_size = models.PositiveBigIntegerField(
        default=1024 * 1024 * 1024,
        null=True,
        blank=True,
        validators=(MinValueValidator(1024), MaxValueValidator(10 * 1024 * 1024 * 1024)),
        help_text="Maximum size of one artifact copied to this destination.",
    )
    local_retention_policy = models.ForeignKey(
        RetentionPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="local_storages",
        verbose_name="local retention profile",
    )
    remote_retention_policy = models.ForeignKey(
        RemoteRetentionPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="remote_storages",
        verbose_name="FTP retention profile",
    )
    enforce_retention_policy = models.BooleanField(
        default=False,
        help_text=(
            "Always use this storage's retention profile instead of a device retention override."
        ),
    )
    host_key_type = models.CharField(max_length=64, blank=True, editable=False)
    host_key_public = models.TextField(blank=True, editable=False)
    host_key_fingerprint_sha256 = models.CharField(max_length=100, blank=True, editable=False)
    host_key_fingerprint_md5 = models.CharField(max_length=64, blank=True, editable=False)
    host_key_approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    host_key_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="approved_config_backup_destinations",
    )
    last_tested_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_success_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error_code = models.CharField(max_length=64, blank=True, editable=False)
    last_error_message = models.CharField(max_length=500, blank=True, editable=False)

    class Meta:
        ordering = ("name",)
        verbose_name = "storage"
        verbose_name_plural = "storages"
        constraints = (
            models.UniqueConstraint(
                fields=("protocol",),
                condition=Q(protocol=DestinationProtocolChoices.LOCAL),
                name="ncb_destination_one_local",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        protocol=DestinationProtocolChoices.LOCAL,
                        is_default=True,
                        remote_retention_policy__isnull=True,
                    )
                    | (
                        ~Q(protocol=DestinationProtocolChoices.LOCAL)
                        & Q(is_default=False, local_retention_policy__isnull=True)
                    )
                ),
                name="ncb_destination_typed_retention",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(protocol=DestinationProtocolChoices.LOCAL)
                    | Q(
                        enabled=True,
                        auto_replicate=False,
                        integrity_audit_enabled=False,
                        allow_insecure_ftp=False,
                        base_path="",
                        connect_timeout__isnull=True,
                        credential_profile__isnull=True,
                        host="",
                        max_artifact_size__isnull=True,
                        max_retries__isnull=True,
                        port__isnull=True,
                        retry_delay_minutes__isnull=True,
                    )
                ),
                name="ncb_destination_local_invariants",
            ),
            models.CheckConstraint(
                condition=(
                    Q(protocol=DestinationProtocolChoices.LOCAL)
                    | (
                        Q(
                            credential_profile__isnull=False,
                            port__isnull=False,
                            connect_timeout__isnull=False,
                            max_retries__isnull=False,
                            retry_delay_minutes__isnull=False,
                            max_artifact_size__isnull=False,
                        )
                        & ~Q(host="")
                        & ~Q(base_path="")
                    )
                ),
                name="ncb_destination_remote_transport",
            ),
            models.CheckConstraint(
                condition=(
                    Q(enforce_retention_policy=False)
                    | Q(
                        protocol=DestinationProtocolChoices.LOCAL,
                        local_retention_policy__isnull=False,
                    )
                    | Q(
                        protocol__in=(
                            DestinationProtocolChoices.FTP,
                            DestinationProtocolChoices.SFTP,
                        ),
                        remote_retention_policy__isnull=False,
                    )
                ),
                name="ncb_destination_enforced_policy",
            ),
        )

    def clean(self) -> None:
        super().clean()
        if self.pk:
            original_identity = (
                type(self).objects.filter(pk=self.pk).values("protocol", "is_default").first()
            )
            if (
                original_identity
                and original_identity["protocol"] == DestinationProtocolChoices.LOCAL
                and (self.protocol != DestinationProtocolChoices.LOCAL or not self.is_default)
            ):
                raise ValidationError(
                    {"protocol": "The default Local storage type cannot be changed."}
                )
            if (
                original_identity
                and original_identity["protocol"] != DestinationProtocolChoices.LOCAL
                and self.protocol == DestinationProtocolChoices.LOCAL
            ):
                raise ValidationError(
                    {"protocol": "The system default Local storage already exists."}
                )

        if self.protocol == DestinationProtocolChoices.LOCAL:
            local_errors = {}
            if not self.is_default:
                local_errors["is_default"] = "The Local storage must be the system default."
            if not self.enabled:
                local_errors["enabled"] = "The default Local storage cannot be disabled."
            if self.auto_replicate:
                local_errors["auto_replicate"] = (
                    "The primary Local storage is written directly, not replicated."
                )
            if self.integrity_audit_enabled:
                local_errors["integrity_audit_enabled"] = (
                    "FTP integrity audits do not apply to the Local storage."
                )
            if self.credential_profile_id:
                local_errors["credential_profile"] = (
                    "The Local storage does not use remote credentials."
                )
            if self.host:
                local_errors["host"] = "The Local storage does not use a remote host."
            if self.port is not None:
                local_errors["port"] = "The Local storage does not use a remote port."
            if self.base_path:
                local_errors["base_path"] = "The Local storage path is managed by the deployment."
            for field_name in (
                "connect_timeout",
                "max_retries",
                "retry_delay_minutes",
                "max_artifact_size",
            ):
                if getattr(self, field_name) is not None:
                    local_errors[field_name] = (
                        "This FTP transport setting does not apply to the Local storage."
                    )
            if self.remote_retention_policy_id:
                local_errors["remote_retention_policy"] = (
                    "Select a local retention profile for the Local storage."
                )
            if self.enforce_retention_policy and not self.local_retention_policy_id:
                local_errors["local_retention_policy"] = (
                    "Select a local retention profile before enforcing it."
                )
            if local_errors:
                raise ValidationError(local_errors)
            return

        remote_errors = {}
        if self.is_default:
            remote_errors["is_default"] = "Only the Local storage can be the system default."
        if self.local_retention_policy_id:
            remote_errors["local_retention_policy"] = (
                "Select an FTP retention profile for a remote storage."
            )
        if self.enforce_retention_policy and not self.remote_retention_policy_id:
            remote_errors["remote_retention_policy"] = (
                "Select an FTP retention profile before enforcing it."
            )
        for field_name in (
            "port",
            "credential_profile",
            "connect_timeout",
            "max_retries",
            "retry_delay_minutes",
            "max_artifact_size",
        ):
            value = (
                self.credential_profile_id
                if field_name == "credential_profile"
                else getattr(self, field_name)
            )
            if value is None:
                remote_errors[field_name] = "This remote-storage field is required."
        if remote_errors:
            raise ValidationError(remote_errors)

        if (
            self.pk
            and self.replicas.filter(
                remote_deleted_at__isnull=True,
            )
            .exclude(remote_path="")
            .exists()
        ):
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(
                    "protocol",
                    "host",
                    "port",
                    "base_path",
                    "credential_profile_id",
                )
                .get()
            )
            endpoint_errors = {
                ("credential_profile" if field_name == "credential_profile_id" else field_name): (
                    "This endpoint field cannot be changed while FTP copies exist. "
                    "Create a new destination for a different FTP server or path."
                )
                for field_name in (
                    "protocol",
                    "host",
                    "port",
                    "base_path",
                    "credential_profile_id",
                )
                if getattr(self, field_name) != original[field_name]
            }
            if endpoint_errors:
                raise ValidationError(endpoint_errors)
        if self.protocol == DestinationProtocolChoices.FTP and not self.allow_insecure_ftp:
            raise ValidationError(
                {"allow_insecure_ftp": ("Confirm that this destination may use unencrypted FTP.")}
            )
        if (
            self.protocol == DestinationProtocolChoices.FTP
            and self.credential_profile_id
            and self.credential_profile.auth_type != AuthTypeChoices.PASSWORD
        ):
            raise ValidationError(
                {"credential_profile": "FTP destinations require password credentials."}
            )
        if self.integrity_audit_enabled and self.protocol != DestinationProtocolChoices.FTP:
            raise ValidationError(
                {"integrity_audit_enabled": "Automatic integrity audits require FTP."}
            )
        if (
            not self.host
            or any(character.isspace() for character in self.host)
            or any(value in self.host for value in ("/", "\\", "[", "]", "\x00"))
        ):
            raise ValidationError({"host": "Enter a valid server host name or address."})
        path = self.base_path.strip()
        parts = path.replace("\\", "/").split("/")
        if (
            not path
            or path == "."
            or "\\" in path
            or "\x00" in path
            or any(ord(character) < 32 for character in path)
            or any(part == ".." for part in parts)
        ):
            raise ValidationError(
                {"base_path": "Use a safe POSIX path without backslashes or '..' segments."}
            )
        key_values = (
            self.host_key_type,
            self.host_key_public,
            self.host_key_fingerprint_sha256,
        )
        if any(key_values) and not all(key_values):
            raise ValidationError("The approved SFTP host key is incomplete.")

    @property
    def known_hosts_line(self) -> str:
        if self.protocol != DestinationProtocolChoices.SFTP or not self.host_key_public:
            return ""
        host = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        return f"{host} {self.host_key_type} {self.host_key_public}"

    @property
    def host_key_is_trusted(self) -> bool:
        return bool(
            self.protocol == DestinationProtocolChoices.SFTP
            and self.host_key_public
            and self.host_key_approved_at
        )

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_config_backup:backupdestination", args=(self.pk,))


@receiver(pre_delete, sender=BackupDestination)
def protect_default_local_storage(sender, instance, using, **kwargs) -> None:
    """Prevent deletion through both Model.delete() and QuerySet.delete()."""

    if instance.protocol == DestinationProtocolChoices.LOCAL or instance.is_default:
        raise ProtectedError(
            "The system default Local storage cannot be deleted.",
            {instance},
        )


class SftpReceiverProfile(NetBoxModel):
    """A plugin-managed upload endpoint used by devices which push native backups.

    The historical model name is retained for migration and API compatibility. New
    profiles may use secure SFTP or explicitly opt in to legacy FTP for old ALFOplus
    firmware which has no SFTP backup client.
    """

    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)
    protocol = models.CharField(
        max_length=8,
        choices=ReceiverProtocolChoices.choices,
        default=ReceiverProtocolChoices.SFTP,
    )
    mode = models.CharField(
        max_length=24,
        choices=ReceiverModeChoices.choices,
        default=ReceiverModeChoices.DIRECT,
    )
    credential_profile = models.ForeignKey(
        CredentialProfile,
        on_delete=models.PROTECT,
        related_name="sftp_receiver_profiles",
    )
    listen_host = models.CharField(max_length=255, default="0.0.0.0")
    listen_port = models.PositiveIntegerField(
        default=2022,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    advertised_host = models.CharField(
        max_length=255,
        blank=True,
        help_text="Address which devices use in direct mode.",
    )
    advertised_port = models.PositiveIntegerField(
        default=2022,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    bridge_host = models.CharField(
        max_length=255,
        default="config-backup-receiver",
        help_text="Receiver address reachable from the backup worker.",
    )
    bridge_port = models.PositiveIntegerField(
        default=2022,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    remote_bind_host = models.GenericIPAddressField(default="127.0.0.1")
    remote_bind_port = models.PositiveIntegerField(
        default=2222,
        validators=(MinValueValidator(1024), MaxValueValidator(65535)),
    )
    upload_directory = models.CharField(max_length=100, default="incoming")
    export_timeout = models.PositiveIntegerField(
        default=180,
        validators=(MinValueValidator(10), MaxValueValidator(3600)),
    )
    max_upload_size = models.PositiveBigIntegerField(
        default=100 * 1024 * 1024,
        validators=(MinValueValidator(1024), MaxValueValidator(1024 * 1024 * 1024)),
    )
    passive_port_start = models.PositiveIntegerField(
        default=30000,
        validators=(MinValueValidator(1024), MaxValueValidator(65535)),
        help_text="First passive data port used only by the legacy FTP receiver.",
    )
    passive_port_end = models.PositiveIntegerField(
        default=30009,
        validators=(MinValueValidator(1024), MaxValueValidator(65535)),
        help_text="Last passive data port used only by the legacy FTP receiver.",
    )

    class Meta:
        ordering = ("name",)

    def clean(self) -> None:
        super().clean()
        if self.protocol == ReceiverProtocolChoices.FTP:
            if self.mode != ReceiverModeChoices.DIRECT:
                raise ValidationError(
                    {"mode": "The legacy FTP receiver supports direct mode only."}
                )
            if self.passive_port_start > self.passive_port_end:
                raise ValidationError(
                    {"passive_port_end": "The last passive port must not be lower than the first."}
                )
            if self.passive_port_end - self.passive_port_start > 99:
                raise ValidationError(
                    {"passive_port_end": "Configure at most 100 passive FTP ports."}
                )
        if self.mode == ReceiverModeChoices.DIRECT and not self.advertised_host:
            raise ValidationError({"advertised_host": "This address is required in direct mode."})
        if not self.bridge_host:
            raise ValidationError({"bridge_host": "The worker bridge address is required."})
        if (
            not self.upload_directory
            or self.upload_directory in {".", ".."}
            or "/" in self.upload_directory
            or "\\" in self.upload_directory
            or "\x00" in self.upload_directory
        ):
            raise ValidationError(
                {"upload_directory": "Use one plain directory name without slashes."}
            )

    def __str__(self) -> str:
        return self.name


class StoredCredential(models.Model):
    """Encrypted password material. This private model has no direct UI or API."""

    _netbox_private = True

    profile = models.OneToOneField(
        CredentialProfile,
        on_delete=models.CASCADE,
        related_name="stored_credential",
    )
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    username = models.CharField(max_length=255)
    ciphertext = models.BinaryField(editable=False)
    nonce = models.BinaryField(editable=False)
    key_version = models.CharField(max_length=50, editable=False)
    rotated_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        default_permissions = ("add", "change", "delete", "view")

    def __str__(self) -> str:
        return f"Stored credential for {self.profile}"


class PlatformMapping(NetBoxModel):
    platform = models.OneToOneField(
        "dcim.Platform",
        on_delete=models.CASCADE,
        related_name="config_backup_mapping",
    )
    driver_id = models.CharField(max_length=100)
    connection_profile = models.ForeignKey(
        ConnectionProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="platform_mappings",
    )
    credential_profile = models.ForeignKey(
        CredentialProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="platform_mappings",
    )
    receiver_profile = models.ForeignKey(
        SftpReceiverProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="platform_mappings",
    )
    enabled = models.BooleanField(default=True)
    driver_options = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("platform__name",)

    def clean(self) -> None:
        super().clean()
        from .drivers import driver_registry

        if not driver_registry.contains(self.driver_id):
            raise ValidationError({"driver_id": "Unknown registered backup driver."})
        if not isinstance(self.driver_options, dict):
            raise ValidationError({"driver_options": "Driver options must be an object."})

    def __str__(self) -> str:
        return f"{self.platform}: {self.driver_id}"


class BackupTarget(JobsMixin, NetBoxModel):
    device = models.OneToOneField(
        "dcim.Device",
        on_delete=models.PROTECT,
        related_name="config_backup_target",
    )
    enabled = models.BooleanField(default=True)
    policy_override = models.ForeignKey(
        BackupPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
    )
    retention_override = models.ForeignKey(
        RetentionPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
        verbose_name="local retention profile",
    )
    remote_retention_policy = models.ForeignKey(
        RemoteRetentionPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
        verbose_name="FTP retention profile",
        help_text=(
            "Leave blank to use each FTP storage profile. Copies are kept indefinitely "
            "only on a storage which also has no profile."
        ),
    )
    credential_override = models.ForeignKey(
        CredentialProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
    )
    connection_override = models.ForeignKey(
        ConnectionProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
    )
    receiver_override = models.ForeignKey(
        SftpReceiverProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="target_overrides",
    )
    driver_override = models.CharField(max_length=100, blank=True)
    driver_options_override = models.JSONField(default=dict, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_change_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=TargetStatusChoices.choices,
        default=TargetStatusChoices.NEVER,
        db_index=True,
    )
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_revision = models.ForeignKey(
        "ConfigRevision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_targets",
    )

    class Meta:
        ordering = ("device__name",)

    def clean(self) -> None:
        super().clean()
        if self.driver_override:
            from .drivers import driver_registry

            if not driver_registry.contains(self.driver_override):
                raise ValidationError({"driver_override": "Unknown registered backup driver."})
        if not isinstance(self.driver_options_override, dict):
            raise ValidationError({"driver_options_override": "Driver options must be an object."})

    def __str__(self) -> str:
        return str(self.device)

    def get_absolute_url(self):
        return reverse("plugins:netbox_config_backup:backuptarget", args=(self.pk,))

    def get_status_color(self) -> str:
        return {
            TargetStatusChoices.NEVER: "secondary",
            TargetStatusChoices.HEALTHY: "success",
            TargetStatusChoices.FAILED: "danger",
            TargetStatusChoices.STALE: "warning",
            TargetStatusChoices.DISABLED: "secondary",
        }.get(self.status, "secondary")


class SSHHostKey(NetBoxModel):
    """SSH server identity discovered for one backup target."""

    target = models.ForeignKey(
        BackupTarget,
        on_delete=models.CASCADE,
        related_name="ssh_host_keys",
    )
    address = models.CharField(max_length=255)
    port = models.PositiveIntegerField(
        default=22,
        validators=(MinValueValidator(1), MaxValueValidator(65535)),
    )
    key_type = models.CharField(max_length=64)
    public_key = models.TextField()
    fingerprint_sha256 = models.CharField(max_length=100)
    fingerprint_md5 = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16,
        choices=SSHHostKeyStatusChoices.choices,
        default=SSHHostKeyStatusChoices.PENDING,
        db_index=True,
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_config_backup_host_keys",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-last_seen_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("target", "address", "port", "key_type", "fingerprint_sha256"),
                name="ncb_sshkey_target_identity",
            ),
        )

    def clean(self) -> None:
        super().clean()
        if not self.address or any(value in self.address for value in ("\r", "\n", "\x00")):
            raise ValidationError({"address": "Enter a valid management address."})
        if not self.key_type or any(value in self.key_type for value in (" ", "\r", "\n", "\x00")):
            raise ValidationError({"key_type": "The SSH key type is invalid."})
        if not self.public_key or any(
            value in self.public_key for value in (" ", "\r", "\n", "\x00")
        ):
            raise ValidationError({"public_key": "The SSH public key is invalid."})

    @property
    def known_hosts_line(self) -> str:
        host = self.address if self.port == 22 else f"[{self.address}]:{self.port}"
        return f"{host} {self.key_type} {self.public_key}"

    def __str__(self) -> str:
        return f"{self.address}:{self.port} {self.fingerprint_sha256}"

    def get_absolute_url(self):
        return self.target.get_absolute_url()


class ConfigRevision(NetBoxModel):
    target = models.ForeignKey(
        BackupTarget,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    revision_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    normalized_hash = models.CharField(max_length=64, db_index=True)
    normalizer_version = models.CharField(max_length=50)
    driver_id = models.CharField(max_length=100)
    content_changed = models.BooleanField(default=True)
    protected = models.BooleanField(default=False, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    comments = models.TextField(blank=True)
    previous_revision = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_revisions",
    )

    class Meta:
        ordering = ("-created",)
        indexes = (models.Index(fields=("target", "-created"), name="ncb_revision_target_created"),)

    def __str__(self) -> str:
        return f"{self.target} @ {self.revision_uuid}"


class ConfigArtifact(NetBoxModel):
    _netbox_private = True

    revision = models.ForeignKey(
        ConfigRevision,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    artifact_type = models.CharField(max_length=100)
    format = models.CharField(max_length=50, default="text")
    storage_key = models.CharField(max_length=1000, unique=True)
    size = models.PositiveBigIntegerField()
    raw_hash = models.CharField(max_length=64)
    normalized_hash = models.CharField(max_length=64)
    is_primary = models.BooleanField(default=False)
    local_available = models.BooleanField(default=True)
    local_deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("artifact_type",)
        constraints = (
            models.UniqueConstraint(
                fields=("revision", "artifact_type"),
                name="ncb_artifact_revision_type",
            ),
            models.UniqueConstraint(
                fields=("revision",),
                condition=Q(is_primary=True),
                name="ncb_artifact_one_primary",
            ),
        )

    def __str__(self) -> str:
        return f"{self.revision}: {self.artifact_type}"


class RevisionReplica(NetBoxModel):
    """Audit state for one revision replicated to one external destination."""

    revision = models.ForeignKey(
        ConfigRevision,
        on_delete=models.CASCADE,
        related_name="replicas",
    )
    destination = models.ForeignKey(
        BackupDestination,
        on_delete=models.PROTECT,
        related_name="replicas",
    )
    status = models.CharField(
        max_length=16,
        choices=ReplicaStatusChoices.choices,
        default=ReplicaStatusChoices.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    bytes_transferred = models.PositiveBigIntegerField(default=0)
    remote_path = models.CharField(max_length=1000, blank=True)
    remote_available = models.BooleanField(default=False)
    remote_deleted_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, db_index=True)
    error_message = models.CharField(max_length=500, blank=True)
    job_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ("-created",)
        constraints = (
            models.UniqueConstraint(
                fields=("revision", "destination"),
                name="ncb_replica_revision_destination",
            ),
        )
        indexes = (
            models.Index(
                fields=("destination", "status", "next_retry_at"),
                name="ncb_replica_retry",
            ),
        )

    def __str__(self) -> str:
        return f"{self.revision} -> {self.destination}"

    def get_absolute_url(self):
        return self.destination.get_absolute_url()

    def get_status_color(self) -> str:
        return {
            ReplicaStatusChoices.PENDING: "secondary",
            ReplicaStatusChoices.QUEUED: "info",
            ReplicaStatusChoices.RUNNING: "info",
            ReplicaStatusChoices.SUCCESS: "success",
            ReplicaStatusChoices.FAILED: "danger",
        }.get(self.status, "secondary")


class BackupRun(NetBoxModel):
    target = models.ForeignKey(
        BackupTarget,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    source = models.CharField(
        max_length=16,
        choices=RunSourceChoices.choices,
        default=RunSourceChoices.MANUAL,
    )
    scheduled_for = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=128, null=True, blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=RunStatusChoices.choices,
        default=RunStatusChoices.QUEUED,
        db_index=True,
    )
    attempt_number = models.PositiveSmallIntegerField(default=1)
    error_code = models.CharField(max_length=64, blank=True, db_index=True)
    error_message = models.CharField(max_length=1000, blank=True)
    revision = models.ForeignKey(
        ConfigRevision,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    changed = models.BooleanField(default=False)
    raw_changed = models.BooleanField(default=False)
    job_id = models.UUIDField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="config_backup_runs",
    )

    class Meta:
        ordering = ("-queued_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("target", "dedupe_key"),
                condition=Q(dedupe_key__isnull=False),
                name="ncb_run_target_dedupe",
            ),
            models.UniqueConstraint(
                fields=("target",),
                condition=Q(status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING)),
                name="ncb_run_one_active_target",
            ),
        )
        indexes = (models.Index(fields=("target", "-queued_at"), name="ncb_run_target_queued"),)

    def __str__(self) -> str:
        return f"{self.target} - {self.get_status_display()}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_config_backup:backuprun", args=(self.pk,))

    def get_status_color(self) -> str:
        return {
            RunStatusChoices.QUEUED: "secondary",
            RunStatusChoices.RUNNING: "info",
            RunStatusChoices.SUCCESS_UNCHANGED: "success",
            RunStatusChoices.SUCCESS_CHANGED: "success",
            RunStatusChoices.PARTIAL: "warning",
            RunStatusChoices.FAILED: "danger",
            RunStatusChoices.ERRORED: "danger",
            RunStatusChoices.SKIPPED: "secondary",
        }.get(self.status, "secondary")

    @property
    def is_stuck(self) -> bool:
        from .services.health import is_run_stuck

        return is_run_stuck(
            self,
            now=timezone.now(),
            timeout_minutes=settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_run_minutes"],
        )
