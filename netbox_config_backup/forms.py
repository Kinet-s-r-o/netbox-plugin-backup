import uuid
from typing import ClassVar

from dcim.models import Device, Platform
from django import forms
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import DynamicModelChoiceField

from .choices import (
    ConnectionProtocolChoices,
    SSHHostKeyPolicyChoices,
)
from .credentials.base import SecretProviderError
from .credentials.encrypted_database import (
    DatabaseCredentialCipher,
    EncryptedDatabaseSecretProvider,
    MasterKeyConfigurationError,
)
from .forms_filters import BackupRunFilterForm, BackupTargetFilterForm, ConfigRevisionFilterForm
from .forms_setup import (
    DownloadEncryptionSettingsForm,
    InterfaceLanguageSettingsForm,
    NotificationSettingsForm,
    OperationalSettingsForm,
    QuickSetupForm,
)
from .forms_setup import (
    driver_choices as _driver_choices,
)
from .forms_storage import (
    BackupDestinationForm,
    FtpIntegrityAuditScheduleForm,
    SftpReceiverProfileForm,
)
from .models import (
    BackupPolicy,
    BackupTarget,
    ConnectionProfile,
    CredentialProfile,
    PlatformMapping,
    RemoteRetentionPolicy,
    RetentionPolicy,
    SftpReceiverProfile,
    StoredCredential,
)
from .services.scheduling import apply_target_schedule

__all__ = [
    "BackupDestinationForm",
    "BackupPolicyForm",
    "BackupRunFilterForm",
    "BackupTargetFilterForm",
    "BackupTargetForm",
    "ConfigRevisionFilterForm",
    "ConnectionProfileForm",
    "CredentialProfileForm",
    "DownloadEncryptionSettingsForm",
    "FtpIntegrityAuditScheduleForm",
    "InterfaceLanguageSettingsForm",
    "NotificationSettingsForm",
    "OperationalSettingsForm",
    "PlatformMappingForm",
    "QuickSetupForm",
    "RemoteRetentionCleanupConfirmationForm",
    "RemoteRetentionPolicyForm",
    "RetentionCleanupConfirmationForm",
    "RetentionPolicyForm",
    "SftpReceiverProfileForm",
]


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
        labels: ClassVar[dict[str, str]] = {
            "name": "Profile name",
            "keep_all_days": "Keep all revisions (days)",
            "daily_days": "Keep daily revisions (days)",
            "weekly_weeks": "Keep weekly revisions (weeks)",
            "monthly_months": "Keep monthly revisions (months)",
            "minimum_changed_revisions": "Minimum changed revisions",
            "unchanged_run_days": "Keep unchanged runs (days)",
            "changed_run_days": "Keep changed runs (days)",
            "failed_run_days": "Keep failed runs (days)",
            "max_runs_per_target": "Maximum completed runs per device",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "keep_all_days": "Keep every local revision created within this period.",
            "daily_days": "After that, keep the newest local revision from each day.",
            "weekly_weeks": "Keep the newest local revision from each week in this period.",
            "monthly_months": "Keep the newest local revision from each month in this period.",
            "minimum_changed_revisions": (
                "Always keep at least this many revisions where the configuration changed."
            ),
            "unchanged_run_days": "Keep successful run records with no configuration change.",
            "changed_run_days": "Keep successful run records that created a changed revision.",
            "failed_run_days": "Keep failed, partial, errored, and skipped run records.",
            "max_runs_per_target": (
                "Final safety limit for completed runs. Queued and running jobs are not removed."
            ),
        }


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
        labels: ClassVar[dict[str, str]] = {
            "name": "Profile name",
            "keep_all_days": "Keep all remote copies (days)",
            "daily_days": "Keep daily remote copies (days)",
            "weekly_weeks": "Keep weekly remote copies (weeks)",
            "monthly_months": "Keep monthly remote copies (months)",
            "minimum_changed_revisions": "Minimum changed copies",
            "max_copies_per_target": "Maximum remote copies per device",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "keep_all_days": "Keep every remote copy created within this period.",
            "daily_days": "After that, keep the newest remote copy from each day.",
            "weekly_weeks": "Keep the newest remote copy from each week in this period.",
            "monthly_months": "Keep the newest remote copy from each month in this period.",
            "minimum_changed_revisions": (
                "Always keep at least this many copies where the configuration changed."
            ),
            "max_copies_per_target": (
                "Final safety limit per device and remote storage. Latest and protected copies remain."
            ),
        }


class BackupPolicyForm(NetBoxModelForm):
    retention_policy = forms.ModelChoiceField(
        queryset=RetentionPolicy.objects.all(),
        label="Local retention profile",
        help_text="Controls local revision and backup run cleanup for devices using this policy.",
    )

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
        labels: ClassVar[dict[str, str]] = {
            "name": "Policy name",
            "schedule_type": "Schedule",
            "interval_minutes": "Run every (minutes)",
            "time_of_day": "Run at",
            "timezone_mode": "Time zone source",
            "jitter_minutes": "Start delay window (minutes)",
            "connection_timeout": "Connection timeout (seconds)",
            "command_timeout": "Command timeout (seconds)",
            "max_retries": "Retry attempts",
            "retry_backoff_minutes": "Retry delays (minutes)",
            "store_mode": "Revision creation",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "enabled": "Disabled policies do not queue scheduled backups.",
            "schedule_type": "Run at a fixed daily time or at a repeating interval.",
            "interval_minutes": "Required only for an interval schedule.",
            "time_of_day": "Required only for a daily schedule.",
            "timezone_mode": (
                "Use the device site's time zone or the default time zone configured in NetBox."
            ),
            "jitter_minutes": "Spread start times randomly across this window to reduce load.",
            "connection_timeout": "Maximum time allowed to establish a device connection.",
            "command_timeout": "Maximum time allowed for a backup command to finish.",
            "max_retries": "Number of retries after the first failed attempt.",
            "retry_backoff_minutes": "JSON list of delays, for example [1, 5, 15].",
            "store_mode": "Store only changes or create a revision after every successful run.",
        }

    def save(self, commit=True):
        policy = super().save(commit=commit)
        if commit:
            now = timezone.now()
            for target in policy.target_overrides.select_related("policy_override", "device__site"):
                apply_target_schedule(target, now=now)
        return policy


