from __future__ import annotations

import logging
from collections.abc import Iterable

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("netbox_config_backup.metrics")

TARGET_STATES = ("healthy", "stale", "failed", "never", "disabled")
RUN_STATES = (
    "queued",
    "running",
    "success_unchanged",
    "success_changed",
    "partial",
    "failed",
    "errored",
    "skipped",
)
REPLICA_STATES = ("pending", "queued", "running", "success", "failed")
DESTINATION_STATES = ("ready", "failed", "unverified", "disabled")
KNOWN_ERROR_CODES = frozenset(
    {
        "AUTH_FAILED",
        "COMMAND_REJECTED",
        "CONFIG_TOO_LARGE",
        "CONNECTION_FAILED",
        "CONNECTION_TIMEOUT",
        "DNS_FAILED",
        "EMPTY_CONFIG",
        "EXPORT_NOT_CONFIRMED",
        "HOST_KEY_FAILED",
        "HOST_KEY_UNKNOWN",
        "INCOMPLETE_CONFIG",
        "INTERNAL_ERROR",
        "INVALID_OUTPUT",
        "NO_CREDENTIAL_PROFILE",
        "NO_RECEIVER_CREDENTIALS",
        "RECEIVER_UPLOAD_TIMEOUT",
        "SECRET_RESOLUTION_FAILED",
        "STALE_RUN",
        "STORAGE_FAILED",
        "TIMEOUT",
        "UNSUPPORTED_PLATFORM",
        "VALIDATION_FAILED",
    }
)

_collector = None


def normalize_error_code(value: str) -> str:
    """Keep the Prometheus label set bounded even for third-party drivers."""

    return value if value in KNOWN_ERROR_CODES else "other"


def register_metrics_collector() -> None:
    global _collector
    if _collector is not None:
        return
    try:
        from prometheus_client import REGISTRY
    except ImportError:
        logger.warning("prometheus_client is unavailable; backup metrics are disabled.")
        return

    _collector = ConfigBackupCollector()
    REGISTRY.register(_collector)


