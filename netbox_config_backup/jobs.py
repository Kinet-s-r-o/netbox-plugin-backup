from dataclasses import asdict

from core.exceptions import JobFailed
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from netbox.jobs import JobRunner, system_job

from .choices import DestinationProtocolChoices
from .drivers.base import DriverError
from .models import BackupDestination, OperationalSettings, RevisionReplica
from .services.destination import DestinationError, reconcile_destination, test_destination
from .services.destination_sftp import scan_destination_host_key
from .services.dispatcher import dispatch_due_targets, reconcile_stale_runs
from .services.ftp_audit_dispatcher import dispatch_due_ftp_audits
from .services.ftp_recovery import (
    build_ftp_recovery_package,
    cleanup_expired_recovery_packages,
)
from .services.health import refresh_target_health
from .services.remote_retention_cleanup import (
    RemoteRetentionCleanupError,
    execute_remote_retention_cleanup,
)
from .services.remote_retention_dispatcher import dispatch_expired_remote_targets
from .services.replication import (
    dispatch_due_replicas,
    execute_revision_replica,
    reconcile_stale_replicas,
)
from .services.retention_cleanup import RetentionCleanupError, execute_retention_cleanup
from .services.retention_dispatcher import dispatch_expired_targets
from .services.runtime import build_backup_pipeline, build_connection_tester
from .services.ssh_host_keys import scan_target_host_key

BACKUP_QUEUE = "netbox_config_backup.backup"


class BackupRunJob(JobRunner):
    class Meta:
        name = "Config backup"

    def run(self, *args, **kwargs):
        run_id = kwargs["run_id"]
        self.logger.info(f"Starting configuration backup run {run_id}.")
        result = build_backup_pipeline().execute(run_id)
        self.logger.info(
            f"Backup run {run_id} finished with status {result.status} (changed={result.changed})."
        )
        if result.status in {"failed", "errored"}:
            raise JobFailed(f"Configuration backup run {run_id} failed: {result.error_code}")
        return result.status


class ConnectionTestJob(JobRunner):
    class Meta:
        name = "Config backup connection test"

    def run(self, *args, **kwargs):
        target_id = kwargs["target_id"]
        self.logger.info(f"Starting connection test for backup target {target_id}.")
        result = build_connection_tester().execute(target_id)
        connection_test_data = {
            "success": result.success,
            "driver_id": result.driver_id,
            "artifact_count": result.artifact_count,
            "total_bytes": result.total_bytes,
            "error_code": result.error_code,
            "safe_message": result.safe_message,
        }
        if not result.success and result.error_code in {
            "HOST_KEY_UNKNOWN",
            "HOST_KEY_MISMATCH",
            "HOST_KEY_FAILED",
        }:
            try:
                candidate = scan_target_host_key(target_id)
                connection_test_data["host_key_candidate"] = candidate.as_dict()
                self.logger.info(
                    "Discovered SSH host key candidate %s for target %s.",
                    candidate.candidate_id,
                    target_id,
                )
            except DriverError as exc:
                connection_test_data["host_key_scan_error"] = {
                    "error_code": exc.error_code,
                    "safe_message": exc.safe_message,
                }
        self.job.data = {"connection_test": connection_test_data}
        self.job.save(update_fields=("data",))
        if not result.success:
            self.logger.error(
                f"Connection test failed with code {result.error_code}: {result.safe_message}"
            )
            raise JobFailed(f"Connection test failed: {result.error_code}")
        self.logger.info(
            f"Connection test succeeded with driver {result.driver_id}; "
            f"validated {result.artifact_count} artifact(s), {result.total_bytes} bytes."
        )
        return {
            "success": True,
            "driver_id": result.driver_id,
            "artifact_count": result.artifact_count,
            "total_bytes": result.total_bytes,
        }


