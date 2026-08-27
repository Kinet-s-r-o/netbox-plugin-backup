"""Serializers for public Config Backup models.

NetBox uses these serializers for changelog and event-rule snapshots even when
the plugin does not expose REST API endpoints yet.
"""

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import ModelSerializer

from netbox_config_backup.choices import DestinationProtocolChoices
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigArtifact,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    OperationalSettings,
    PlatformMapping,
    RemoteRetentionPolicy,
    RetentionPolicy,
    RevisionReplica,
    SftpReceiverProfile,
    SSHHostKey,
    StoredCredential,
)


class OperationalSettingsSerializer(NetBoxModelSerializer):
    class Meta:
        model = OperationalSettings
        fields = "__all__"


class RetentionPolicySerializer(NetBoxModelSerializer):
    class Meta:
        model = RetentionPolicy
        fields = "__all__"


class RemoteRetentionPolicySerializer(NetBoxModelSerializer):
    class Meta:
        model = RemoteRetentionPolicy
        fields = "__all__"


class BackupPolicySerializer(NetBoxModelSerializer):
    class Meta:
        model = BackupPolicy
        fields = "__all__"


class ConnectionProfileSerializer(NetBoxModelSerializer):
    class Meta:
        model = ConnectionProfile
        fields = "__all__"


class CredentialProfileSerializer(NetBoxModelSerializer):
    class Meta:
        model = CredentialProfile
        fields = "__all__"


class BackupDestinationSerializer(NetBoxModelSerializer):
    def validate(self, attrs):
        original = None
        if self.instance is not None and self.instance.pk:
            # NetBox's validated model serializer temporarily applies incoming
            # values to ``self.instance`` while running model validation. Keep
            # an independent database snapshot for immutable-field checks.
            original = BackupDestination.objects.get(pk=self.instance.pk)
        attrs = super().validate(attrs)
        protocol = attrs.get(
            "protocol",
            getattr(original, "protocol", DestinationProtocolChoices.SFTP),
        )
        if self.instance is None and protocol == DestinationProtocolChoices.LOCAL:
            raise ValidationError(
                {"protocol": "The system creates and protects the default Local storage."}
            )
        if original and original.protocol == DestinationProtocolChoices.LOCAL:
            immutable_fields = (
                "name",
                "enabled",
                "auto_replicate",
                "protocol",
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
                "integrity_audit_enabled",
                "integrity_audit_frequency",
                "integrity_audit_time",
                "integrity_audit_weekday",
            )
            errors = {
                field_name: "This field is managed by the protected Local storage."
                for field_name in immutable_fields
                if field_name in attrs and attrs[field_name] != getattr(original, field_name)
            }
            if errors:
                raise ValidationError(errors)
        policy_field = (
            "local_retention_policy"
            if protocol == DestinationProtocolChoices.LOCAL
            else "remote_retention_policy"
        )
        selected_policy = attrs.get(policy_field, getattr(original, policy_field, None))
        if (
            attrs.get(
                "enforce_retention_policy",
                getattr(original, "enforce_retention_policy", False),
            )
            and selected_policy is None
        ):
            raise ValidationError({policy_field: "Select a retention profile before enforcing it."})
        if (
            original
            and original.replicas.filter(
                remote_deleted_at__isnull=True,
            )
            .exclude(remote_path="")
            .exists()
        ):
            errors = {
                field_name: (
                    "This endpoint field cannot be changed while remote copies exist. "
                    "Create a new destination for a different endpoint or path."
                )
                for field_name in ("protocol", "host", "port", "base_path", "mount_path")
                if field_name in attrs and attrs[field_name] != getattr(original, field_name)
            }
            if (
                "credential_profile" in attrs
                and getattr(attrs["credential_profile"], "pk", None)
                != original.credential_profile_id
            ):
                errors["credential_profile"] = (
                    "This endpoint field cannot be changed while remote copies exist. "
                    "Rotate the password inside the current credential profile or create a new "
                    "destination."
                )
            if errors:
                raise ValidationError(errors)
        return attrs

    class Meta:
        model = BackupDestination
        fields = "__all__"
        read_only_fields = (
            "is_default",
            "host_key_type",
            "host_key_public",
            "host_key_fingerprint_sha256",
            "host_key_fingerprint_md5",
            "host_key_approved_at",
            "host_key_approved_by",
            "last_tested_at",
            "last_success_at",
            "last_error_code",
            "last_error_message",
        )


class RevisionReplicaSerializer(NetBoxModelSerializer):
    class Meta:
        model = RevisionReplica
        fields = "__all__"


class SftpReceiverProfileSerializer(NetBoxModelSerializer):
    class Meta:
        model = SftpReceiverProfile
        fields = "__all__"


class PlatformMappingSerializer(NetBoxModelSerializer):
    class Meta:
        model = PlatformMapping
        fields = "__all__"


class BackupTargetSerializer(NetBoxModelSerializer):
    class Meta:
        model = BackupTarget
        fields = "__all__"


class SSHHostKeySerializer(NetBoxModelSerializer):
    class Meta:
        model = SSHHostKey
        fields = "__all__"


class ConfigRevisionSerializer(NetBoxModelSerializer):
    class Meta:
        model = ConfigRevision
        fields = "__all__"


class BackupRunSerializer(NetBoxModelSerializer):
    class Meta:
        model = BackupRun
        fields = "__all__"


class ConfigArtifactSerializer(ModelSerializer):
    """Internal event snapshot; artifacts are not exposed by the REST router."""

    class Meta:
        model = ConfigArtifact
        fields = (
            "id",
            "revision",
            "artifact_type",
            "format",
            "storage_key",
            "size",
            "raw_hash",
            "normalized_hash",
            "is_primary",
            "local_available",
            "local_deleted_at",
            "created",
            "last_updated",
        )


class StoredCredentialSerializer(ModelSerializer):
    """Redacted internal event snapshot which never serializes credential material."""

    class Meta:
        model = StoredCredential
        fields = ("id", "profile", "reference", "key_version", "rotated_at")
        read_only_fields = fields
