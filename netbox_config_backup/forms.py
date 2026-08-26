import uuid
from typing import ClassVar

from dcim.models import Device, Platform, Site
from django import forms
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DateTimePicker

from .choices import (
    ConnectionProtocolChoices,
    DestinationProtocolChoices,
    RunSourceChoices,
    RunStatusChoices,
    TargetStatusChoices,
)
from .credentials.base import SecretProviderError
from .credentials.encrypted_database import (
    DatabaseCredentialCipher,
    EncryptedDatabaseSecretProvider,
    MasterKeyConfigurationError,
)
from .drivers import driver_registry
from .models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    OperationalSettings,
    PlatformMapping,
    RemoteRetentionPolicy,
    RetentionPolicy,
    SftpReceiverProfile,
    StoredCredential,
)
from .services.ftp_audit_scheduling import calculate_destination_next_ftp_audit
from .services.reporting_period import REPORTING_PERIOD_CHOICES
from .services.scheduling import apply_target_schedule

REPORTING_PERIOD_FORM_CHOICES = (("", "Any time"), *REPORTING_PERIOD_CHOICES)


class BackupTargetFilterForm(NetBoxModelFilterSetForm):
    model = BackupTarget
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet(
            "status",
            "enabled",
            "device_id",
            "site_id",
            "policy_override_id",
            "retention_override_id",
            "remote_retention_policy_id",
            "driver_override",
            name="Health and device",
        ),
    )
    status = forms.MultipleChoiceField(
        choices=TargetStatusChoices.choices,
        required=False,
    )
    enabled = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    policy_override_id = DynamicModelMultipleChoiceField(
        queryset=BackupPolicy.objects.all(),
        required=False,
        label="Backup policy",
    )
    retention_override_id = DynamicModelMultipleChoiceField(
        queryset=RetentionPolicy.objects.all(),
        required=False,
        label="Local retention profile",
    )
    remote_retention_policy_id = DynamicModelMultipleChoiceField(
        queryset=RemoteRetentionPolicy.objects.all(),
        required=False,
        label="FTP retention profile",
    )
    driver_override = forms.CharField(required=False, label="Driver override")