class SSHHostKeyScanJob(JobRunner):
    class Meta:
        name = "Scan config backup SSH identities"

    def run(self, *args, **kwargs):
        target_ids = tuple(dict.fromkeys(kwargs.get("target_ids") or ()))[:1000]
        summary = {"scanned": 0, "pending": 0, "trusted": 0, "skipped": 0, "failed": 0}
        for target_id in target_ids:
            try:
                candidate = scan_target_host_key(target_id)
                summary["scanned"] += 1
                if candidate.status == "trusted":
                    summary["trusted"] += 1
                else:
                    summary["pending"] += 1
            except DriverError as exc:
                if exc.error_code == "NOT_SSH":
                    summary["skipped"] += 1
                    continue
                summary["failed"] += 1
                self.logger.warning(
                    "SSH identity scan for target %s failed with code %s.",
                    target_id,
                    exc.error_code,
                )
        self.logger.info(
            "SSH identity scan completed: scanned=%s pending=%s trusted=%s skipped=%s failed=%s.",
            summary["scanned"],
            summary["pending"],
            summary["trusted"],
            summary["skipped"],
            summary["failed"],
        )
        return summary


class DestinationConnectionTestJob(JobRunner):
    class Meta:
        name = "Config backup external destination test"

    def run(self, *args, **kwargs):
        destination_id = kwargs["destination_id"]
        destination = BackupDestination.objects.select_related("credential_profile").get(
            pk=destination_id
        )
        self.logger.info(
            "Starting %s destination test for %s.",
            destination.protocol.upper(),
            destination.name,
        )
        candidate = None
        try:
            if destination.protocol == DestinationProtocolChoices.SFTP:
                candidate = scan_destination_host_key(destination)
                if not destination.host_key_is_trusted:
                    raise DestinationError(
                        "HOST_KEY_UNKNOWN",
                        "Verify and approve the SFTP server fingerprint before the first connection.",
                    )
            result = test_destination(destination)
            result["protocol"] = destination.protocol
        except DestinationError as exc:
            now = timezone.now()
            BackupDestination.objects.filter(pk=destination.pk).update(
                last_tested_at=now,
                last_error_code=exc.error_code,
                last_error_message=exc.safe_message,
                last_updated=now,
            )
            self.job.data = {
                "destination_test": {
                    "success": False,
                    "error_code": exc.error_code,
                    "safe_message": exc.safe_message,
                    "host_key_candidate": candidate.as_dict() if candidate else None,
                    "protocol": destination.protocol,
                }
            }
            self.job.save(update_fields=("data",))
            raise JobFailed(
                f"{destination.protocol.upper()} destination test failed: {exc.error_code}"
            ) from exc

        now = timezone.now()
        BackupDestination.objects.filter(pk=destination.pk).update(
            last_tested_at=now,
            last_error_code="",
            last_error_message="",
            last_updated=now,
        )
        self.job.data = {"destination_test": result}
        self.job.save(update_fields=("data",))
        self.logger.info(
            "%s destination test for %s succeeded.",
            destination.protocol.upper(),
            destination.name,
        )
        return result