class ConnectionProfileForm(NetBoxModelForm):
    host_key_policy = forms.ChoiceField(
        choices=SSHHostKeyPolicyChoices.choices,
        label="SSH identity verification",
        help_text=(
            "Manual approval is safest. Trust on first use accepts only the first observed key; "
            "later changes still require approval. Disabled skips server identity verification."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["host_key_policy"].initial = self.instance.host_key_policy

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        if cleaned.get("protocol") == ConnectionProtocolChoices.TELNET:
            cleaned["host_key_policy"] = SSHHostKeyPolicyChoices.DISABLED
        policy = cleaned.get("host_key_policy", SSHHostKeyPolicyChoices.STRICT)
        self.instance.verify_host_key = policy != SSHHostKeyPolicyChoices.DISABLED
        self.instance.auto_trust_first_host_key = (
            policy == SSHHostKeyPolicyChoices.TRUST_ON_FIRST_USE
        )
        if policy == SSHHostKeyPolicyChoices.DISABLED:
            self.instance.known_hosts_path = ""
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
            "host_key_policy",
            "tags",
        )
        labels: ClassVar[dict[str, str]] = {
            "name": "Profile name",
            "protocol": "Connection protocol",
            "address_preference": "Management IP priority",
            "port": "TCP port",
            "connect_timeout": "Connection timeout (seconds)",
            "command_timeout": "Command timeout (seconds)",
            "keepalive": "Keepalive interval (seconds)",
            "host_key_policy": "SSH identity verification",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "protocol": "Automatic lets the selected driver and port choose SSH or Telnet.",
            "address_preference": (
                "Choose which NetBox device address is tried first. Dedicated management IP "
                "(OOB) means the device's OOB IP field in NetBox."
            ),
            "port": "Use 22 for SSH or 23 for Telnet unless the device uses a custom port.",
            "connect_timeout": "Maximum time allowed to establish the session.",
            "command_timeout": "Maximum time allowed for one backup command to finish.",
            "keepalive": "Send a keepalive at this interval. Use 0 to disable it.",
            "host_key_policy": (
                "Choose manual approval, trust only the first observed key, or explicitly disable "
                "SSH server identity verification."
            ),
        }


class CredentialProfileForm(NetBoxModelForm):
    provider_id = forms.ChoiceField(
        choices=(
            ("environment", "Environment variables"),
            ("encrypted_database", "Encrypted database (write-only password)"),
        ),
        label="Credential source",
        help_text="Store an encrypted password or reference an environment variable.",
    )
    secret_reference = forms.CharField(
        required=False,
        label="Environment reference",
        help_text="Required for environment credentials. Example: env://ROUTER_1.",
    )
    username = forms.CharField(
        required=False,
        max_length=255,
        help_text="Used only when the password is stored by the plugin.",
    )
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Stored encrypted and never displayed. Leave blank while editing to keep it.",
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
        labels: ClassVar[dict[str, str]] = {
            "name": "Profile name",
            "auth_type": "Authentication method",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "auth_type": "Select the authentication method expected by the device.",
        }


class PlatformMappingForm(NetBoxModelForm):
    platform = DynamicModelChoiceField(
        queryset=Platform.objects.all(),
        label="NetBox platform",
        help_text="Devices using this platform inherit the defaults below.",
    )
    connection_profile = forms.ModelChoiceField(
        queryset=ConnectionProfile.objects.all(),
        required=False,
        label="Default connection",
        help_text="Address selection, protocol, port, timeouts, and SSH verification.",
    )
    credential_profile = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.exclude(provider_id="vault_kv2"),
        required=False,
        label="Default credentials",
        help_text="Login used unless the backup device has its own credential override.",
    )
    receiver_profile = forms.ModelChoiceField(
        queryset=SftpReceiverProfile.objects.all(),
        required=False,
        label="Default device upload receiver",
        help_text="Required only for drivers where the device sends a native backup file.",
    )
    driver_id = forms.ChoiceField(
        choices=(),
        label="Backup driver",
        help_text="Select the driver that matches devices using this NetBox platform.",
    )

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
        labels: ClassVar[dict[str, str]] = {
            "enabled": "Use this mapping",
            "driver_options": "Driver options (JSON)",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "enabled": "Disabled mappings are ignored by automatic device setup.",
            "driver_options": "Optional driver-specific settings as a JSON object.",
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
        label="Remote retention profile",
        help_text=(
            "Leave blank to use each remote storage profile. Copies are kept indefinitely "
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
        available_devices = Device.objects.filter(config_backup_target__isnull=True)
        if self.instance and self.instance.pk and self.instance.device_id:
            available_devices = Device.objects.filter(
                Q(config_backup_target__isnull=True) | Q(pk=self.instance.device_id)
            )
        self.fields["device"].queryset = available_devices
        self.fields["device"].widget.attrs["data-url"] = reverse(
            "plugins-api:netbox_config_backup-api:available-device-list"
        )
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
        label="I understand that expired remote copies will be permanently deleted",
    )