class ConfigBackupCollector:
    """DB-backed gauges which remain correct across NetBox worker processes."""

    def describe(self) -> Iterable:
        """Describe names without touching Django's DB during app initialization."""

        from prometheus_client.core import GaugeMetricFamily

        yield GaugeMetricFamily(
            "netbox_config_backup_targets",
            "Current number of configuration backup targets by health state.",
            labels=["status"],
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_runs",
            "Retained configuration backup runs by state.",
            labels=["status"],
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_failure_runs",
            "Retained failed configuration backup runs by bounded error code.",
            labels=["error_code"],
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_stuck_runs",
            "Current number of queued or running backup runs past their timeout.",
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_revisions",
            "Current number of retained configuration revisions.",
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_artifact_bytes",
            "Logical bytes referenced by retained configuration artifacts.",
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_last_success_timestamp_seconds",
            "Unix timestamp of the most recent successful backup, or zero if none exists.",
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_replica_destinations",
            "Current number of FTP storages by health state.",
            labels=["status"],
        )
        yield GaugeMetricFamily(
            "netbox_config_backup_revision_replicas",
            "Current number of external revision copies by state.",
            labels=["status"],
        )

    def collect(self) -> Iterable:
        if not self._enabled():
            return
        try:
            yield from self._collect_database_metrics()
        except Exception:
            # A monitoring scrape must never make the NetBox /metrics endpoint fail.
            logger.exception("Could not collect NetBox Config Backup metrics.")

    @staticmethod
    def _enabled() -> bool:
        try:
            from django.conf import settings

            return bool(
                settings.PLUGINS_CONFIG["netbox_config_backup"].get("metrics_enabled", False)
            )
        except (AttributeError, ImproperlyConfigured, KeyError, TypeError):
            return False

    @staticmethod
    def _collect_database_metrics() -> Iterable:
        from django.conf import settings
        from django.db.models import Count, Max, Sum
        from django.utils import timezone
        from prometheus_client.core import GaugeMetricFamily

        from netbox_config_backup.models import (
            BackupDestination,
            BackupRun,
            BackupTarget,
            ConfigArtifact,
            ConfigRevision,
            RevisionReplica,
        )
        from netbox_config_backup.services.health import stuck_run_queryset

        target_counts = dict(BackupTarget.objects.values_list("status").annotate(total=Count("pk")))
        targets = GaugeMetricFamily(
            "netbox_config_backup_targets",
            "Current number of configuration backup targets by health state.",
            labels=["status"],
        )
        for state in TARGET_STATES:
            targets.add_metric([state], target_counts.get(state, 0))
        yield targets

        run_counts = dict(BackupRun.objects.values_list("status").annotate(total=Count("pk")))
        runs = GaugeMetricFamily(
            "netbox_config_backup_runs",
            "Retained configuration backup runs by state.",
            labels=["status"],
        )
        for state in RUN_STATES:
            runs.add_metric([state], run_counts.get(state, 0))
        yield runs

        failure_counts: dict[str, int] = {}
        for row in (
            BackupRun.objects.exclude(error_code="")
            .values("error_code")
            .annotate(total=Count("pk"))
        ):
            code = normalize_error_code(row["error_code"])
            failure_counts[code] = failure_counts.get(code, 0) + row["total"]
        failures = GaugeMetricFamily(
            "netbox_config_backup_failure_runs",
            "Retained failed configuration backup runs by bounded error code.",
            labels=["error_code"],
        )
        for code, count in sorted(failure_counts.items()):
            failures.add_metric([code], count)
        yield failures

        stale_minutes = settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_run_minutes"]
        stuck = GaugeMetricFamily(
            "netbox_config_backup_stuck_runs",
            "Current number of queued or running backup runs past their timeout.",
        )
        stuck.add_metric(
            [],
            stuck_run_queryset(
                BackupRun.objects.all(),
                now=timezone.now(),
                timeout_minutes=stale_minutes,
            ).count(),
        )
        yield stuck

        revisions = GaugeMetricFamily(
            "netbox_config_backup_revisions",
            "Current number of retained configuration revisions.",
        )
        revisions.add_metric([], ConfigRevision.objects.count())
        yield revisions

        artifact_bytes = GaugeMetricFamily(
            "netbox_config_backup_artifact_bytes",
            "Logical bytes referenced by retained configuration artifacts.",
        )
        artifact_bytes.add_metric(
            [],
            ConfigArtifact.objects.filter(local_available=True).aggregate(total=Sum("size"))[
                "total"
            ]
            or 0,
        )
        yield artifact_bytes

        latest_success = GaugeMetricFamily(
            "netbox_config_backup_last_success_timestamp_seconds",
            "Unix timestamp of the most recent successful backup, or zero if none exists.",
        )
        latest = BackupTarget.objects.aggregate(value=Max("last_success_at"))["value"]
        latest_success.add_metric([], latest.timestamp() if latest else 0)
        yield latest_success

        destination_counts = {state: 0 for state in DESTINATION_STATES}
        for destination in BackupDestination.objects.filter(protocol="ftp").only(
            "enabled", "last_error_code"
        ):
            if not destination.enabled:
                state = "disabled"
            elif destination.last_error_code:
                state = "failed"
            else:
                state = "ready"
            destination_counts[state] += 1
        destinations = GaugeMetricFamily(
            "netbox_config_backup_replica_destinations",
            "Current number of FTP storages by health state.",
            labels=["status"],
        )
        for state in DESTINATION_STATES:
            destinations.add_metric([state], destination_counts[state])
        yield destinations

        replica_counts = dict(
            RevisionReplica.objects.filter(remote_deleted_at__isnull=True)
            .values_list("status")
            .annotate(total=Count("pk"))
        )
        replicas = GaugeMetricFamily(
            "netbox_config_backup_revision_replicas",
            "Current number of external revision copies by state.",
            labels=["status"],
        )
        for state in REPLICA_STATES:
            replicas.add_metric([state], replica_counts.get(state, 0))
        yield replicas
