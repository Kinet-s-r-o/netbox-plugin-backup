from django.urls import include, path
from utilities.urls import get_model_urls

from . import views

app_name = "netbox_config_backup"

urlpatterns = [
    path("", views.ConfigBackupHomeView.as_view(), name="home"),
    path("settings/", views.AdvancedSettingsView.as_view(), name="advanced_settings"),
    path("examples/", views.ExampleConfigurationView.as_view(), name="examples"),
    path("ssh-host-keys/", views.SSHHostKeyListView.as_view(), name="ssh_host_key_list"),
    path("ssh-host-keys/scan/", views.SSHHostKeyScanView.as_view(), name="ssh_host_key_scan"),
    path(
        "ssh-host-keys/<int:pk>/trust/",
        views.SSHHostKeyTrustView.as_view(),
        name="ssh_host_key_trust",
    ),
    path(
        "ssh-host-keys/<int:pk>/reject/",
        views.SSHHostKeyRejectView.as_view(),
        name="ssh_host_key_reject",
    ),
    path(
        "targets/",
        include(get_model_urls("netbox_config_backup", "backuptarget", detail=False)),
    ),
    path(
        "targets/<int:pk>/connection-test/<uuid:job_id>/",
        views.BackupTargetConnectionTestResultView.as_view(),
        name="backuptarget_connection_test_result",
    ),
    path(
        "targets/<int:pk>/connection-test/<uuid:job_id>/status/",
        views.BackupTargetConnectionTestStatusView.as_view(),
        name="backuptarget_connection_test_status",
    ),
    path(
        "targets/<int:pk>/connection-test/<uuid:job_id>/trust-host-key/",
        views.BackupTargetTrustHostKeyView.as_view(),
        name="backuptarget_trust_host_key",
    ),
    path(
        "targets/<int:pk>/connection-test/<uuid:job_id>/reject-host-key/",
        views.BackupTargetRejectHostKeyView.as_view(),
        name="backuptarget_reject_host_key",
    ),
    path(
        "targets/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "backuptarget")),
    ),
    path("runs/", include(get_model_urls("netbox_config_backup", "backuprun", detail=False))),
    path("runs/<int:pk>/", include(get_model_urls("netbox_config_backup", "backuprun"))),
    path(
        "revisions/",
        include(get_model_urls("netbox_config_backup", "configrevision", detail=False)),
    ),
    path(
        "revisions/<int:pk>/artifacts/<int:artifact_pk>/download/",
        views.ConfigRevisionArtifactDownloadView.as_view(),
        name="configrevision_artifact_download",
    ),
    path(
        "revisions/<int:pk>/ftp-recovery/<int:replica_pk>/prepare/",
        views.ConfigRevisionFtpRecoveryPrepareView.as_view(),
        name="configrevision_ftp_recovery_prepare",
    ),
    path(
        "revisions/<int:pk>/ftp-recovery/<uuid:job_id>/",
        views.ConfigRevisionFtpRecoveryResultView.as_view(),
        name="configrevision_ftp_recovery_result",
    ),
    path(
        "revisions/<int:pk>/ftp-recovery/<uuid:job_id>/status/",
        views.ConfigRevisionFtpRecoveryStatusView.as_view(),
        name="configrevision_ftp_recovery_status",
    ),
    path(
        "revisions/<int:pk>/ftp-recovery/<uuid:job_id>/download/",
        views.ConfigRevisionFtpRecoveryDownloadView.as_view(),
        name="configrevision_ftp_recovery_download",
    ),
    path(
        "revisions/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "configrevision")),
    ),
    path(
        "policies/",
        include(get_model_urls("netbox_config_backup", "backuppolicy", detail=False)),
    ),
    path(
        "policies/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "backuppolicy")),
    ),
    path(
        "retention-policies/",
        include(get_model_urls("netbox_config_backup", "retentionpolicy", detail=False)),
    ),
    path(
        "retention-policies/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "retentionpolicy")),
    ),
    path(
        "platform-mappings/",
        include(get_model_urls("netbox_config_backup", "platformmapping", detail=False)),
    ),
    path(
        "platform-mappings/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "platformmapping")),
    ),
    path(
        "connection-profiles/",
        include(get_model_urls("netbox_config_backup", "connectionprofile", detail=False)),
    ),
    path(
        "connection-profiles/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "connectionprofile")),
    ),
    path(
        "credential-profiles/",
        include(get_model_urls("netbox_config_backup", "credentialprofile", detail=False)),
    ),
    path(
        "credential-profiles/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "credentialprofile")),
    ),
    path(
        "destinations/",
        include(get_model_urls("netbox_config_backup", "backupdestination", detail=False)),
    ),
    path(
        "destinations/<int:pk>/test/<uuid:job_id>/",
        views.BackupDestinationTestResultView.as_view(),
        name="backupdestination_test_result",
    ),
    path(
        "destinations/<int:pk>/test/<uuid:job_id>/status/",
        views.BackupDestinationTestStatusView.as_view(),
        name="backupdestination_test_status",
    ),
    path(
        "destinations/<int:pk>/reconciliation/<uuid:job_id>/",
        views.BackupDestinationReconciliationResultView.as_view(),
        name="backupdestination_reconciliation_result",
    ),
    path(
        "destinations/<int:pk>/reconciliation/<uuid:job_id>/status/",
        views.BackupDestinationReconciliationStatusView.as_view(),
        name="backupdestination_reconciliation_status",
    ),
    path(
        "destinations/<int:pk>/test/<uuid:job_id>/trust-host-key/",
        views.BackupDestinationTrustHostKeyView.as_view(),
        name="backupdestination_trust_host_key",
    ),
    path(
        "destinations/<int:pk>/backfill/",
        views.BackupDestinationBackfillView.as_view(),
        name="backupdestination_backfill",
    ),
    path(
        "destinations/<int:pk>/replicas/<int:replica_pk>/retry/",
        views.RevisionReplicaRetryView.as_view(),
        name="revisionreplica_retry",
    ),
    path(
        "destinations/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "backupdestination")),
    ),
    path(
        "sftp-receivers/",
        include(get_model_urls("netbox_config_backup", "sftpreceiverprofile", detail=False)),
    ),
    path(
        "sftp-receivers/<int:pk>/",
        include(get_model_urls("netbox_config_backup", "sftpreceiverprofile")),
    ),
]