class DestinationReconciliationJob(JobRunner):
    class Meta:
        name = "Config backup FTP integrity audit"

    def run(self, *args, **kwargs):
        destination_id = kwargs["destination_id"]
        destination = BackupDestination.objects.select_related("credential_profile").get(
            pk=destination_id
        )
        previous_status = destination.last_integrity_audit_status
        self.logger.info("Starting read-only FTP integrity audit for %s.", destination.name)
        try:
            result = reconcile_destination(destination)
        except DestinationError as exc:
            now = timezone.now()
            BackupDestination.objects.filter(pk=destination.pk).update(
                last_integrity_audit_at=now,
                last_integrity_audit_status="failed",
                last_integrity_audit_problem_count=0,
                last_updated=now,
            )
            result = {
                "success": False,
                "error_code": exc.error_code,
                "safe_message": exc.safe_message,
                "protocol": destination.protocol,
            }
            self.job.data = {"destination_reconciliation": result}
            self.job.save(update_fields=("data",))
            if previous_status not in {"failed", "problems"}:
                from netbox_config_backup.events import (
                    FTP_AUDIT_FAILED,
                    queue_destination_event,
                )

                queue_destination_event(FTP_AUDIT_FAILED, destination.pk)
            self.logger.error(
                "FTP integrity audit for %s failed with code %s.",
                destination.name,
                exc.error_code,
            )
            raise JobFailed(f"FTP integrity audit failed: {exc.error_code}") from exc

        self.job.data = {"destination_reconciliation": result}
        self.job.save(update_fields=("data",))
        problem_count = sum(
            result.get(key, 0)
            for key in (
                "missing_files",
                "size_mismatches",
                "hash_mismatches",
                "unreadable_files",
            )
        )
        now = timezone.now()
        BackupDestination.objects.filter(pk=destination.pk).update(
            last_integrity_audit_at=now,
            last_integrity_audit_status=("healthy" if result["success"] else "problems"),
            last_integrity_audit_problem_count=problem_count,
            last_updated=now,
        )
        if result["success"] and previous_status in {"failed", "problems"}:
            from netbox_config_backup.events import (
                FTP_AUDIT_RECOVERED,
                queue_destination_event,
            )

            queue_destination_event(FTP_AUDIT_RECOVERED, destination.pk)
        elif not result["success"] and previous_status not in {"failed", "problems"}:
            from netbox_config_backup.events import (
                FTP_AUDIT_FAILED,
                queue_destination_event,
            )

            queue_destination_event(FTP_AUDIT_FAILED, destination.pk)
        log = self.logger.info if result["success"] else self.logger.warning
        log(
            "FTP integrity audit for %s completed: revisions=%s files=%s problems=%s.",
            destination.name,
            result["checked_replicas"],
            result["checked_files"],
            result["failed_replicas"],
        )
        return result


class FtpRecoveryPackageJob(JobRunner):
    class Meta:
        name = "Prepare verified FTP recovery package"

    def run(self, *args, **kwargs):
        replica_id = kwargs["replica_id"]
        package_token = kwargs["package_token"]
        replica = (
            RevisionReplica.objects.select_related(
                "destination__credential_profile",
                "revision__target__device",
            )
            .prefetch_related("revision__artifacts")
            .get(pk=replica_id)
        )
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        self.logger.info("Preparing a read-only verified FTP package for replica %s.", replica_id)
        try:
            result = build_ftp_recovery_package(
                replica,
                storage_root=plugin_settings["storage_root"],
                package_token=package_token,
                ttl_minutes=plugin_settings["recovery_package_ttl_minutes"],
                max_total_bytes=plugin_settings["recovery_package_max_bytes"],
                now=timezone.now(),
            )
        except DestinationError as exc:
            self.job.data = {
                "ftp_recovery_package": {
                    "ready": False,
                    "replica_id": replica_id,
                    "revision_id": replica.revision_id,
                    "error_code": exc.error_code,
                    "safe_message": exc.safe_message,
                }
            }
            self.job.save(update_fields=("data",))
            self.logger.error(
                "Verified FTP package for replica %s failed with code %s.",
                replica_id,
                exc.error_code,
            )
            raise JobFailed(f"Verified FTP package failed: {exc.error_code}") from exc

        payload = result.as_dict()
        payload["download_count"] = 0
        payload["downloads"] = []
        self.job.data = {"ftp_recovery_package": payload}
        self.job.save(update_fields=("data",))
        self.logger.info(
            "Verified FTP package for replica %s is ready: files=%s bytes=%s.",
            replica_id,
            result.file_count,
            result.verified_bytes,
        )
        return payload


class DestinationReplicationJob(JobRunner):
    class Meta:
        name = "Config backup external replication"

    def run(self, *args, **kwargs):
        replica_id = kwargs["replica_id"]
        self.logger.info("Starting revision replica %s.", replica_id)
        try:
            result = execute_revision_replica(replica_id)
        except DestinationError as exc:
            self.logger.error(
                "Revision replica %s failed with code %s.", replica_id, exc.error_code
            )
            raise JobFailed(f"External replication failed: {exc.error_code}") from exc
        self.logger.info(
            "Revision replica %s completed: artifacts=%s transferred=%s bytes.",
            replica_id,
            result.artifact_count,
            result.bytes_transferred,
        )
        return {
            "remote_path": result.remote_path,
            "artifact_count": result.artifact_count,
            "bytes_transferred": result.bytes_transferred,
        }


