from __future__ import annotations

from typing import ClassVar

from django import forms
from django.conf import settings
from django.utils import timezone
from netbox.forms import NetBoxModelForm
from utilities.forms.rendering import FieldSet

from .choices import MOUNTED_DESTINATION_PROTOCOLS, DestinationProtocolChoices
from .models import BackupDestination, CredentialProfile, SftpReceiverProfile
from .services.ftp_audit_scheduling import calculate_destination_next_ftp_audit


class FtpIntegrityAuditScheduleForm(forms.ModelForm):
    integrity_audit_enabled = forms.BooleanField(
        required=False,
        label="Run integrity audits automatically",
        help_text="Read-only: verifies expected remote file sizes and SHA-256 hashes.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean(self):
        super().clean()
        if self.cleaned_data.get("integrity_audit_enabled") and not self.instance.enabled:
            self.add_error(
                "integrity_audit_enabled",
                "Enable the remote storage before enabling automatic audits.",
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
    protocol = forms.ChoiceField(
        choices=(
            (DestinationProtocolChoices.FTP, "FTP server (internal, unencrypted)"),
            (DestinationProtocolChoices.NFS, "NFS mount"),
            (DestinationProtocolChoices.SMB, "SMB3 / Samba mount"),
        ),
        label="Storage type",
        help_text=(
            "NFS and SMB3 shares must already be mounted into the NetBox web and worker "
            "containers. SMB1 is not supported."
        ),
    )
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
        label="Allow unencrypted FTP",
        help_text=(
            "Required to save. Use only on a trusted internal network because FTP does not "
            "encrypt credentials or backup data."
        ),
    )
    credential_profile = forms.ModelChoiceField(
        queryset=CredentialProfile.objects.filter(auth_type="password").exclude(
            provider_id="vault_kv2"
        ),
        label="FTP credentials",
        help_text="Reusable username and password used to sign in to this FTP server.",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # The singleton Local storage is provisioned by a plugin migration.
            self.instance.protocol = DestinationProtocolChoices.FTP
            self.fields["port"].initial = 21
        self.is_local_storage = self.instance.protocol == DestinationProtocolChoices.LOCAL
        if self.is_local_storage:
            # The Local storage represents the built-in primary backend. Its
            # endpoint, state, identity, and tags are system-managed.
            for field_name in (
                "name",
                "protocol",
                "enabled",
                "auto_replicate",
                "allow_insecure_ftp",
                "host",
                "port",
                "base_path",
                "mount_path",
                "credential_profile",
                "connect_timeout",
                "max_retries",
                "retry_delay_minutes",
                "max_artifact_size",
                "remote_retention_policy",
                "tags",
            ):
                self.fields.pop(field_name, None)
            self.fieldsets = (
                FieldSet(
                    "local_retention_policy",
                    "enforce_retention_policy",
                    name="Local retention",
                ),
            )
        else:
            self.fields.pop("local_retention_policy", None)
            self.fieldsets = (
                FieldSet(
                    "name",
                    "protocol",
                    "enabled",
                    "auto_replicate",
                    name="Storage",
                ),
                FieldSet(
                    "host",
                    "port",
                    "credential_profile",
                    "connect_timeout",
                    "allow_insecure_ftp",
                    "mount_path",
                    name="Connection",
                ),
                FieldSet(
                    "base_path",
                    "remote_retention_policy",
                    "enforce_retention_policy",
                    name="Layout and retention",
                ),
                FieldSet(
                    "max_retries",
                    "retry_delay_minutes",
                    "max_artifact_size",
                    "tags",
                    name="Reliability limits",
                ),
            )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        protocol = (
            DestinationProtocolChoices.LOCAL
            if self.is_local_storage
            else cleaned.get("protocol", self.instance.protocol)
        )
        if protocol == DestinationProtocolChoices.FTP and not cleaned.get("allow_insecure_ftp"):
            self.add_error(
                "allow_insecure_ftp",
                "Confirm that this internal storage may use unencrypted FTP.",
            )
        if not self.is_local_storage and not cleaned.get("base_path"):
            self.add_error("base_path", "Enter the directory used for plugin backup copies.")
        if protocol == DestinationProtocolChoices.FTP:
            cleaned["mount_path"] = ""
            self.instance.mount_path = ""
            for field_name, message in (
                ("host", "Enter the FTP server name or address."),
                ("port", "Enter the FTP control port."),
                ("credential_profile", "Select FTP credentials."),
                ("connect_timeout", "Enter a connection timeout."),
            ):
                if not cleaned.get(field_name):
                    self.add_error(field_name, message)
        elif protocol in MOUNTED_DESTINATION_PROTOCOLS and not cleaned.get("mount_path"):
            self.add_error(
                "mount_path",
                "Enter the absolute directory where the share is mounted in the containers.",
            )
        elif protocol in MOUNTED_DESTINATION_PROTOCOLS:
            cleaned["host"] = ""
            cleaned["port"] = None
            cleaned["credential_profile"] = None
            cleaned["connect_timeout"] = None
            cleaned["allow_insecure_ftp"] = False
            # ModelForm skips omitted fields which have model defaults. The
            # protocol-specific UI disables these FTP inputs, so clear the
            # instance too before Django runs model validation.
            self.instance.host = ""
            self.instance.port = None
            self.instance.credential_profile = None
            self.instance.connect_timeout = None
            self.instance.allow_insecure_ftp = False
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
            destination.protocol = self.cleaned_data["protocol"]
            destination.host_key_type = ""
            destination.host_key_public = ""
            destination.host_key_fingerprint_sha256 = ""
            destination.host_key_fingerprint_md5 = ""
            destination.host_key_approved_at = None
            destination.host_key_approved_by = None
            if destination.protocol in MOUNTED_DESTINATION_PROTOCOLS:
                destination.host = ""
                destination.port = None
                destination.credential_profile = None
                destination.connect_timeout = None
                destination.allow_insecure_ftp = False
            else:
                destination.mount_path = ""
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
            "protocol",
            "enabled",
            "auto_replicate",
            "local_retention_policy",
            "remote_retention_policy",
            "enforce_retention_policy",
            "allow_insecure_ftp",
            "host",
            "port",
            "base_path",
            "mount_path",
            "credential_profile",
            "connect_timeout",
            "max_retries",
            "retry_delay_minutes",
            "max_artifact_size",
            "tags",
        )
        labels: ClassVar[dict[str, str]] = {
            "name": "Storage name",
            "protocol": "Storage type",
            "enabled": "Use this storage",
            "auto_replicate": "Copy new revisions automatically",
            "local_retention_policy": "Local retention profile",
            "remote_retention_policy": "Remote retention profile",
            "host": "FTP server",
            "port": "FTP port",
            "base_path": "Base directory",
            "mount_path": "Mounted directory",
            "connect_timeout": "Connection timeout (seconds)",
            "max_retries": "Retry attempts",
            "retry_delay_minutes": "Retry delay (minutes)",
            "max_artifact_size": "Maximum artifact size (bytes)",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "enabled": "Disabled remote storage receives no new copies, retries, or audits.",
            "auto_replicate": "Copy each new local revision to this storage automatically.",
            "local_retention_policy": (
                "Default cleanup profile for local revisions and run history."
            ),
            "remote_retention_policy": "Default cleanup profile for copies on this storage.",
            "host": "FTP only: DNS name or IP address reachable from the NetBox worker.",
            "port": "FTP only: control port. The default is 21.",
            "base_path": "Directory below which device backup folders are created.",
            "mount_path": (
                "NFS/SMB3 only: absolute mounted directory below an allowed mount root. Configure "
                "the same read/write mount in the NetBox web and backup-worker containers."
            ),
            "connect_timeout": "FTP only: maximum time allowed to connect to the server.",
            "max_retries": "Number of copy retries after the first failed attempt.",
            "retry_delay_minutes": "Wait time between failed copy attempts.",
            "max_artifact_size": "Largest single backup artifact accepted by this storage.",
        }


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
