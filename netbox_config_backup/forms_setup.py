from __future__ import annotations

from typing import ClassVar

from dcim.models import Device
from django import forms
from django.urls import reverse
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import DynamicModelChoiceField

from .choices import ConnectionProtocolChoices, SSHHostKeyPolicyChoices
from .credentials.encrypted_database import DatabaseCredentialCipher, MasterKeyConfigurationError
from .drivers import driver_registry
from .models import (
    BackupTarget,
    ConnectionProfile,
    CredentialProfile,
    OperationalSettings,
    PlatformMapping,
    SftpReceiverProfile,
)


def driver_choices(
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
        label="I understand that remote retention permanently deletes expired copies",
        help_text="Required only when automatic remote retention is being enabled.",
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
                "Confirm the permanent deletion warning before enabling remote cleanup.",
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


class InterfaceLanguageSettingsForm(NetBoxModelForm):
    class Meta:
        model = OperationalSettings
        fields = ("ui_language",)


class QuickSetupForm(forms.Form):
    advanced_field_names = (
        "driver_id",
        "receiver_profile",
        "sync_receiver_credentials",
        "restore_point",
        "connection_profile",
        "protocol",
        "port",
        "host_key_policy",
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
        label="Device upload receiver",
        help_text="Automatic uses the device upload receiver from the platform mapping.",
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
    host_key_policy = forms.ChoiceField(
        choices=SSHHostKeyPolicyChoices.choices,
        initial=SSHHostKeyPolicyChoices.STRICT,
        required=False,
        label="SSH identity verification",
        help_text=(
            "Manual approval is safest. Trust on first use removes the first approval step but "
            "still blocks later key changes. Disabled performs no SSH server identity check."
        ),
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
            ("", "Use remote storage profile"),
            (90, "90 days"),
            (365, "365 days"),
            (730, "730 days"),
        ),
        coerce=int,
        empty_value=None,
        required=False,
        initial="",
        label="Remote backup history",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = Device.objects.filter(config_backup_target__isnull=True)
        self.fields["device"].widget.attrs["data-url"] = reverse(
            "plugins-api:netbox_config_backup-api:available-device-list"
        )
        self.fields["driver_id"].choices = (
            ("", "Automatic from platform mapping"),
            *driver_choices(),
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
                "an enabled device upload receiver.",
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