class BackupRunFilterForm(NetBoxModelFilterSetForm):
    model = BackupRun
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("status", "source", "failed", "stuck", "error_code", name="Result"),
        FieldSet("target_id", "device_id", "site_id", name="Target"),
        FieldSet("period", "date_from", "date_to", name="Period"),
        FieldSet("queued_at_after", "queued_at_before", name="Exact queued time"),
    )
    status = forms.MultipleChoiceField(
        choices=RunStatusChoices.choices,
        required=False,
    )
    source = forms.MultipleChoiceField(
        choices=RunSourceChoices.choices,
        required=False,
    )
    failed = forms.NullBooleanField(
        required=False,
        label="Failure",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    stuck = forms.NullBooleanField(
        required=False,
        label="Stuck",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    error_code = forms.CharField(required=False, label="Error code contains")
    period = forms.ChoiceField(
        choices=REPORTING_PERIOD_FORM_CHOICES,
        required=False,
        label="Period",
    )
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="To date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_id = DynamicModelMultipleChoiceField(
        queryset=BackupTarget.objects.all(),
        required=False,
        label="Backup target",
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    queued_at_after = forms.DateTimeField(
        required=False,
        label="Queued after",
        widget=DateTimePicker(),
    )
    queued_at_before = forms.DateTimeField(
        required=False,
        label="Queued before",
        widget=DateTimePicker(),
    )


class ConfigRevisionFilterForm(NetBoxModelFilterSetForm):
    model = ConfigRevision
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("period", "date_from", "date_to", name="Period"),
        FieldSet("target_id", "device_id", "site_id", name="Target"),
        FieldSet("driver_id", "content_changed", "protected", name="Revision"),
    )
    period = forms.ChoiceField(
        choices=REPORTING_PERIOD_FORM_CHOICES,
        required=False,
        label="Period",
    )
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="To date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_id = DynamicModelMultipleChoiceField(
        queryset=BackupTarget.objects.all(),
        required=False,
        label="Backup target",
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    driver_id = forms.CharField(required=False, label="Driver")
    content_changed = forms.NullBooleanField(
        required=False,
        label="Configuration changed",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    protected = forms.NullBooleanField(
        required=False,
        label="Protected",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )


def _driver_choices(
    *,
    blank: bool = False,
    include_ids: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    choices = [
        (driver.driver_id, driver.display_name)
        for driver in driver_registry.classes()
        if driver.user_selectable or driver.driver_id in include_ids
    ]
    return [("", "---------"), *choices] if blank else choices


class OperationalSettingsForm(NetBoxModelForm):
    confirm_enable = forms.BooleanField(
        required=False,
        label="I understand that automatic retention can permanently delete expired history",
        help_text="Required only when automatic retention is being enabled.",
    )
    confirm_remote_enable = forms.BooleanField(
        required=False,
        label="I understand that FTP retention permanently deletes expired remote copies",
        help_text="Required only when automatic FTP retention is being enabled.",
    )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        enabling = bool(cleaned.get("retention_scheduler_enabled")) and not bool(
            self.instance.retention_scheduler_enabled
        )
        if enabling and not cleaned.get("confirm_enable"):
            self.add_error(
                "confirm_enable",
                "Confirm the automatic deletion warning before enabling retention.",
            )
        enabling_remote = bool(cleaned.get("remote_retention_scheduler_enabled")) and not bool(
            self.instance.remote_retention_scheduler_enabled
        )
        if enabling_remote and not cleaned.get("confirm_remote_enable"):
            self.add_error(
                "confirm_remote_enable",
                "Confirm the permanent FTP deletion warning before enabling remote retention.",
            )
        return self.cleaned_data

    class Meta:
        model = OperationalSettings
        fields = (
            "retention_scheduler_enabled",
            "remote_retention_scheduler_enabled",
            "retention_scheduler_batch_size",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "retention_scheduler_batch_size": forms.HiddenInput(),
        }


class NotificationSettingsForm(NetBoxModelForm):
    events_enabled = forms.BooleanField(
        required=False,
        label="Emit backup events",
        help_text="Send failure, recovery, stale, and stuck events to NetBox Event Rules.",
    )
    notify_on_every_failure = forms.BooleanField(
        required=False,
        label="Notify on every failed attempt",
        help_text="When disabled, repeated failures stay quiet until the device recovers.",
    )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if cleaned.get("notify_on_every_failure") and not cleaned.get("events_enabled"):
            self.add_error(
                "notify_on_every_failure",
                "Enable backup events before enabling notifications for every failure.",
            )
        return cleaned

    class Meta:
        model = OperationalSettings
        fields = (
            "events_enabled",
            "notify_on_every_failure",
        )


class QuickSetupForm(forms.Form):
    advanced_field_names = (
        "driver_id",
        "receiver_profile",
        "sync_receiver_credentials",
        "restore_point",
        "connection_profile",
        "protocol",
        "port",
        "verify_host_key",
        "username",
        "password",
        "password_confirm",
    )

    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        help_text="Select a NetBox device which does not already have a backup target.",
    )
    driver_id = forms.ChoiceField(
        choices=(),
        required=False,
        label="Backup driver",
        help_text="Automatic uses the enabled platform mapping. Select a driver if none exists.",
    )
    receiver_profile = DynamicModelChoiceField(
        queryset=SftpReceiverProfile.objects.filter(enabled=True),
        required=False,
        label="Backup receiver",
        help_text="Automatic uses the receiver from the platform mapping.",
    )
    allow_device_export = forms.BooleanField(
        required=False,
        label="Allow the device to create and send a backup file",
        help_text=(
            "Required for native backup drivers such as Ceragon IP-50. The plugin may replace "
            "the selected backup workspace, but it never imports a configuration, activates "
            "changes, or reboots the device."
        ),
    )
    sync_receiver_credentials = forms.BooleanField(
        required=False,
        label="Configure the legacy FTP login on ALFOplus",
        help_text=(
            "Optional. This writes the selected receiver username and password into the "
            "device file-transfer settings. Leave disabled when the radio already uses "
            "matching FTP credentials."
        ),
    )
    restore_point = forms.ChoiceField(
        choices=(
            ("restore-point-1", "Restore point 1"),
            ("restore-point-2", "Restore point 2"),
            ("restore-point-3", "Restore point 3"),
        ),
        initial="restore-point-1",
        required=False,
    )
    connection_profile = DynamicModelChoiceField(
        queryset=ConnectionProfile.objects.all(),
        required=False,
        label="Connection profile",
        help_text=(
            "Automatic uses the platform mapping. If none exists, the device address and values "
            "below are used."
        ),
    )
    protocol = forms.ChoiceField(
        choices=ConnectionProtocolChoices.choices,
        initial=ConnectionProtocolChoices.AUTOMATIC,
        label="Protocol",
        help_text="Automatic uses SSH on port 22 and Telnet on port 23.",
    )
    port = forms.IntegerField(
        min_value=1,
        max_value=65535,
        initial=22,
        required=False,
    )
    verify_host_key = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Recommended. The host key must exist in the configured known_hosts file.",
    )
    credential_profile = DynamicModelChoiceField(
        queryset=CredentialProfile.objects.exclude(provider_id="vault_kv2"),
        required=False,
        label="Credential profile",
        help_text=(
            "Automatic uses the platform mapping. Select a profile to override it, or enter a "
            "dedicated login under Advanced settings."
        ),
    )
    username = forms.CharField(max_length=255, required=False)
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
        help_text="Stored encrypted and never displayed again.",
    )
    password_confirm = forms.CharField(
        required=False,
        strip=False,
        label="Confirm password",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
    )
    schedule = forms.ChoiceField(
        choices=(
            ("6h", "Every 6 hours"),
            ("12h", "Every 12 hours"),
            ("daily", "Daily at 02:00"),
        ),
        initial="daily",
    )
    retention_days = forms.TypedChoiceField(
        choices=((30, "30 days"), (90, "90 days"), (365, "365 days")),
        coerce=int,
        initial=90,
        label="Local history",
    )
    remote_retention_days = forms.TypedChoiceField(
        choices=(
            ("", "Use FTP storage profile"),
            (90, "90 days"),
            (365, "365 days"),
            (730, "730 days"),
        ),
        coerce=int,
        empty_value=None,
        required=False,
        initial="",
        label="Remote FTP history",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = Device.objects.filter(config_backup_target__isnull=True)
        self.fields["device"].widget.attrs["data-url"] = reverse(
            "plugins-api:netbox_config_backup-api:available-device-list"
        )
        self.fields["driver_id"].choices = (
            ("", "Automatic from platform mapping"),
            *_driver_choices(),
        )
        self.fields["receiver_profile"].empty_label = "Automatic from platform mapping"
        self.fields[
            "connection_profile"
        ].empty_label = "Automatic from platform mapping or device address"
        self.fields["credential_profile"].empty_label = "Automatic from platform mapping"

    @property
    def advanced_has_errors(self) -> bool:
        return any(name in self.errors for name in self.advanced_field_names)

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        device = cleaned.get("device")
        driver_id = cleaned.get("driver_id")
        mapping = None

        if device and BackupTarget.objects.filter(device=device).exists():
            self.add_error("device", "This device already has a backup target.")

        if device and device.platform_id:
            mapping = PlatformMapping.objects.filter(
                platform_id=device.platform_id,
                enabled=True,
            ).first()

        if device and not driver_id:
            if mapping and driver_registry.contains(mapping.driver_id):
                cleaned["driver_id"] = mapping.driver_id
                cleaned["restore_point"] = mapping.driver_options.get(
                    "restore_point", cleaned.get("restore_point") or "restore-point-1"
                )
            else:
                self.add_error(
                    "driver_id",
                    "No enabled platform mapping was found. Select a backup driver.",
                )

        if mapping:
            if not cleaned.get("receiver_profile") and mapping.receiver_profile_id:
                cleaned["receiver_profile"] = mapping.receiver_profile
            if not cleaned.get("connection_profile") and mapping.connection_profile_id:
                cleaned["connection_profile"] = mapping.connection_profile
            has_new_login = bool(cleaned.get("username") or cleaned.get("password"))
            if (
                not cleaned.get("credential_profile")
                and not has_new_login
                and mapping.credential_profile_id
            ):
                cleaned["credential_profile"] = mapping.credential_profile

        effective_driver = cleaned.get("driver_id")
        selected_connection = cleaned.get("connection_profile")
        protocol = selected_connection.protocol if selected_connection else cleaned.get("protocol")
        if effective_driver == "siae_smos_cli":
            if protocol == ConnectionProtocolChoices.SSH:
                self.add_error(
                    "protocol",
                    "The selected SIAE Telnet driver cannot use an SSH connection profile.",
                )
            cleaned["protocol"] = ConnectionProtocolChoices.TELNET
        elif effective_driver == "siae_smos_ssh":
            if protocol == ConnectionProtocolChoices.TELNET:
                self.add_error(
                    "protocol",
                    "The selected SIAE SSH driver cannot use a Telnet connection profile.",
                )
            cleaned["protocol"] = ConnectionProtocolChoices.SSH
        if effective_driver == "ceragon_ip50" and not cleaned.get("receiver_profile"):
            enabled_receivers = SftpReceiverProfile.objects.filter(enabled=True)
            if enabled_receivers.count() == 1:
                cleaned["receiver_profile"] = enabled_receivers.first()
        if effective_driver == "ceragon_ip50" and not cleaned.get("receiver_profile"):
            self.add_error(
                "receiver_profile",
                "No receiver could be selected automatically. Open Advanced settings and choose "
                "an enabled SFTP receiver.",
            )
        if effective_driver == "ceragon_ip50" and not cleaned.get("allow_device_export"):
            self.add_error(
                "allow_device_export",
                "Confirm the device-side backup export before saving.",
            )
        selected_receiver = cleaned.get("receiver_profile")
        if (
            effective_driver == "siae_smos_auto"
            and selected_receiver
            and selected_receiver.protocol == "ftp"
            and not cleaned.get("allow_device_export")
        ):
            self.add_error(
                "allow_device_export",
                "Confirm the legacy ALFOplus native export before saving.",
            )

        if not cleaned.get("connection_profile") and not cleaned.get("port"):
            self.add_error("port", "Enter a port or select a connection profile.")

        credential_profile = cleaned.get("credential_profile")
        password = cleaned.get("password") or ""
        username = cleaned.get("username") or ""
        if credential_profile:
            cleaned["username"] = ""
            cleaned["password"] = ""
            cleaned["password_confirm"] = ""
        else:
            if not username:
                self.add_error("username", "Enter a username or select a credential profile.")
            if not password:
                self.add_error("password", "Enter a password or select a credential profile.")
            if password != (cleaned.get("password_confirm") or ""):
                self.add_error("password_confirm", "Passwords do not match.")

        if password and not credential_profile:
            try:
                DatabaseCredentialCipher().active_key()
            except MasterKeyConfigurationError as exc:
                self.add_error("password", str(exc))
        return cleaned


class RetentionPolicyForm(NetBoxModelForm):
    class Meta:
        model = RetentionPolicy
        fields = (
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "minimum_changed_revisions",
            "unchanged_run_days",
            "changed_run_days",
            "failed_run_days",
            "max_runs_per_target",
            "tags",
        )


class RemoteRetentionPolicyForm(NetBoxModelForm):
    class Meta:
        model = RemoteRetentionPolicy
        fields = (
            "name",
            "keep_all_days",
            "daily_days",
            "weekly_weeks",
            "monthly_months",
            "minimum_changed_revisions",
            "max_copies_per_target",
            "tags",
        )


class BackupPolicyForm(NetBoxModelForm):
    retention_policy = forms.ModelChoiceField(queryset=RetentionPolicy.objects.all())

    class Meta:
        model = BackupPolicy
        fields = (
            "name",
            "enabled",
            "schedule_type",
            "interval_minutes",
            "time_of_day",
            "timezone_mode",
            "jitter_minutes",
            "connection_timeout",
            "command_timeout",
            "max_retries",
            "retry_backoff_minutes",
            "store_mode",
            "retention_policy",
            "tags",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
            "retry_backoff_minutes": forms.Textarea(attrs={"rows": 2}),
        }

    def save(self, commit=True):
        policy = super().save(commit=commit)
        if commit:
            now = timezone.now()
            for target in policy.target_overrides.select_related("policy_override", "device__site"):
                apply_target_schedule(target, now=now)
        return policy


class ConnectionProfileForm(NetBoxModelForm):
    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if cleaned.get("protocol") == ConnectionProtocolChoices.TELNET:
            cleaned["verify_host_key"] = False
            cleaned["known_hosts_path"] = ""
        return cleaned

    class Meta:
        model = ConnectionProfile
        fields = (
            "name",
            "protocol",
            "address_preference",
            "port",
            "connect_timeout",
            "command_timeout",
            "keepalive",
            "verify_host_key",
            "known_hosts_path",
            "tags",
        )


class CredentialProfileForm(NetBoxModelForm):
    provider_id = forms.ChoiceField(
        choices=(
            ("environment", "Environment variables"),
            ("encrypted_database", "Encrypted database (write-only password)"),
        ),
        help_text=(
            "Use an encrypted write-only password in NetBox or reference an environment variable."
        ),
    )
    secret_reference = forms.CharField(
        required=False,
        help_text="Environment example: env://ROUTER_1.",
    )
    username = forms.CharField(
        required=False,
        max_length=255,
        help_text="Used only by the encrypted database provider.",
    )
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Write-only. Leave blank while editing to keep the current password.",
    )
    password_confirm = forms.CharField(
        required=False,
        strip=False,
        label="Confirm password",
        widget=forms.PasswordInput(render_value=False),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending_reference = uuid.uuid4()
        current_provider = self.instance.provider_id if self.instance and self.instance.pk else ""
        known_values = {value for value, _label in self.fields["provider_id"].choices}
        if current_provider and current_provider not in known_values:
            self.fields["provider_id"].choices = (
                *self.fields["provider_id"].choices,
                (current_provider, f"Legacy/other provider: {current_provider}"),
            )
        if current_provider == "encrypted_database":
            self.fields["username"].initial = self.instance.stored_username

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        provider_id = cleaned.get("provider_id")
        password = cleaned.get("password") or ""
        password_confirm = cleaned.get("password_confirm") or ""

        if provider_id == "encrypted_database":
            if cleaned.get("auth_type") != "password":
                self.add_error(
                    "auth_type",
                    "Encrypted database credentials currently support password authentication only.",
                )
            cleaned["auth_type"] = "password"
            if not cleaned.get("username"):
                self.add_error("username", "Username is required for encrypted credentials.")
            has_existing = bool(
                self.instance
                and self.instance.pk
                and self.instance.provider_id == "encrypted_database"
                and self.instance.has_stored_password
            )
            if not password and not has_existing:
                self.add_error("password", "Password is required for a new encrypted credential.")
            if password != password_confirm:
                self.add_error("password_confirm", "Passwords do not match.")
            if password:
                try:
                    DatabaseCredentialCipher().active_key()
                except MasterKeyConfigurationError as exc:
                    self.add_error("password", str(exc))
            cleaned["secret_reference"] = (
                self.instance.secret_reference
                if has_existing
                else EncryptedDatabaseSecretProvider.format_reference(self._pending_reference)
            )
        elif provider_id in {"environment", "vault_kv2"}:
            if not cleaned.get("secret_reference"):
                self.add_error(
                    "secret_reference", "Secret reference is required for this provider."
                )
            if password or password_confirm or cleaned.get("username"):
                self.add_error(
                    "provider_id",
                    "Username and password fields are available only for encrypted database provider.",
                )
            if provider_id == "vault_kv2" and cleaned.get("secret_reference"):
                from .credentials.vault import VaultKV2SecretProvider

                try:
                    VaultKV2SecretProvider().parse_reference(cleaned["secret_reference"])
                except SecretProviderError as exc:
                    self.add_error("secret_reference", str(exc))
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        if not commit:
            return profile

        provider_id = self.cleaned_data["provider_id"]
        if provider_id == "encrypted_database":
            existing = (
                StoredCredential.objects.filter(profile=profile).first() if profile.pk else None
            )
            reference = (
                existing.reference
                if existing
                else EncryptedDatabaseSecretProvider.parse_reference(
                    self.cleaned_data["secret_reference"]
                )
            )
            profile.provider_id = provider_id
            profile.auth_type = "password"
            profile.secret_reference = EncryptedDatabaseSecretProvider.format_reference(reference)
            profile.save()
            self.save_m2m()

            password = self.cleaned_data.get("password") or ""
            if password:
                payload = DatabaseCredentialCipher().encrypt(
                    reference=reference,
                    plaintext=password,
                )
                StoredCredential.objects.update_or_create(
                    profile=profile,
                    defaults={
                        "reference": reference,
                        "username": self.cleaned_data["username"],
                        "ciphertext": payload.ciphertext,
                        "nonce": payload.nonce,
                        "key_version": payload.key_version,
                        "rotated_at": timezone.now(),
                    },
                )
            elif existing:
                existing.username = self.cleaned_data["username"]
                existing.save(update_fields=("username",))
        else:
            profile.save()
            self.save_m2m()
            StoredCredential.objects.filter(profile=profile).delete()
        return profile

    class Meta:
        model = CredentialProfile
        fields = (
            "name",
            "provider_id",
            "secret_reference",
            "auth_type",
            "username",
            "password",
            "password_confirm",
            "tags",
        )


class FtpIntegrityAuditScheduleForm(forms.ModelForm):
    integrity_audit_enabled = forms.BooleanField(
        required=False,
        label="Run integrity audits automatically",
        help_text="Read-only: verifies expected FTP file sizes and SHA-256 hashes.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean(self):
        super().clean()
        if self.cleaned_data.get("integrity_audit_enabled") and not self.instance.enabled:
            self.add_error(
                "integrity_audit_enabled",
                "Enable the FTP storage before enabling automatic audits.",
            )
        return self.cleaned_data

    def save(self, commit=True):
        destination = super().save(commit=False)
        destination.next_integrity_audit_at = calculate_destination_next_ftp_audit(
            destination,
            now=timezone.now(),
            timezone_name=settings.TIME_ZONE,
        )
        if commit:
            destination.save()
        return destination

    class Meta:
        model = BackupDestination
        fields = (
            "integrity_audit_enabled",
            "integrity_audit_frequency",
            "integrity_audit_weekday",
            "integrity_audit_time",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "integrity_audit_frequency": forms.Select(attrs={"class": "form-select"}),
            "integrity_audit_weekday": forms.Select(attrs={"class": "form-select"}),
            "integrity_audit_time": forms.TimeInput(
                attrs={"class": "form-control", "type": "time"}
            ),
        }


class BackupDestinationForm(NetBoxModelForm):
    enforce_retention_policy = forms.BooleanField(
        required=False,
        label="Always use this storage's retention profile",
        help_text=(
            "When enabled, a retention profile selected on a device cannot override the "
            "profile configured on this storage."
        ),
    )
    allow_insecure_ftp = forms.BooleanField(
        required=False,
        label=(
            "I understand that FTP sends the password and backup configuration without encryption"
        ),
        help_text="Required because this FTP storage is intended for a trusted internal network.",
    )
    credential_profile = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.filter(auth_type="password").exclude(
            provider_id="vault_kv2"
        ),
        help_text="Reusable password login for the internal FTP server.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # Additional storages created from the UI are FTP storages. The
            # singleton Local storage is provisioned by the plugin migration.
            self.instance.protocol = DestinationProtocolChoices.FTP
            self.fields["port"].initial = 21
        self.is_local_storage = self.instance.protocol == DestinationProtocolChoices.LOCAL
        if self.is_local_storage:
            # The Local storage represents the built-in primary backend. Its
            # endpoint, state, identity, and tags are system-managed.
            for field_name in (
                "name",
                "enabled",
                "auto_replicate",
                "allow_insecure_ftp",
                "host",
                "port",
                "base_path",
                "credential_profile",
                "connect_timeout",
                "max_retries",
                "retry_delay_minutes",
                "max_artifact_size",
                "remote_retention_policy",
                "tags",
            ):
                self.fields.pop(field_name, None)
        else:
            self.fields.pop("local_retention_policy", None)

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if not self.is_local_storage and not cleaned.get("allow_insecure_ftp"):
            self.add_error(
                "allow_insecure_ftp",
                "Confirm that this internal storage may use unencrypted FTP.",
            )
        retention_field = (
            "local_retention_policy" if self.is_local_storage else "remote_retention_policy"
        )
        if cleaned.get("enforce_retention_policy") and not cleaned.get(retention_field):
            self.add_error(
                retention_field,
                "Select a retention profile before enforcing storage-level retention.",
            )
        return cleaned

    def save(self, commit=True):
        destination = super().save(commit=False)
        if not self.is_local_storage:
            destination.protocol = DestinationProtocolChoices.FTP
            destination.host_key_type = ""
            destination.host_key_public = ""
            destination.host_key_fingerprint_sha256 = ""
            destination.host_key_fingerprint_md5 = ""
            destination.host_key_approved_at = None
            destination.host_key_approved_by = None
            if not destination.enabled or not destination.integrity_audit_enabled:
                destination.next_integrity_audit_at = None
            elif destination.next_integrity_audit_at is None:
                destination.next_integrity_audit_at = calculate_destination_next_ftp_audit(
                    destination,
                    now=timezone.now(),
                    timezone_name=settings.TIME_ZONE,
                )
        if commit:
            destination.save()
            self.save_m2m()
        return destination

    class Meta:
        model = BackupDestination
        fields = (
            "name",
            "enabled",
            "auto_replicate",
            "local_retention_policy",
            "remote_retention_policy",
            "enforce_retention_policy",
            "allow_insecure_ftp",
            "host",
            "port",
            "base_path",
            "credential_profile",
            "connect_timeout",
            "max_retries",
            "retry_delay_minutes",
            "max_artifact_size",
            "tags",
        )


class SftpReceiverProfileForm(NetBoxModelForm):
    credential_profile = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.exclude(provider_id="vault_kv2"),
        help_text="Password credentials used only by devices uploading to this receiver.",
    )

    class Meta:
        model = SftpReceiverProfile
        fields = (
            "name",
            "enabled",
            "protocol",
            "mode",
            "credential_profile",
            "listen_host",
            "listen_port",
            "advertised_host",
            "advertised_port",
            "bridge_host",
            "bridge_port",
            "remote_bind_host",
            "remote_bind_port",
            "upload_directory",
            "export_timeout",
            "max_upload_size",
            "passive_port_start",
            "passive_port_end",
            "tags",
        )


class PlatformMappingForm(NetBoxModelForm):
    platform = DynamicModelChoiceField(queryset=Platform.objects.all())
    connection_profile = forms.ModelChoiceField(
        queryset=ConnectionProfile.objects.all(), required=False
    )
    credential_profile = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.exclude(provider_id="vault_kv2"), required=False
    )
    receiver_profile = forms.ModelChoiceField(
        queryset=SftpReceiverProfile.objects.all(), required=False
    )
    driver_id = forms.ChoiceField(choices=(), label="Driver")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_driver = self.instance.driver_id if self.instance and self.instance.pk else ""
        self.fields["driver_id"].choices = _driver_choices(
            include_ids=(current_driver,) if current_driver else (),
        )

    class Meta:
        model = PlatformMapping
        fields = (
            "platform",
            "driver_id",
            "connection_profile",
            "credential_profile",
            "receiver_profile",
            "enabled",
            "driver_options",
            "tags",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "driver_options": forms.Textarea(attrs={"rows": 5})
        }