class RetentionCleanupJob(JobRunner):
    class Meta:
        name = "Config backup retention cleanup"

    def run(self, *args, **kwargs):
        target_id = kwargs["target_id"]
        self.logger.info(f"Starting retention cleanup for backup target {target_id}.")
        try:
            summary = execute_retention_cleanup(target_id)
        except RetentionCleanupError as exc:
            self.logger.error(str(exc))
            raise JobFailed(str(exc)) from exc
        self.logger.info(
            f"Retention cleanup for target {target_id} deleted {summary.revision_count} "
            f"revision(s), {summary.run_count} run(s), and {summary.artifact_count} "
            f"artifact(s) totaling {summary.artifact_bytes} bytes."
        )
        if summary.missing_artifact_count:
            self.logger.warning(
                f"{summary.missing_artifact_count} artifact file(s) were already missing."
            )
        if summary.quarantine_purge_failures:
            self.logger.warning(
                f"{summary.quarantine_purge_failures} quarantined artifact file(s) "
                "could not be purged and require storage housekeeping."
            )
        return {
            "target_id": summary.target_id,
            "run_count": summary.run_count,
            "revision_count": summary.revision_count,
            "artifact_count": summary.artifact_count,
            "artifact_bytes": summary.artifact_bytes,
            "missing_artifact_count": summary.missing_artifact_count,
            "quarantine_purge_failures": summary.quarantine_purge_failures,
            "deferred_revision_count": summary.deferred_revision_count,
        }


class RemoteRetentionCleanupJob(JobRunner):
    class Meta:
        name = "Config backup FTP retention cleanup"

    def run(self, *args, **kwargs):
        target_id = kwargs["target_id"]
        self.logger.info("Starting FTP retention cleanup for backup target %s.", target_id)
        try:
            summary = execute_remote_retention_cleanup(target_id)
        except RemoteRetentionCleanupError as exc:
            self.logger.error(str(exc))
            raise JobFailed(str(exc)) from exc
        self.logger.info(
            "FTP retention for target %s expired %s revision(s), deleted %s file(s) "
            "(%s bytes), cancelled %s incomplete replica(s), and deferred %s active revision(s).",
            target_id,
            summary.revision_count,
            summary.deleted_file_count,
            summary.deleted_bytes,
            summary.cancelled_replica_count,
            summary.deferred_revision_count,
        )
        return {
            "target_id": summary.target_id,
            "revision_count": summary.revision_count,
            "replica_count": summary.replica_count,
            "cancelled_replica_count": summary.cancelled_replica_count,
            "deleted_file_count": summary.deleted_file_count,
            "missing_file_count": summary.missing_file_count,
            "deleted_bytes": summary.deleted_bytes,
            "removed_directory_count": summary.removed_directory_count,
            "deferred_revision_count": summary.deferred_revision_count,
            "metadata_revision_count": summary.metadata_revision_count,
        }


@system_job(interval=1)
class ScheduledBackupDispatcherJob(JobRunner):
    class Meta:
        name = "Scheduled config backup dispatcher"

    def run(self, *args, **kwargs):
        now = timezone.now()
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        reconciled = reconcile_stale_runs(
            now=now,
            stale_after_minutes=plugin_settings["stale_run_minutes"],
        )
        summary = dispatch_due_targets(
            now=now,
            limit=plugin_settings["dispatcher_batch_size"],
        )
        health = refresh_target_health(
            now=now,
            grace_minutes=plugin_settings["stale_target_grace_minutes"],
        )
        self.logger.info(
            f"Config backup dispatcher: initialized={summary.initialized} "
            f"due={summary.due} queued={summary.queued} "
            f"active={summary.skipped_active} conflicts={summary.conflicts} "
            f"reconciled={reconciled} health_updated={health.updated} "
            f"stale_targets={health.stale}."
        )


