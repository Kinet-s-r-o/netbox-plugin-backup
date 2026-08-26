"""Serializers for public Config Backup models.

NetBox uses these serializers for changelog and event-rule snapshots even when
the plugin does not expose REST API endpoints yet.
"""

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import ModelSerializer

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
        attrs = super().validate(attrs)
        if (
            self.instance
            and self.instance.replicas.filter(
                remote_deleted_at__isnull=True,
            )
            .exclude(remote_path="")
            .exists()
        ):
            errors = {
                field_name: (
                    "This endpoint field cannot be changed while FTP copies exist. "
                    "Create a new destination for a different FTP server or path."
                )
                for field_name in ("protocol", "host", "port", "base_path")
                if field_name in attrs and attrs[field_name] != getattr(self.instance, field_name)
            }
            if (
                "credential_profile" in attrs
                and attrs["credential_profile"].pk != self.instance.credential_profile_id
            ):
                errors["credential_profile"] = (
                    "This endpoint field cannot be changed while FTP copies exist. "
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