class BackupTargetForm(NetBoxModelForm):
    device = DynamicModelChoiceField(queryset=Device.objects.all())
    policy_override = forms.ModelChoiceField(queryset=BackupPolicy.objects.all(), required=False)
    retention_override = forms.ModelChoiceField(
        queryset=RetentionPolicy.objects.all(),
        required=False,
        label="Local retention profile",
        help_text=(
            "Leave blank to use the backup policy or Local storage profile. "
            "An enforced Local storage profile always wins."
        ),
    )
    remote_retention_policy = forms.ModelChoiceField(
        queryset=RemoteRetentionPolicy.objects.all(),
        required=False,
        label="FTP retention profile",
        help_text=(
            "Leave blank to use each FTP storage profile. Copies are kept indefinitely "
            "only on a storage which also has no profile."
        ),
    )
    credential_override = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.exclude(provider_id="vault_kv2"), required=False
    )
    connection_override = forms.ModelChoiceField(
        queryset=ConnectionProfile.objects.all(), required=False
    )
    receiver_override = forms.ModelChoiceField(
        queryset=SftpReceiverProfile.objects.all(), required=False
    )
    driver_override = forms.ChoiceField(choices=(), required=False, label="Driver override")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_driver = self.instance.driver_override if self.instance and self.instance.pk else ""
        self.fields["driver_override"].choices = _driver_choices(
            blank=True,
            include_ids=(current_driver,) if current_driver else (),
        )

    class Meta:
        model = BackupTarget
        fields = (
            "device",
            "enabled",
            "policy_override",
            "retention_override",
            "remote_retention_policy",
            "credential_override",
            "connection_override",
            "receiver_override",
            "driver_override",
            "driver_options_override",
            "tags",
        )
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "driver_options_override": forms.Textarea(attrs={"rows": 5})
        }

    def save(self, commit=True):
        target = super().save(commit=commit)
        if commit:
            target = BackupTarget.objects.select_related("policy_override", "device__site").get(
                pk=target.pk
            )
            apply_target_schedule(target, now=timezone.now())
        return target


class RetentionCleanupConfirmationForm(forms.Form):
    confirm = forms.BooleanField(
        label="I understand that expired history and its stored artifacts will be deleted",
    )


class RemoteRetentionCleanupConfirmationForm(forms.Form):
    confirm = forms.BooleanField(
        label="I understand that expired FTP copies will be permanently deleted",
    )
