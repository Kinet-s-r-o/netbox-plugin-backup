from dcim.api.serializers import DeviceSerializer
from dcim.filtersets import DeviceFilterSet
from dcim.models import Device
from netbox.api.viewsets import NetBoxModelViewSet, NetBoxReadOnlyModelViewSet
from rest_framework.exceptions import PermissionDenied, ValidationError

from netbox_config_backup.filtersets import BackupRunFilterSet, BackupTargetFilterSet
from netbox_config_backup.models import (
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
    RevisionReplica,
    SftpReceiverProfile,
)

from .serializers import (
    BackupDestinationSerializer,
    BackupPolicySerializer,
    BackupRunSerializer,
    BackupTargetSerializer,
    ConfigRevisionSerializer,
    ConnectionProfileSerializer,
    CredentialProfileSerializer,
    PlatformMappingSerializer,
    RemoteRetentionPolicySerializer,
    RetentionPolicySerializer,
    RevisionReplicaSerializer,
    SftpReceiverProfileSerializer,
)


class RetentionPolicyViewSet(NetBoxModelViewSet):
    queryset = RetentionPolicy.objects.all()
    serializer_class = RetentionPolicySerializer


class RemoteRetentionPolicyViewSet(NetBoxModelViewSet):
    queryset = RemoteRetentionPolicy.objects.all()
    serializer_class = RemoteRetentionPolicySerializer


class AvailableDeviceViewSet(NetBoxReadOnlyModelViewSet):
    """NetBox devices which do not already have a Config Backup target."""

    queryset = Device.objects.filter(config_backup_target__isnull=True).select_related(
        "device_type__manufacturer",
        "site",
    )
    serializer_class = DeviceSerializer
    filterset_class = DeviceFilterSet


class BackupPolicyViewSet(NetBoxModelViewSet):
    queryset = BackupPolicy.objects.all()
    serializer_class = BackupPolicySerializer


class ConnectionProfileViewSet(NetBoxModelViewSet):
    queryset = ConnectionProfile.objects.all()
    serializer_class = ConnectionProfileSerializer


class CredentialProfileViewSet(NetBoxModelViewSet):
    queryset = CredentialProfile.objects.all()
    serializer_class = CredentialProfileSerializer


class BackupDestinationViewSet(NetBoxModelViewSet):
    queryset = BackupDestination.objects.all()
    serializer_class = BackupDestinationSerializer

    def _assert_retention_permissions(self, *, local_changed=False, remote_changed=False):
        user = self.request.user
        if (local_changed or remote_changed) and not (
            OperationalSettings.objects.restrict(user, "change").filter(singleton=True).exists()
        ):
            raise PermissionDenied("Storage retention changes require operational authority.")
        if local_changed and not user.has_perms(
            (
                "netbox_config_backup.delete_configartifact",
                "netbox_config_backup.delete_configrevision",
                "netbox_config_backup.delete_backuprun",
                "netbox_config_backup.delete_revisionreplica",
            )
        ):
            raise PermissionDenied("Local retention cleanup permissions are required.")
        if remote_changed and not user.has_perms(
            (
                "netbox_config_backup.delete_configartifact",
                "netbox_config_backup.delete_revisionreplica",
                "netbox_config_backup.delete_configrevision",
            )
        ):
            raise PermissionDenied("Remote retention cleanup permissions are required.")

    def perform_create(self, serializer):
        data = serializer.validated_data
        self._assert_retention_permissions(
            remote_changed=bool(
                data.get("remote_retention_policy") or data.get("enforce_retention_policy")
            )
        )
        return super().perform_create(serializer)

    def perform_update(self, serializer):
        instance = serializer.instance
        original = BackupDestination.objects.get(pk=instance.pk)
        data = serializer.validated_data
        self._assert_retention_permissions(
            local_changed=(
                original.protocol == "local"
                and (
                    "local_retention_policy" in data
                    and getattr(data["local_retention_policy"], "pk", None)
                    != original.local_retention_policy_id
                    or "enforce_retention_policy" in data
                    and data["enforce_retention_policy"] != original.enforce_retention_policy
                )
            ),
            remote_changed=(
                original.protocol != "local"
                and (
                    "remote_retention_policy" in data
                    and getattr(data["remote_retention_policy"], "pk", None)
                    != original.remote_retention_policy_id
                    or "enforce_retention_policy" in data
                    and data["enforce_retention_policy"] != original.enforce_retention_policy
                )
            ),
        )
        return super().perform_update(serializer)

    def perform_destroy(self, instance):
        if instance.is_default or instance.protocol == "local":
            raise ValidationError({"detail": "The system default Local storage cannot be deleted."})
        return super().perform_destroy(instance)


class RevisionReplicaViewSet(NetBoxReadOnlyModelViewSet):
    queryset = RevisionReplica.objects.all()
    serializer_class = RevisionReplicaSerializer


class SftpReceiverProfileViewSet(NetBoxModelViewSet):
    queryset = SftpReceiverProfile.objects.all()
    serializer_class = SftpReceiverProfileSerializer


class PlatformMappingViewSet(NetBoxModelViewSet):
    queryset = PlatformMapping.objects.all()
    serializer_class = PlatformMappingSerializer


class BackupTargetViewSet(NetBoxReadOnlyModelViewSet):
    queryset = BackupTarget.objects.all()
    serializer_class = BackupTargetSerializer
    filterset_class = BackupTargetFilterSet


class ConfigRevisionViewSet(NetBoxReadOnlyModelViewSet):
    queryset = ConfigRevision.objects.all()
    serializer_class = ConfigRevisionSerializer


class BackupRunViewSet(NetBoxReadOnlyModelViewSet):
    queryset = BackupRun.objects.all()
    serializer_class = BackupRunSerializer
    filterset_class = BackupRunFilterSet
