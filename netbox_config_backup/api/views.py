from dcim.api.serializers import DeviceSerializer
from dcim.filtersets import DeviceFilterSet
from dcim.models import Device
from netbox.api.viewsets import NetBoxModelViewSet, NetBoxReadOnlyModelViewSet

from netbox_config_backup.filtersets import BackupRunFilterSet, BackupTargetFilterSet
from netbox_config_backup.models import (
    BackupDestination,
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    ConnectionProfile,
    CredentialProfile,
    PlatformMapping,
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
    RetentionPolicySerializer,
    RevisionReplicaSerializer,
    SftpReceiverProfileSerializer,
)


class RetentionPolicyViewSet(NetBoxModelViewSet):
    queryset = RetentionPolicy.objects.all()
    serializer_class = RetentionPolicySerializer


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


class RevisionReplicaViewSet(NetBoxReadOnlyModelViewSet):
    queryset = RevisionReplica.objects.all()
    serializer_class = RevisionReplicaSerializer


class SftpReceiverProfileViewSet(NetBoxModelViewSet):
    queryset = SftpReceiverProfile.objects.all()
    serializer_class = SftpReceiverProfileSerializer


class PlatformMappingViewSet(NetBoxModelViewSet):
    queryset = PlatformMapping.objects.all()
    serializer_class = PlatformMappingSerializer


class BackupTargetViewSet(NetBoxModelViewSet):
    queryset = BackupTarget.objects.all()
    serializer_class = BackupTargetSerializer
    filterset_class = BackupTargetFilterSet


class ConfigRevisionViewSet(NetBoxModelViewSet):
    queryset = ConfigRevision.objects.all()
    serializer_class = ConfigRevisionSerializer


class BackupRunViewSet(NetBoxModelViewSet):
    queryset = BackupRun.objects.all()
    serializer_class = BackupRunSerializer
    filterset_class = BackupRunFilterSet
