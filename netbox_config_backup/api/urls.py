from netbox.api.routers import NetBoxRouter

from .views import (
    AvailableDeviceViewSet,
    BackupDestinationViewSet,
    BackupPolicyViewSet,
    BackupRunViewSet,
    BackupTargetViewSet,
    ConfigRevisionViewSet,
    ConnectionProfileViewSet,
    CredentialProfileViewSet,
    PlatformMappingViewSet,
    RetentionPolicyViewSet,
    RevisionReplicaViewSet,
    SftpReceiverProfileViewSet,
)

router = NetBoxRouter()
router.register("available-devices", AvailableDeviceViewSet, basename="available-device")
router.register("retention-policies", RetentionPolicyViewSet)
router.register("backup-policies", BackupPolicyViewSet)
router.register("connection-profiles", ConnectionProfileViewSet)
router.register("credential-profiles", CredentialProfileViewSet)
router.register("backup-destinations", BackupDestinationViewSet)
router.register("revision-replicas", RevisionReplicaViewSet)
router.register("sftp-receivers", SftpReceiverProfileViewSet)
router.register("platform-mappings", PlatformMappingViewSet)
router.register("backup-targets", BackupTargetViewSet)
router.register("config-revisions", ConfigRevisionViewSet)
router.register("backup-runs", BackupRunViewSet)

urlpatterns = router.urls