@system_job(interval=1440)
class ScheduledRetentionDispatcherJob(JobRunner):
    class Meta:
        name = "Scheduled config backup retention dispatcher"

    def run(self, *args, **kwargs):
        try:
            operational_settings = OperationalSettings.objects.filter(singleton=True).first()
        except DatabaseError:
            operational_settings = None
        local_enabled = bool(
            operational_settings and operational_settings.retention_scheduler_enabled
        )
        remote_enabled = bool(
            operational_settings and operational_settings.remote_retention_scheduler_enabled
        )
        if not local_enabled and not remote_enabled:
            self.logger.info("Automatic local and FTP retention cleanup are disabled.")
            return {"enabled": False, "queued": 0}

        limit = operational_settings.retention_scheduler_batch_size

        now = timezone.now()
        summary = dispatch_expired_targets(now=now, limit=limit) if local_enabled else None
        remote_summary = (
            dispatch_expired_remote_targets(now=now, limit=limit) if remote_enabled else None
        )
        if summary:
            self.logger.info(
                f"Local retention dispatcher: considered={summary.considered} "
                f"expired={summary.expired} queued={summary.queued} "
                f"active_backups={summary.skipped_active_backup} "
                f"active_cleanups={summary.skipped_active_cleanup} "
                f"conflicts={summary.conflicts}."
            )
        if remote_summary:
            self.logger.info(
                f"FTP retention dispatcher: considered={remote_summary.considered} "
                f"expired={remote_summary.expired} queued={remote_summary.queued} "
                f"active_backups={remote_summary.skipped_active_backup} "
                f"active_cleanups={remote_summary.skipped_active_cleanup} "
                f"conflicts={remote_summary.conflicts}."
            )
        return {
            "enabled": True,
            "local_enabled": local_enabled,
            "remote_enabled": remote_enabled,
            "local": asdict(summary) if summary else None,
            "remote": asdict(remote_summary) if remote_summary else None,
            "queued": (summary.queued if summary else 0)
            + (remote_summary.queued if remote_summary else 0),
        }


@system_job(interval=1)
class ScheduledReplicationDispatcherJob(JobRunner):
    class Meta:
        name = "Scheduled config backup FTP replication dispatcher"

    def run(self, *args, **kwargs):
        reconciled = reconcile_stale_replicas(
            stale_after_minutes=settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_run_minutes"]
        )
        summary = dispatch_due_replicas(limit=100)
        if summary.considered or reconciled:
            self.logger.info(
                "FTP replica dispatcher: considered=%s queued=%s skipped=%s reconciled=%s.",
                summary.considered,
                summary.queued,
                summary.skipped,
                reconciled,
            )
        return {
            "considered": summary.considered,
            "queued": summary.queued,
            "skipped": summary.skipped,
            "reconciled": reconciled,
        }


@system_job(interval=1)
class ScheduledFtpIntegrityAuditDispatcherJob(JobRunner):
    class Meta:
        name = "Scheduled config backup FTP integrity audit dispatcher"

    def run(self, *args, **kwargs):
        summary = dispatch_due_ftp_audits(limit=25)
        if summary.initialized or summary.due:
            self.logger.info(
                "FTP integrity audit dispatcher: initialized=%s due=%s queued=%s "
                "active=%s conflicts=%s.",
                summary.initialized,
                summary.due,
                summary.queued,
                summary.skipped_active,
                summary.conflicts,
            )
        return {
            "initialized": summary.initialized,
            "due": summary.due,
            "queued": summary.queued,
            "skipped_active": summary.skipped_active,
            "conflicts": summary.conflicts,
        }


@system_job(interval=60)
class ScheduledRecoveryPackageCleanupJob(JobRunner):
    class Meta:
        name = "Scheduled config backup recovery package cleanup"

    def run(self, *args, **kwargs):
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        try:
            summary = cleanup_expired_recovery_packages(
                storage_root=plugin_settings["storage_root"],
                ttl_minutes=plugin_settings["recovery_package_ttl_minutes"],
                now=timezone.now(),
            )
        except DestinationError as exc:
            self.logger.warning(
                "Temporary recovery package cleanup could not run: %s.", exc.error_code
            )
            return {"deleted": 0, "failed": 1, "error_code": exc.error_code}
        if summary["deleted"] or summary["failed"]:
            self.logger.info(
                "Temporary recovery package cleanup: deleted=%s failed=%s.",
                summary["deleted"],
                summary["failed"],
            )
        return summary
