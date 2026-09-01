import io
import logging
from pathlib import Path
from uuid import UUID, uuid4

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from dcim.models import Device
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Prefetch, Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, TemplateView, View
from netbox.object_actions import AddObject, BulkExport
from netbox.views import generic
from utilities.forms import BulkDeleteForm, DeleteForm
from utilities.jobs import is_background_request
from utilities.rqworker import any_workers_for_queue
from utilities.views import ViewTab, register_model_view

from . import filtersets, forms, tables
from .choices import (
    MANAGED_DESTINATION_PROTOCOLS,
    REPLICATED_DESTINATION_PROTOCOLS,
    DestinationProtocolChoices,
    InterfaceLanguageChoices,
    ReplicaStatusChoices,
    RunSourceChoices,
    RunStatusChoices,
    SSHHostKeyPolicyChoices,
)
from .drivers import driver_registry
from .jobs import (
    BACKUP_QUEUE,
    ConnectionTestJob,
    DestinationConnectionTestJob,
    DestinationReconciliationJob,
    FtpRecoveryPackageJob,
    RemoteRetentionCleanupJob,
    RetentionCleanupJob,
    SSHHostKeyScanJob,
)
from .models import (
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
)
from .services.connection_test_status import connection_test_status_payload
from .services.destination_reconciliation_status import (
    destination_reconciliation_status_payload,
)
from .services.destination_test_status import destination_test_status_payload
from .services.destination_types import DestinationError
from .services.examples import (
    create_or_reset_example_configuration,
    get_example_configuration,
)
from .services.ftp_helpers import readable_artifact_filename
from .services.ftp_recovery import (
    recovery_package_is_expired,
    validate_recovery_package,
)
from .services.ftp_recovery_status import ftp_recovery_status_payload
from .services.health import (
    FAILURE_RUN_STATUSES,
    evaluate_target_health,
    is_run_stuck,
    stuck_run_queryset,
)
from .services.queueing import enqueue_backup_run
from .services.quick_setup import create_quick_setup
from .services.replication import backfill_destination, enqueue_revision_replica
from .services.reporting_period import resolve_reporting_period
from .services.retention import (
    RevisionCandidate,
    RunCandidate,
    build_retention_plan,
    effective_local_retention_policy,
    effective_remote_retention_policy,
    effective_retention_policy,
    local_retention_policy_source,
    remote_retention_policy_source,
    settings_from_policy,
    settings_from_remote_policy,
)
from .services.revision_deletion import (
    RevisionDeletionError,
    delete_config_revision_everywhere,
)
from .services.revision_display import (
    RevisionDisplayError,
    build_display_diff,
    load_artifact_content,
    load_revision_content,
)
from .services.run_cancellation import (
    BackupRunCancellationError,
    cancel_queued_backup_run,
)
from .services.runtime_controls import get_runtime_controls
from .services.scheduling import apply_target_schedule
from .services.ssh_host_keys import reject_host_key, trust_host_key
from .services.target_deletion import TargetDeletionError, delete_backup_target
from .services.ui_language import SESSION_KEY, resolve_ui_language

SETTINGS_STYLESHEET_PATH = (
    Path(__file__).resolve().parent / "static" / "netbox_config_backup" / "settings.css"
)


class SettingsStylesheetView(View):
    """Serve the small plugin stylesheet when global collectstatic is incomplete."""

    def get(self, request):
        try:
            content = SETTINGS_STYLESHEET_PATH.read_bytes()
        except OSError as exc:
            raise Http404("Config Backup stylesheet is unavailable.") from exc
        response = HttpResponse(content, content_type="text/css; charset=utf-8")
        response["Cache-Control"] = "public, max-age=300"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def _connection_test_job(target, job_id):
    return get_object_or_404(ConnectionTestJob.get_jobs(target), job_id=job_id)


def _backup_worker_available() -> bool | None:
    """Return the dedicated backup worker state, or None when Redis is unavailable."""
    try:
        return any_workers_for_queue(BACKUP_QUEUE)
    except Exception:
        logging.getLogger("netbox_config_backup.views").exception(
            "Could not inspect the config backup worker."
        )
        return None


def _connection_test_status(job, target):
    payload = connection_test_status_payload(job)
    candidate = payload.get("host_key_candidate")
    if candidate and candidate.get("id"):
        current_status = (
            SSHHostKey.objects.filter(pk=candidate["id"], target=target)
            .values_list("status", flat=True)
            .first()
        )
        if current_status:
            candidate["status"] = current_status
    return payload


class ConfigBackupHomeView(PermissionRequiredMixin, LoginRequiredMixin, TemplateView):
    template_name = "netbox_config_backup/home.html"
    permission_required = "netbox_config_backup.view_backuptarget"
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        reporting_period = resolve_reporting_period(self.request.GET, now=now)
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        targets = BackupTarget.objects.restrict(self.request.user, "view")
        visible_target_ids = targets.values("pk")
        runs = BackupRun.objects.restrict(self.request.user, "view").filter(
            target_id__in=visible_target_ids
        )
        revisions = ConfigRevision.objects.restrict(self.request.user, "view").filter(
            target_id__in=visible_target_ids
        )
        destinations = BackupDestination.objects.restrict(self.request.user, "view").filter(
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS
        )
        visible_destination_ids = destinations.values("pk")
        replicas = RevisionReplica.objects.restrict(self.request.user, "view").filter(
            destination_id__in=visible_destination_ids,
            remote_deleted_at__isnull=True,
        )
        period_runs = reporting_period.filter(runs, "queued_at")
        period_revisions = reporting_period.filter(revisions, "created")
        period_replicas = reporting_period.filter(replicas, "finished_at")
        ftp_attention = Q(last_error_code__gt="") | Q(
            last_integrity_audit_status__in=("failed", "problems")
        )
        enabled_destinations = destinations.filter(enabled=True)
        target_status_counts = {
            row["status"]: row["count"]
            for row in targets.values("status").annotate(count=Count("pk"))
        }
        recent_failures = period_runs.filter(status__in=FAILURE_RUN_STATUSES).select_related(
            "target__device"
        )[:10]
        context.update(
            {
                "target_count": targets.count(),
                "enabled_target_count": targets.filter(enabled=True).count(),
                "healthy_target_count": target_status_counts.get("healthy", 0),
                "stale_target_count": target_status_counts.get("stale", 0),
                "failed_target_count": target_status_counts.get("failed", 0),
                "never_target_count": target_status_counts.get("never", 0),
                "disabled_target_count": target_status_counts.get("disabled", 0),
                "scheduled_target_count": targets.filter(
                    enabled=True, next_run_at__isnull=False
                ).count(),
                "due_target_count": targets.filter(
                    enabled=True, next_run_at__lte=timezone.now()
                ).count(),
                "stuck_run_count": stuck_run_queryset(
                    runs,
                    now=now,
                    timeout_minutes=plugin_settings["stale_run_minutes"],
                ).count(),
                "stuck_run_minutes": plugin_settings["stale_run_minutes"],
                "stale_target_grace_minutes": plugin_settings["stale_target_grace_minutes"],
                "reporting_period": reporting_period,
                "revision_count": period_revisions.count(),
                "recent_runs": period_runs.select_related("target__device", "revision")[:10],
                "recent_failures": recent_failures,
                "recent_revisions": period_revisions.select_related("target__device")[:10],
                "ftp_destination_count": destinations.count(),
                "ftp_enabled_destination_count": enabled_destinations.count(),
                "ftp_healthy_destination_count": enabled_destinations.exclude(
                    ftp_attention
                ).count(),
                "ftp_attention_destination_count": enabled_destinations.filter(
                    ftp_attention
                ).count(),
                "ftp_automatic_audit_count": enabled_destinations.filter(
                    integrity_audit_enabled=True
                ).count(),
                "ftp_next_audit_at": enabled_destinations.filter(
                    integrity_audit_enabled=True
                ).aggregate(next=Min("next_integrity_audit_at"))["next"],
                "recent_ftp_failures": period_replicas.filter(status="failed").select_related(
                    "destination", "revision__target__device"
                )[:5],
                "can_add_target": _can_assign_target_retention(self.request.user),
                "backup_worker_available": _backup_worker_available(),
            }
        )
        return context


class AdvancedSettingsView(PermissionRequiredMixin, LoginRequiredMixin, TemplateView):
    template_name = "netbox_config_backup/advanced_settings.html"
    permission_required = "netbox_config_backup.view_operationalsettings"
    raise_exception = True

    @staticmethod
    def _operational_settings():
        operational_settings = OperationalSettings.objects.filter(singleton=True).first()
        return operational_settings or OperationalSettings(singleton=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        operational_settings = kwargs.get(
            "operational_settings",
            self._operational_settings(),
        )
        runtime_controls = get_runtime_controls()
        context.update(
            {
                "operational_settings": operational_settings,
                "operational_settings_form": kwargs.get(
                    "operational_settings_form",
                    forms.OperationalSettingsForm(instance=operational_settings),
                ),
                "notification_settings_form": kwargs.get(
                    "notification_settings_form",
                    forms.NotificationSettingsForm(instance=operational_settings),
                ),
                "interface_language_form": kwargs.get(
                    "interface_language_form",
                    forms.InterfaceLanguageSettingsForm(instance=operational_settings),
                ),
                "current_ui_language": resolve_ui_language(self.request),
                "can_change_operational_settings": self.request.user.has_perm(
                    "netbox_config_backup.change_operationalsettings"
                ),
                "retention_scheduler_enabled": (operational_settings.retention_scheduler_enabled),
                "remote_retention_scheduler_enabled": (
                    operational_settings.remote_retention_scheduler_enabled
                ),
                "backup_events_enabled": runtime_controls.events_enabled,
                "backup_notify_every_failure": runtime_controls.notify_on_every_failure,
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("netbox_config_backup.change_operationalsettings"):
            raise PermissionDenied
        operational_settings = self._operational_settings()
        settings_action = request.POST.get("settings_action", "retention")
        if settings_action == "language":
            return self._save_language(request, operational_settings)
        if settings_action in {"notifications", "runtime_integrations"}:
            return self._save_notifications(request, operational_settings)
        if settings_action != "retention":
            return HttpResponseBadRequest("Unknown settings action.")
        form = forms.OperationalSettingsForm(
            request.POST,
            instance=operational_settings,
        )
        if not form.is_valid():
            if operational_settings.pk:
                operational_settings.refresh_from_db()
            context = self.get_context_data(
                operational_settings=operational_settings,
                operational_settings_form=form,
            )
            return render(request, self.template_name, context, status=400)

        if operational_settings.pk and hasattr(operational_settings, "snapshot"):
            operational_settings.snapshot()
        operational_settings._changelog_message = "Updated automatic retention settings."
        operational_settings = form.save()
        local_state = "enabled" if operational_settings.retention_scheduler_enabled else "disabled"
        remote_state = (
            "enabled" if operational_settings.remote_retention_scheduler_enabled else "disabled"
        )
        messages.success(
            request,
            f"Automatic local retention is {local_state}; remote retention is {remote_state}. "
            "No Docker restart is required.",
        )
        return redirect("plugins:netbox_config_backup:advanced_settings")

    def _save_language(self, request, operational_settings):
        form = forms.InterfaceLanguageSettingsForm(
            request.POST,
            instance=operational_settings,
        )
        if not form.is_valid():
            if operational_settings.pk:
                operational_settings.refresh_from_db()
            context = self.get_context_data(
                operational_settings=operational_settings,
                interface_language_form=form,
            )
            return render(request, self.template_name, context, status=400)

        if operational_settings.pk and hasattr(operational_settings, "snapshot"):
            operational_settings.snapshot()
        operational_settings._changelog_message = "Updated Config Backup interface language."
        operational_settings = form.save()
        request.session.pop(SESSION_KEY, None)
        if operational_settings.ui_language == InterfaceLanguageChoices.SLOVAK:
            messages.success(request, "Jazyk pluginu bol nastavený na slovenčinu.")
        else:
            messages.success(request, "The plugin language was set to English.")
        return redirect("plugins:netbox_config_backup:advanced_settings")

    def _save_notifications(self, request, operational_settings):
        form = forms.NotificationSettingsForm(
            request.POST,
            instance=operational_settings,
        )
        if not form.is_valid():
            if operational_settings.pk:
                operational_settings.refresh_from_db()
            context = self.get_context_data(
                operational_settings=operational_settings,
                notification_settings_form=form,
            )
            return render(request, self.template_name, context, status=400)

        if operational_settings.pk and hasattr(operational_settings, "snapshot"):
            operational_settings.snapshot()
        operational_settings._changelog_message = "Updated notification settings."
        form.save()
        messages.success(
            request,
            "Notification settings were updated; no Docker restart is required.",
        )
        return redirect("plugins:netbox_config_backup:advanced_settings")


class ConfigBackupHelpView(PermissionRequiredMixin, LoginRequiredMixin, TemplateView):
    """Read-only operator guide which never exposes deployment values or secrets."""

    template_name = "netbox_config_backup/help.html"
    permission_required = "netbox_config_backup.view_backuptarget"
    raise_exception = True

    def get(self, request, *args, **kwargs):
        requested_language = request.GET.get("language")
        if requested_language is not None:
            if requested_language not in InterfaceLanguageChoices.values:
                return HttpResponseBadRequest("Unsupported interface language.")
            request.session[SESSION_KEY] = requested_language
            return redirect("plugins:netbox_config_backup:help")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_ui_language"] = resolve_ui_language(self.request)
        return context


def _queryset_fully_permitted(queryset, user, action: str) -> bool:
    permitted_ids = queryset.restrict(user, action).values("pk")
    return not queryset.exclude(pk__in=permitted_ids).exists()


def _assert_target_delete_permissions(targets, user) -> None:
    if not _queryset_fully_permitted(targets, user, "delete"):
        raise PermissionDenied

    target_ids = targets.values("pk")
    dependent_checks = (
        (BackupRun.objects.filter(target_id__in=target_ids), "delete"),
        (ConfigRevision.objects.filter(target_id__in=target_ids), "delete"),
        (ConfigArtifact.objects.filter(revision__target_id__in=target_ids), "delete"),
        (RevisionReplica.objects.filter(revision__target_id__in=target_ids), "delete"),
    )
    if not all(
        _queryset_fully_permitted(queryset, user, action) for queryset, action in dependent_checks
    ):
        raise PermissionDenied

    quick_connections = ConnectionProfile.objects.filter(
        target_overrides__in=target_ids,
        name__startswith="[Quick]",
    ).distinct()
    quick_credentials = CredentialProfile.objects.filter(
        target_overrides__in=target_ids,
        name__startswith="[Quick]",
    ).distinct()
    if not _queryset_fully_permitted(quick_connections, user, "delete"):
        raise PermissionDenied
    if not _queryset_fully_permitted(quick_credentials, user, "delete"):
        raise PermissionDenied
    if quick_credentials.filter(provider_id="encrypted_database").exists() and not user.has_perm(
        "netbox_config_backup.delete_storedcredential"
    ):
        raise PermissionDenied


def _remote_retention_replicas(target):
    """Return every exact FTP path which remote cleanup may tombstone.

    Exhausted failed repairs remain candidates because their recorded path can
    still contain an older complete copy or a partial upload. Active and
    retryable rows remain excluded until their transfer lifecycle finishes.
    """

    return RevisionReplica.objects.filter(
        revision__target=target,
        destination__protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        destination__enabled=True,
        remote_deleted_at__isnull=True,
    ).filter(
        Q(
            status=ReplicaStatusChoices.SUCCESS,
            remote_available=True,
        )
        | Q(
            status=ReplicaStatusChoices.FAILED,
            remote_path__gt="",
            next_retry_at__isnull=True,
        )
    )


def _retention_preview_context(target, *, now, user=None):
    local_storages = BackupDestination.objects.filter(
        protocol=DestinationProtocolChoices.LOCAL,
        is_default=True,
    ).select_related("local_retention_policy")
    if user is not None:
        local_storages = local_storages.restrict(user, "view")
    local_storage = local_storages.first()
    local_policy = (
        effective_local_retention_policy(target, local_storage)
        if local_storage is not None
        else effective_retention_policy(target)
    )
    revisions_queryset = (
        target.revisions.filter(artifacts__local_available=True)
        .distinct()
        .prefetch_related("artifacts")
        .order_by("-created")
    )
    runs_queryset = target.runs.all().order_by("-queued_at")
    if user is not None:
        revisions_queryset = revisions_queryset.restrict(user, "view")
        runs_queryset = runs_queryset.restrict(user, "view")
    revisions = list(revisions_queryset)
    runs = list(runs_queryset)
    local_plan = None
    if local_policy is not None:
        local_plan = build_retention_plan(
            settings_from_policy(local_policy),
            revisions=(
                RevisionCandidate(
                    object_id=revision.pk,
                    created=revision.created,
                    protected=revision.protected,
                    content_changed=revision.content_changed,
                )
                for revision in revisions
            ),
            runs=(
                RunCandidate(
                    object_id=run.pk,
                    timestamp=run.finished_at or run.queued_at,
                    status=run.status,
                )
                for run in runs
            ),
            now=now,
        )
    revision_by_id = {revision.pk: revision for revision in revisions}
    run_by_id = {run.pk: run for run in runs}
    expired_revision_ids = {
        decision.object_id
        for decision in (local_plan.revision_decisions if local_plan else ())
        if not decision.keep
    }
    expired_artifacts = tuple(
        artifact
        for revision in revisions
        if revision.pk in expired_revision_ids
        for artifact in revision.artifacts.all()
        if artifact.local_available
    )

    available_ftp_replicas = _remote_retention_replicas(target).select_related("destination")
    if user is not None:
        available_ftp_replicas = available_ftp_replicas.restrict(user, "view")
    ftp_storages = (
        BackupDestination.objects.filter(
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
            enabled=True,
            replicas__in=available_ftp_replicas,
        )
        .select_related("remote_retention_policy")
        .distinct()
    )
    if user is not None:
        ftp_storages = ftp_storages.restrict(user, "view")
    remote_storage_plans = []
    remote_expired_bytes = 0
    remote_expired_copy_count = 0
    remote_revisions_to_delete = 0
    remote_has_policy = False
    for storage in ftp_storages.order_by("name"):
        storage_replicas = available_ftp_replicas.filter(destination=storage)
        storage_revisions_queryset = (
            target.revisions.filter(pk__in=storage_replicas.values("revision_id"))
            .prefetch_related(
                Prefetch(
                    "replicas",
                    queryset=storage_replicas,
                    to_attr="available_storage_replicas",
                )
            )
            .order_by("-created")
        )
        if user is not None:
            storage_revisions_queryset = storage_revisions_queryset.restrict(user, "view")
        storage_revisions = list(storage_revisions_queryset)
        policy = effective_remote_retention_policy(target, storage)
        plan = None
        if policy is not None:
            remote_has_policy = True
            plan = build_retention_plan(
                settings_from_remote_policy(policy),
                revisions=(
                    RevisionCandidate(
                        object_id=revision.pk,
                        created=revision.created,
                        protected=revision.protected,
                        content_changed=revision.content_changed,
                    )
                    for revision in storage_revisions
                ),
                runs=(),
                now=now,
            )
        revision_by_id_for_storage = {revision.pk: revision for revision in storage_revisions}
        storage_rows = []
        storage_expired_bytes = 0
        storage_expired_copies = 0
        for decision in plan.revision_decisions if plan else ():
            revision = revision_by_id_for_storage[decision.object_id]
            replicas = tuple(revision.available_storage_replicas)
            if not decision.keep:
                storage_expired_bytes += sum(replica.bytes_transferred for replica in replicas)
                storage_expired_copies += len(replicas)
            storage_rows.append((revision, decision, replicas))
        remote_expired_bytes += storage_expired_bytes
        remote_expired_copy_count += storage_expired_copies
        remote_revisions_to_delete += plan.revisions_to_delete if plan else 0
        remote_storage_plans.append(
            {
                "storage": storage,
                "policy": policy,
                "policy_source": remote_retention_policy_source(target, storage),
                "plan": plan,
                "revision_rows": tuple(storage_rows),
                "expired_bytes": storage_expired_bytes,
                "expired_copy_count": storage_expired_copies,
            }
        )

    return {
        # Compatibility aliases used by the existing local confirmation view.
        "policy": local_policy,
        "plan": local_plan,
        "revision_rows": tuple(
            (revision_by_id[decision.object_id], decision)
            for decision in (local_plan.revision_decisions if local_plan else ())
        ),
        "run_rows": tuple(
            (run_by_id[decision.object_id], decision)
            for decision in (local_plan.run_decisions if local_plan else ())
        ),
        "expired_artifact_count": len(expired_artifacts),
        "expired_artifact_bytes": sum(artifact.size for artifact in expired_artifacts),
        "local_policy": local_policy,
        "local_storage": local_storage,
        "local_policy_source": (
            local_retention_policy_source(target, local_storage)
            if local_storage is not None
            else "Device configuration"
        ),
        "local_plan": local_plan,
        "remote_has_policy": remote_has_policy,
        "remote_storage_plans": tuple(remote_storage_plans),
        "remote_revisions_to_delete": remote_revisions_to_delete,
        "remote_expired_bytes": remote_expired_bytes,
        "remote_expired_copy_count": remote_expired_copy_count,
    }


class ExampleConfigurationView(PermissionRequiredMixin, LoginRequiredMixin, View):
    template_name = "netbox_config_backup/examples.html"
    permission_required = (
        "netbox_config_backup.view_retentionpolicy",
        "netbox_config_backup.view_backuppolicy",
        "netbox_config_backup.view_connectionprofile",
        "netbox_config_backup.view_credentialprofile",
        "netbox_config_backup.view_platformmapping",
    )
    raise_exception = True
    create_permissions = (
        "netbox_config_backup.add_retentionpolicy",
        "netbox_config_backup.add_backuppolicy",
        "netbox_config_backup.add_connectionprofile",
        "netbox_config_backup.add_credentialprofile",
        "netbox_config_backup.add_platformmapping",
        "dcim.add_platform",
    )

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "examples": get_example_configuration(),
                "can_create_examples": request.user.has_perms(self.create_permissions),
            },
        )

    def post(self, request):
        if not request.user.has_perms(self.create_permissions):
            raise PermissionDenied
        create_or_reset_example_configuration()
        messages.success(request, "Example configuration was created or reset.")
        return redirect("plugins:netbox_config_backup:examples")


class ConfigObjectView(generic.ObjectView):
    template_name = "netbox_config_backup/config_object.html"
    display_fields: tuple[str, ...] = ()

    def get_extra_context(self, request, instance):
        rows = []
        for field_name in self.display_fields:
            field = instance._meta.get_field(field_name)
            display_method = getattr(instance, f"get_{field_name}_display", None)
            value = display_method() if display_method else getattr(instance, field_name)
            rows.append((field.verbose_name, value))
        return {"detail_rows": rows}


@register_model_view(RetentionPolicy, "list", path="", detail=False)
class RetentionPolicyListView(generic.ObjectListView):
    queryset = RetentionPolicy.objects.all()
    table = tables.RetentionPolicyTable


@register_model_view(RetentionPolicy)
class RetentionPolicyView(ConfigObjectView):
    queryset = RetentionPolicy.objects.all()
    display_fields = (
        "keep_all_days",
        "daily_days",
        "weekly_weeks",
        "monthly_months",
        "minimum_changed_revisions",
        "unchanged_run_days",
        "changed_run_days",
        "failed_run_days",
        "max_runs_per_target",
    )


@register_model_view(RetentionPolicy, "add", detail=False)
@register_model_view(RetentionPolicy, "edit")
class RetentionPolicyEditView(generic.ObjectEditView):
    queryset = RetentionPolicy.objects.all()
    form = forms.RetentionPolicyForm


@register_model_view(RetentionPolicy, "delete")
class RetentionPolicyDeleteView(generic.ObjectDeleteView):
    queryset = RetentionPolicy.objects.all()


@register_model_view(RemoteRetentionPolicy, "list", path="", detail=False)
class RemoteRetentionPolicyListView(generic.ObjectListView):
    queryset = RemoteRetentionPolicy.objects.all()
    table = tables.RemoteRetentionPolicyTable


@register_model_view(RemoteRetentionPolicy)
class RemoteRetentionPolicyView(ConfigObjectView):
    queryset = RemoteRetentionPolicy.objects.all()
    display_fields = (
        "keep_all_days",
        "daily_days",
        "weekly_weeks",
        "monthly_months",
        "minimum_changed_revisions",
        "max_copies_per_target",
    )


@register_model_view(RemoteRetentionPolicy, "add", detail=False)
@register_model_view(RemoteRetentionPolicy, "edit")
class RemoteRetentionPolicyEditView(generic.ObjectEditView):
    queryset = RemoteRetentionPolicy.objects.all()
    form = forms.RemoteRetentionPolicyForm


@register_model_view(RemoteRetentionPolicy, "delete")
class RemoteRetentionPolicyDeleteView(generic.ObjectDeleteView):
    queryset = RemoteRetentionPolicy.objects.all()


@register_model_view(BackupPolicy, "list", path="", detail=False)
class BackupPolicyListView(generic.ObjectListView):
    queryset = BackupPolicy.objects.select_related("retention_policy")
    table = tables.BackupPolicyTable


@register_model_view(BackupPolicy)
class BackupPolicyView(ConfigObjectView):
    queryset = BackupPolicy.objects.select_related("retention_policy")
    display_fields = (
        "enabled",
        "schedule_type",
        "interval_minutes",
        "time_of_day",
        "timezone_mode",
        "jitter_minutes",
        "connection_timeout",
        "command_timeout",
        "max_retries",
        "retry_backoff_minutes",
        "store_mode",
        "retention_policy",
    )


@register_model_view(BackupPolicy, "add", detail=False)
@register_model_view(BackupPolicy, "edit")
class BackupPolicyEditView(generic.ObjectEditView):
    queryset = BackupPolicy.objects.all()
    form = forms.BackupPolicyForm


@register_model_view(BackupPolicy, "delete")
class BackupPolicyDeleteView(generic.ObjectDeleteView):
    queryset = BackupPolicy.objects.all()


@register_model_view(ConnectionProfile, "list", path="", detail=False)
class ConnectionProfileListView(generic.ObjectListView):
    queryset = ConnectionProfile.objects.all()
    table = tables.ConnectionProfileTable


@register_model_view(ConnectionProfile)
class ConnectionProfileView(ConfigObjectView):
    queryset = ConnectionProfile.objects.all()
    display_fields = (
        "protocol",
        "address_preference",
        "port",
        "connect_timeout",
        "command_timeout",
        "keepalive",
        "host_key_policy_label",
    )


@register_model_view(ConnectionProfile, "add", detail=False)
@register_model_view(ConnectionProfile, "edit")
class ConnectionProfileEditView(generic.ObjectEditView):
    queryset = ConnectionProfile.objects.all()
    form = forms.ConnectionProfileForm


@register_model_view(ConnectionProfile, "delete")
class ConnectionProfileDeleteView(generic.ObjectDeleteView):
    queryset = ConnectionProfile.objects.all()


@register_model_view(CredentialProfile, "list", path="", detail=False)
class CredentialProfileListView(generic.ObjectListView):
    queryset = CredentialProfile.objects.exclude(provider_id="vault_kv2").select_related(
        "stored_credential"
    )
    table = tables.CredentialProfileTable


@register_model_view(CredentialProfile)
class CredentialProfileView(ConfigObjectView):
    queryset = CredentialProfile.objects.exclude(provider_id="vault_kv2").select_related(
        "stored_credential"
    )

    def get_extra_context(self, request, instance):
        username = instance.stored_username if instance.provider_id == "encrypted_database" else ""
        password_status = (
            "Configured (write-only)"
            if instance.provider_id == "encrypted_database" and instance.has_stored_password
            else "Not stored in NetBox"
        )
        return {
            "detail_rows": (
                ("Provider", instance.provider_id),
                ("Secret reference", instance.secret_reference),
                ("Authentication type", instance.get_auth_type_display()),
                ("Username", username),
                ("Password", password_status),
            )
        }


@register_model_view(CredentialProfile, "add", detail=False)
@register_model_view(CredentialProfile, "edit")
class CredentialProfileEditView(generic.ObjectEditView):
    queryset = CredentialProfile.objects.exclude(provider_id="vault_kv2").select_related(
        "stored_credential"
    )
    form = forms.CredentialProfileForm

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            pk = kwargs.get("pk")
            existing = CredentialProfile.objects.filter(pk=pk).first() if pk else None
            posted_provider = request.POST.get("provider_id", "")
            required_permission = None
            if existing and existing.provider_id == "encrypted_database":
                required_permission = (
                    "netbox_config_backup.change_storedcredential"
                    if posted_provider == "encrypted_database"
                    else "netbox_config_backup.delete_storedcredential"
                )
            elif posted_provider == "encrypted_database":
                required_permission = "netbox_config_backup.add_storedcredential"
            if required_permission and not request.user.has_perm(required_permission):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


@register_model_view(CredentialProfile, "delete")
class CredentialProfileDeleteView(generic.ObjectDeleteView):
    queryset = CredentialProfile.objects.exclude(provider_id="vault_kv2")

    def dispatch(self, request, *args, **kwargs):
        instance = self.queryset.filter(pk=kwargs.get("pk")).first()
        if (
            request.method == "POST"
            and instance
            and instance.provider_id == "encrypted_database"
            and not request.user.has_perm("netbox_config_backup.delete_storedcredential")
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


@register_model_view(BackupDestination, "list", path="", detail=False)
class BackupDestinationListView(generic.ObjectListView):
    queryset = BackupDestination.objects.filter(
        protocol__in=MANAGED_DESTINATION_PROTOCOLS
    ).select_related(
        "credential_profile",
        "local_retention_policy",
        "remote_retention_policy",
    )
    table = tables.BackupDestinationTable
    # Per-row edit/delete controls below understand the protected Local row.
    # Omitting bulk mutation also prevents selecting the system storage for a
    # generic bulk-delete or bulk-edit request.
    actions = (AddObject, BulkExport)


@register_model_view(BackupDestination)
class BackupDestinationView(generic.ObjectView):
    queryset = BackupDestination.objects.filter(
        protocol__in=MANAGED_DESTINATION_PROTOCOLS
    ).select_related(
        "credential_profile",
        "local_retention_policy",
        "remote_retention_policy",
    )
    template_name = "netbox_config_backup/backupdestination.html"

    def get_permitted_actions(self, user, model=None):
        actions = super().get_permitted_actions(user, model=model)
        if getattr(model, "protocol", None) == DestinationProtocolChoices.LOCAL:
            # A Local storage may be edited only to select its retention
            # profile/precedence. It cannot be cloned or deleted.
            return tuple(action for action in actions if action.name not in {"add", "delete"})
        return actions

    def get_extra_context(self, request, instance):
        is_local = instance.protocol == DestinationProtocolChoices.LOCAL
        is_remote = instance.protocol in REPLICATED_DESTINATION_PROTOCOLS
        replicas = RevisionReplica.objects.none()
        replica_page = None
        replica_page_numbers = ()
        replica_search = ""
        replica_state = "all"
        replica_stats = {"total": 0, "available": 0, "problems": 0, "expired": 0}
        replica_pagination_query = ""
        if is_remote:
            replicas = instance.replicas.restrict(request.user, "view").select_related(
                "revision__target__device"
            )
            available_filter = Q(
                status=ReplicaStatusChoices.SUCCESS,
                remote_available=True,
                remote_deleted_at__isnull=True,
            )
            problem_filter = Q(status=ReplicaStatusChoices.FAILED) | Q(
                status=ReplicaStatusChoices.SUCCESS,
                remote_available=False,
                remote_deleted_at__isnull=True,
            )
            replica_stats = replicas.aggregate(
                total=Count("pk"),
                available=Count("pk", filter=available_filter),
                problems=Count("pk", filter=problem_filter),
                expired=Count("pk", filter=Q(remote_deleted_at__isnull=False)),
            )

            replica_search = request.GET.get("replica_q", "").strip()[:200]
            requested_state = request.GET.get("replica_state", "all")
            if requested_state in {"all", "available", "processing", "problems", "expired"}:
                replica_state = requested_state
            if replica_search:
                search_filter = Q(
                    revision__target__device__name__icontains=replica_search
                ) | Q(remote_path__icontains=replica_search)
                try:
                    search_filter |= Q(revision__revision_uuid=UUID(replica_search))
                except ValueError:
                    pass
                replicas = replicas.filter(search_filter)
            if replica_state == "available":
                replicas = replicas.filter(available_filter)
            elif replica_state == "processing":
                replicas = replicas.filter(
                    status__in=(
                        ReplicaStatusChoices.PENDING,
                        ReplicaStatusChoices.QUEUED,
                        ReplicaStatusChoices.RUNNING,
                    )
                )
            elif replica_state == "problems":
                replicas = replicas.filter(problem_filter)
            elif replica_state == "expired":
                replicas = replicas.filter(remote_deleted_at__isnull=False)

            replicas = replicas.order_by("-revision__created", "-created")
            replica_page = Paginator(replicas, 25).get_page(request.GET.get("replica_page"))
            replica_page_numbers = replica_page.paginator.get_elided_page_range(
                replica_page.number,
                on_each_side=2,
                on_ends=1,
            )
            pagination_query = request.GET.copy()
            pagination_query.pop("replica_page", None)
            replica_pagination_query = pagination_query.urlencode()
        latest_reconciliation_job = (
            DestinationReconciliationJob.get_jobs(instance).order_by("-created").first()
            if is_remote
            else None
        )
        return {
            "is_local_storage": is_local,
            "is_remote_storage": is_remote,
            "is_ftp_storage": instance.protocol == DestinationProtocolChoices.FTP,
            "is_mounted_storage": instance.protocol
            in (DestinationProtocolChoices.NFS, DestinationProtocolChoices.SMB),
            "replicas": replica_page.object_list if replica_page else replicas,
            "replica_page": replica_page,
            "replica_page_numbers": replica_page_numbers,
            "replica_page_ellipsis": Paginator.ELLIPSIS,
            "replica_search": replica_search,
            "replica_state": replica_state,
            "replica_stats": replica_stats,
            "replica_pagination_query": replica_pagination_query,
            "latest_reconciliation_job": latest_reconciliation_job,
            "latest_reconciliation": (
                destination_reconciliation_status_payload(latest_reconciliation_job)
                if latest_reconciliation_job
                else None
            ),
            "audit_schedule_form": (
                forms.FtpIntegrityAuditScheduleForm(instance=instance) if is_remote else None
            ),
            "audit_timezone": settings.TIME_ZONE,
            "can_test": is_remote
            and request.user.has_perm("netbox_config_backup.change_backupdestination"),
            "can_reconcile": is_remote
            and request.user.has_perm("netbox_config_backup.change_backupdestination"),
            "can_backfill": is_remote
            and request.user.has_perms(
                (
                    "netbox_config_backup.change_backupdestination",
                    "netbox_config_backup.add_revisionreplica",
                )
            ),
            "can_retry": request.user.has_perm("netbox_config_backup.change_revisionreplica"),
        }


@register_model_view(BackupDestination, "add", detail=False)
@register_model_view(BackupDestination, "edit")
class BackupDestinationEditView(generic.ObjectEditView):
    queryset = BackupDestination.objects.filter(protocol__in=MANAGED_DESTINATION_PROTOCOLS)
    form = forms.BackupDestinationForm
    template_name = "netbox_config_backup/backupdestination_edit.html"

    def form_valid(self, form):
        if form.instance.pk:
            original = BackupDestination.objects.get(pk=form.instance.pk)
            protocol = original.protocol
            local_changed = protocol == DestinationProtocolChoices.LOCAL and (
                original.local_retention_policy_id
                != getattr(form.cleaned_data.get("local_retention_policy"), "pk", None)
                or original.enforce_retention_policy
                != form.cleaned_data.get("enforce_retention_policy", False)
            )
            remote_changed = protocol in REPLICATED_DESTINATION_PROTOCOLS and (
                original.remote_retention_policy_id
                != getattr(form.cleaned_data.get("remote_retention_policy"), "pk", None)
                or original.enforce_retention_policy
                != form.cleaned_data.get("enforce_retention_policy", False)
            )
        else:
            local_changed = False
            remote_changed = bool(
                form.cleaned_data.get("remote_retention_policy")
                or form.cleaned_data.get("enforce_retention_policy")
            )
        _assert_target_retention_assignment_permissions(
            self.request.user,
            local_retention_changed=local_changed,
            remote_retention_changed=remote_changed,
        )
        return super().form_valid(form)


@register_model_view(BackupDestination, "delete")
class BackupDestinationDeleteView(generic.ObjectDeleteView):
    queryset = BackupDestination.objects.filter(
        protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        is_default=False,
    )


@register_model_view(BackupDestination, "bulk_delete", detail=False)
class BackupDestinationBulkDeleteView(generic.BulkDeleteView):
    queryset = BackupDestination.objects.filter(
        protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        is_default=False,
    )
    table = tables.BackupDestinationTable


@register_model_view(BackupDestination, "test_connection", path="test-connection")
class BackupDestinationTestView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backupdestination",
        "netbox_config_backup.change_backupdestination",
    )
    raise_exception = True

    def post(self, request, pk):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "change"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        active_job = (
            DestinationConnectionTestJob.get_jobs(destination)
            .filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)
            .order_by("-created")
            .first()
        )
        if active_job:
            job = active_job
            messages.info(request, "A destination test is already in progress.")
        else:
            job = DestinationConnectionTestJob.enqueue(
                destination_id=destination.pk,
                instance=destination,
                user=request.user,
                queue_name=BACKUP_QUEUE,
            )
            messages.info(request, "The destination test was queued.")
        return redirect(
            "plugins:netbox_config_backup:backupdestination_test_result",
            pk=destination.pk,
            job_id=job.job_id,
        )


class BackupDestinationTestResultView(PermissionRequiredMixin, LoginRequiredMixin, TemplateView):
    template_name = "netbox_config_backup/destination_test.html"
    permission_required = "netbox_config_backup.view_backupdestination"
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        destination = get_object_or_404(
            BackupDestination.objects.restrict(self.request.user, "view"),
            pk=self.kwargs["pk"],
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        job = get_object_or_404(
            DestinationConnectionTestJob.get_jobs(destination),
            job_id=self.kwargs["job_id"],
        )
        context.update(
            {
                "destination": destination,
                "job": job,
                "status_payload": destination_test_status_payload(job),
                "status_url": reverse(
                    "plugins:netbox_config_backup:backupdestination_test_status",
                    kwargs={"pk": destination.pk, "job_id": job.job_id},
                ),
                "can_manage": self.request.user.has_perm(
                    "netbox_config_backup.change_backupdestination"
                ),
                "can_view_job": self.request.user.has_perm("core.view_job"),
            }
        )
        return context


class BackupDestinationTestStatusView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.view_backupdestination"
    raise_exception = True

    def get(self, request, pk, job_id):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "view"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        job = get_object_or_404(DestinationConnectionTestJob.get_jobs(destination), job_id=job_id)
        response = JsonResponse(destination_test_status_payload(job))
        response["Cache-Control"] = "no-store"
        return response


@register_model_view(BackupDestination, "reconcile", path="reconcile")
class BackupDestinationReconciliationView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backupdestination",
        "netbox_config_backup.change_backupdestination",
    )
    raise_exception = True

    def post(self, request, pk):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "change"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        active_job = (
            DestinationReconciliationJob.get_jobs(destination)
            .filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)
            .order_by("-created")
            .first()
        )
        if active_job:
            job = active_job
            messages.info(request, "A storage integrity audit is already in progress.")
        else:
            job = DestinationReconciliationJob.enqueue(
                destination_id=destination.pk,
                instance=destination,
                user=request.user,
                queue_name=BACKUP_QUEUE,
            )
            messages.info(request, "The read-only storage integrity audit was queued.")
        return redirect(
            "plugins:netbox_config_backup:backupdestination_reconciliation_result",
            pk=destination.pk,
            job_id=job.job_id,
        )


class BackupDestinationReconciliationResultView(
    PermissionRequiredMixin, LoginRequiredMixin, TemplateView
):
    template_name = "netbox_config_backup/destination_reconciliation.html"
    permission_required = "netbox_config_backup.view_backupdestination"
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        destination = get_object_or_404(
            BackupDestination.objects.restrict(self.request.user, "view"),
            pk=self.kwargs["pk"],
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        job = get_object_or_404(
            DestinationReconciliationJob.get_jobs(destination),
            job_id=self.kwargs["job_id"],
        )
        context.update(
            {
                "destination": destination,
                "job": job,
                "status_payload": destination_reconciliation_status_payload(job),
                "status_url": reverse(
                    "plugins:netbox_config_backup:backupdestination_reconciliation_status",
                    kwargs={"pk": destination.pk, "job_id": job.job_id},
                ),
                "can_manage": self.request.user.has_perm(
                    "netbox_config_backup.change_backupdestination"
                ),
                "can_view_job": self.request.user.has_perm("core.view_job"),
            }
        )
        return context


class BackupDestinationReconciliationStatusView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.view_backupdestination"
    raise_exception = True

    def get(self, request, pk, job_id):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "view"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        job = get_object_or_404(DestinationReconciliationJob.get_jobs(destination), job_id=job_id)
        response = JsonResponse(destination_reconciliation_status_payload(job))
        response["Cache-Control"] = "no-store"
        return response


@register_model_view(BackupDestination, "audit_schedule", path="audit-schedule")
class BackupDestinationAuditScheduleView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backupdestination",
        "netbox_config_backup.change_backupdestination",
    )
    raise_exception = True

    def post(self, request, pk):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "change"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        form = forms.FtpIntegrityAuditScheduleForm(request.POST, instance=destination)
        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
            return redirect(destination.get_absolute_url())

        if hasattr(destination, "snapshot"):
            destination.snapshot()
        destination._changelog_message = "Updated automatic storage integrity audit schedule."
        destination = form.save()
        if destination.integrity_audit_enabled:
            messages.success(
                request,
                f"Automatic storage integrity audit enabled; next audit is "
                f"{destination.next_integrity_audit_at}.",
            )
        else:
            messages.success(request, "Automatic storage integrity audit disabled.")
        return redirect(destination.get_absolute_url())


class BackupDestinationTrustHostKeyView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_backupdestination"
    raise_exception = True

    def post(self, request, pk, job_id):
        raise Http404


class BackupDestinationBackfillView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.change_backupdestination",
        "netbox_config_backup.add_revisionreplica",
    )
    raise_exception = True

    def post(self, request, pk):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "change"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        count = backfill_destination(destination, user=request.user)
        if count:
            messages.success(request, f"Queued {count} existing revision(s) for replication.")
        else:
            messages.info(request, "All existing revisions already have a replica record.")
        return redirect(destination.get_absolute_url())


class RevisionReplicaRetryView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_revisionreplica"
    raise_exception = True

    def post(self, request, pk, replica_pk):
        destination = get_object_or_404(
            BackupDestination.objects.restrict(request.user, "view"),
            pk=pk,
            protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        )
        replica = get_object_or_404(
            RevisionReplica.objects.restrict(request.user, "change"),
            pk=replica_pk,
            destination=destination,
        )
        job = enqueue_revision_replica(replica.pk, user=request.user, force=True)
        if job:
            messages.success(request, "The storage replication retry was queued.")
        else:
            messages.warning(request, "The remote storage is disabled.")
        return redirect(destination.get_absolute_url())


@register_model_view(SftpReceiverProfile, "list", path="", detail=False)
class SftpReceiverProfileListView(generic.ObjectListView):
    queryset = SftpReceiverProfile.objects.select_related("credential_profile")
    table = tables.SftpReceiverProfileTable


@register_model_view(SftpReceiverProfile)
class SftpReceiverProfileView(ConfigObjectView):
    queryset = SftpReceiverProfile.objects.select_related("credential_profile")
    display_fields = (
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
    )


@register_model_view(SftpReceiverProfile, "add", detail=False)
@register_model_view(SftpReceiverProfile, "edit")
class SftpReceiverProfileEditView(generic.ObjectEditView):
    queryset = SftpReceiverProfile.objects.all()
    form = forms.SftpReceiverProfileForm


@register_model_view(SftpReceiverProfile, "delete")
class SftpReceiverProfileDeleteView(generic.ObjectDeleteView):
    queryset = SftpReceiverProfile.objects.all()


@register_model_view(PlatformMapping, "list", path="", detail=False)
class PlatformMappingListView(generic.ObjectListView):
    queryset = PlatformMapping.objects.select_related(
        "platform", "connection_profile", "credential_profile", "receiver_profile"
    )
    table = tables.PlatformMappingTable


@register_model_view(PlatformMapping)
class PlatformMappingView(ConfigObjectView):
    queryset = PlatformMapping.objects.select_related(
        "platform", "connection_profile", "credential_profile", "receiver_profile"
    )
    display_fields = (
        "platform",
        "driver_id",
        "enabled",
        "connection_profile",
        "credential_profile",
        "receiver_profile",
        "driver_options",
    )


@register_model_view(PlatformMapping, "add", detail=False)
@register_model_view(PlatformMapping, "edit")
class PlatformMappingEditView(generic.ObjectEditView):
    queryset = PlatformMapping.objects.all()
    form = forms.PlatformMappingForm


@register_model_view(PlatformMapping, "delete")
class PlatformMappingDeleteView(generic.ObjectDeleteView):
    queryset = PlatformMapping.objects.all()


@register_model_view(Device, "config_backup", path="backup")
class DeviceConfigBackupView(generic.ObjectView):
    """Expose permission-aware backup history on the native NetBox device page."""

    queryset = Device.objects.all()
    template_name = "netbox_config_backup/device_backup.html"
    actions = ()
    additional_permissions = ("netbox_config_backup.view_backuptarget",)
    tab = ViewTab(
        label="Backup",
        permission="netbox_config_backup.view_backuptarget",
        weight=1950,
    )

    def get_extra_context(self, request, instance):
        target = (
            BackupTarget.objects.restrict(request.user, "view")
            .select_related(
                "last_revision",
                "policy_override",
                "retention_override",
                "remote_retention_policy",
            )
            .filter(device=instance)
            .first()
        )
        if target is None:
            return {
                "target": None,
                "revisions": (),
                "recent_runs": (),
                "can_add_target": request.user.has_perm(
                    "netbox_config_backup.add_backuptarget"
                ),
            }

        visible_artifacts = ConfigArtifact.objects.restrict(request.user, "view").filter(
            local_available=True
        )
        revisions = list(
            ConfigRevision.objects.restrict(request.user, "view")
            .filter(target=target)
            .prefetch_related(
                Prefetch(
                    "artifacts",
                    queryset=visible_artifacts,
                    to_attr="device_backup_artifacts",
                )
            )
            .order_by("-created")[:25]
        )
        for revision in revisions:
            artifacts = tuple(revision.device_backup_artifacts)
            primary = next((artifact for artifact in artifacts if artifact.is_primary), None)
            native = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.artifact_type == "native_backup"
                ),
                None,
            )
            revision.device_backup_preview_artifact = primary
            revision.device_backup_download_artifact = native or primary
            revision.device_backup_size = sum(artifact.size for artifact in artifacts)

        recent_runs = list(
            BackupRun.objects.restrict(request.user, "view")
            .filter(target=target)
            .select_related("revision")
            .order_by("-queued_at")[:10]
        )
        return {
            "target": target,
            "revisions": revisions,
            "recent_runs": recent_runs,
            "revision_count": ConfigRevision.objects.restrict(request.user, "view")
            .filter(target=target)
            .count(),
            "can_add_target": False,
        }


@register_model_view(BackupTarget, "list", path="", detail=False)
class BackupTargetListView(generic.ObjectListView):
    queryset = BackupTarget.objects.select_related(
        "device",
        "last_revision",
        "policy_override",
        "retention_override",
        "remote_retention_policy",
    )
    table = tables.BackupTargetTable
    filterset = filtersets.BackupTargetFilterSet
    filterset_form = forms.BackupTargetFilterForm

    def get_permitted_actions(self, user, model=None):
        actions = super().get_permitted_actions(user, model=model)
        if not _can_assign_target_retention(user):
            return tuple(action for action in actions if action.name != "add")
        return actions


@register_model_view(BackupTarget)
class BackupTargetView(generic.ObjectView):
    queryset = BackupTarget.objects.select_related(
        "device",
        "policy_override",
        "policy_override__retention_policy",
        "retention_override",
        "remote_retention_policy",
        "credential_override",
        "connection_override",
        "receiver_override",
        "last_revision",
    )
    template_name = "netbox_config_backup/backuptarget.html"

    def get_extra_context(self, request, instance):
        now = timezone.now()
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        local_storage = (
            BackupDestination.objects.filter(
                protocol=DestinationProtocolChoices.LOCAL,
                is_default=True,
            )
            .select_related("local_retention_policy")
            .first()
        )
        local_policy = (
            effective_local_retention_policy(instance, local_storage)
            if local_storage is not None
            else None
        )
        visible_runs = instance.runs.all().restrict(request.user, "view")
        visible_revisions = instance.revisions.all().restrict(request.user, "view")
        health = evaluate_target_health(
            instance,
            now=now,
            grace_minutes=plugin_settings["stale_target_grace_minutes"],
        )
        latest_failure = visible_runs.filter(status__in=FAILURE_RUN_STATUSES).first()
        stuck_run = stuck_run_queryset(
            visible_runs,
            now=now,
            timeout_minutes=plugin_settings["stale_run_minutes"],
        ).first()
        return {
            "recent_runs": visible_runs.select_related("revision")[:10],
            "recent_revisions": visible_revisions[:10],
            "health": health,
            "latest_failure": latest_failure,
            "stuck_run": stuck_run,
            "stuck_run_minutes": plugin_settings["stale_run_minutes"],
            "effective_local_policy": local_policy,
            "effective_local_policy_source": (
                local_retention_policy_source(instance, local_storage)
                if local_storage is not None
                else "Keep indefinitely"
            ),
            "can_run": request.user.has_perm("netbox_config_backup.add_backuprun"),
            "can_test": request.user.has_perm("netbox_config_backup.add_backuprun"),
            "can_preview_retention": request.user.has_perms(
                (
                    "netbox_config_backup.view_configrevision",
                    "netbox_config_backup.view_backuprun",
                    "netbox_config_backup.view_retentionpolicy",
                    "netbox_config_backup.view_remoteretentionpolicy",
                    "netbox_config_backup.view_revisionreplica",
                    "netbox_config_backup.view_backupdestination",
                )
            ),
        }


@register_model_view(BackupTarget, "edit")
class BackupTargetEditView(generic.ObjectEditView):
    queryset = BackupTarget.objects.all()
    form = forms.BackupTargetForm

    def form_valid(self, form):
        original = BackupTarget.objects.select_related(
            "policy_override__retention_policy",
            "retention_override",
        ).get(pk=form.instance.pk)
        selected_policy = form.cleaned_data.get("policy_override")
        selected_local_retention = form.cleaned_data.get("retention_override")
        selected_remote_retention = form.cleaned_data.get("remote_retention_policy")
        _assert_target_retention_assignment_permissions(
            self.request.user,
            local_retention_changed=(
                _effective_local_retention_policy_id(
                    original.policy_override,
                    original.retention_override,
                )
                != _effective_local_retention_policy_id(
                    selected_policy,
                    selected_local_retention,
                )
            ),
            remote_retention_changed=(
                original.remote_retention_policy_id
                != getattr(selected_remote_retention, "pk", None)
            ),
        )
        return super().form_valid(form)


def _effective_local_retention_policy_id(policy, override) -> int | None:
    if override is not None:
        return override.pk
    return getattr(policy, "retention_policy_id", None)


def _assert_target_retention_assignment_permissions(
    user,
    *,
    local_retention_changed: bool,
    remote_retention_changed: bool,
) -> None:
    """Keep policy assignment as privileged as the deletion it can schedule."""

    if (local_retention_changed or remote_retention_changed) and not (
        OperationalSettings.objects.restrict(user, "change").filter(singleton=True).exists()
    ):
        # Retention dispatchers run as system jobs. Requiring object-level
        # control of the singleton runtime settings prevents a constrained
        # delete permission on an unrelated object from authorizing policy
        # assignment for this target.
        raise PermissionDenied
    if local_retention_changed and not user.has_perms(
        (
            "netbox_config_backup.delete_configartifact",
            "netbox_config_backup.delete_configrevision",
            "netbox_config_backup.delete_backuprun",
            "netbox_config_backup.delete_revisionreplica",
        )
    ):
        raise PermissionDenied
    if remote_retention_changed and not user.has_perms(
        (
            "netbox_config_backup.delete_configartifact",
            "netbox_config_backup.delete_revisionreplica",
            "netbox_config_backup.delete_configrevision",
        )
    ):
        raise PermissionDenied


def _can_assign_target_retention(user) -> bool:
    try:
        _assert_target_retention_assignment_permissions(
            user,
            local_retention_changed=True,
            remote_retention_changed=False,
        )
    except PermissionDenied:
        return False
    return True


@register_model_view(BackupTarget, "add", detail=False)
class BackupTargetQuickSetupView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "netbox_config_backup/quick_setup.html"
    form_class = forms.QuickSetupForm
    permission_required = (
        "netbox_config_backup.add_backuptarget",
        "netbox_config_backup.add_connectionprofile",
        "netbox_config_backup.add_credentialprofile",
        "netbox_config_backup.add_storedcredential",
        "netbox_config_backup.add_backuppolicy",
        "netbox_config_backup.add_retentionpolicy",
        "netbox_config_backup.change_operationalsettings",
        "netbox_config_backup.delete_backuprun",
        "netbox_config_backup.delete_configartifact",
        "netbox_config_backup.delete_configrevision",
        "netbox_config_backup.delete_revisionreplica",
    )
    raise_exception = True

    def form_valid(self, form):
        _assert_target_retention_assignment_permissions(
            self.request.user,
            local_retention_changed=True,
            remote_retention_changed=bool(form.cleaned_data.get("remote_retention_days")),
        )
        if form.cleaned_data.get("remote_retention_days") and not self.request.user.has_perm(
            "netbox_config_backup.add_remoteretentionpolicy"
        ):
            raise PermissionDenied
        create_and_test = "_create_and_test" in self.request.POST
        if create_and_test and not self.request.user.has_perm("netbox_config_backup.add_backuprun"):
            raise PermissionDenied

        result = create_quick_setup(
            device=form.cleaned_data["device"],
            driver_id=form.cleaned_data["driver_id"],
            connection_profile=form.cleaned_data["connection_profile"],
            credential_profile=form.cleaned_data["credential_profile"],
            receiver_profile=form.cleaned_data["receiver_profile"],
            allow_device_export=form.cleaned_data["allow_device_export"],
            sync_receiver_credentials=form.cleaned_data["sync_receiver_credentials"],
            restore_point=form.cleaned_data["restore_point"],
            port=form.cleaned_data["port"],
            protocol=form.cleaned_data["protocol"],
            verify_host_key=(
                form.cleaned_data["host_key_policy"] != SSHHostKeyPolicyChoices.DISABLED
            ),
            auto_trust_first_host_key=(
                form.cleaned_data["host_key_policy"] == SSHHostKeyPolicyChoices.TRUST_ON_FIRST_USE
            ),
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
            schedule=form.cleaned_data["schedule"],
            retention_days=form.cleaned_data["retention_days"],
            remote_retention_days=form.cleaned_data["remote_retention_days"],
        )
        target = result.target
        messages.success(request=self.request, message=f"Backup device {target.device} created.")

        if create_and_test:
            job = ConnectionTestJob.enqueue(
                target_id=target.pk,
                instance=target,
                user=self.request.user,
                queue_name=BACKUP_QUEUE,
            )
            messages.info(
                request=self.request,
                message=(
                    f"Connection test for {target.device} was queued. "
                    "This page will update when the test finishes."
                ),
            )
            return redirect(
                "plugins:netbox_config_backup:backuptarget_connection_test_result",
                pk=target.pk,
                job_id=job.job_id,
            )
        return redirect(target.get_absolute_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        device_driver_defaults = dict(
            Device.objects.filter(
                config_backup_target__isnull=True,
                platform__config_backup_mapping__enabled=True,
            ).values_list("pk", "platform__config_backup_mapping__driver_id")
        )
        context.update(
            {
                "return_url": reverse("plugins:netbox_config_backup:backuptarget_list"),
                "can_test": self.request.user.has_perm("netbox_config_backup.add_backuprun"),
                "device_driver_defaults": device_driver_defaults,
                "receiver_protocols": dict(
                    SftpReceiverProfile.objects.filter(enabled=True).values_list("pk", "protocol")
                ),
            }
        )
        return context


@register_model_view(BackupTarget, "delete")
class BackupTargetDeleteView(generic.ObjectDeleteView):
    queryset = BackupTarget.objects.select_related("connection_override", "credential_override")

    def _get_dependent_objects(self, obj):
        return {
            BackupRun: list(BackupRun.objects.filter(target=obj)),
            ConfigRevision: list(ConfigRevision.objects.filter(target=obj)),
            ConfigArtifact: [
                str(artifact) for artifact in ConfigArtifact.objects.filter(revision__target=obj)
            ],
        }

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and request.user.is_authenticated:
            targets = self.queryset.filter(pk=kwargs.get("pk"))
            if targets.exists():
                _assert_target_delete_permissions(targets, request.user)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        logger = logging.getLogger("netbox_config_backup.views.BackupTargetDeleteView")
        target = self.get_object(**kwargs)
        form = DeleteForm(request.POST, instance=target)
        if not form.is_valid():
            return super().post(request, *args, **kwargs)

        target_label = str(target)
        return_url = form.cleaned_data.get("return_url")
        # The deletion service locks and deletes a fresh database instance, so
        # the view's original instance keeps its primary key. Deriving the
        # fallback from that stale instance would redirect to the now-deleted
        # detail page and make a successful deletion look like a failure.
        fallback_url = reverse("plugins:netbox_config_backup:backuptarget_list")
        if hasattr(target, "snapshot"):
            target.snapshot()
        target._changelog_message = form.cleaned_data.pop("changelog_message", "")

        try:
            summary = delete_backup_target(target)
        except TargetDeletionError as exc:
            logger.info("Target deletion was safely aborted: %s", exc)
            messages.error(request, str(exc))
            return redirect(target.get_absolute_url())

        messages.success(
            request,
            (
                f"Deleted backup device {target_label}, {summary.run_count} runs, "
                f"{summary.revision_count} revisions, and {summary.artifact_count} artifacts."
            ),
        )
        if return_url and return_url.startswith("/"):
            return redirect(return_url)
        return redirect(fallback_url)


@register_model_view(BackupTarget, "bulk_delete", detail=False)
class BackupTargetBulkDeleteView(generic.BulkDeleteView):
    queryset = BackupTarget.objects.select_related(
        "device",
        "connection_override",
        "credential_override",
    )
    table = tables.BackupTargetTable
    filterset = filtersets.BackupTargetFilterSet

    def get_queryset(self, request):
        return super().get_queryset(request).restrict(request.user, "delete")

    def _selected_ids(self, request) -> list[int]:
        if request.POST.get("_all"):
            queryset = self.queryset
            if self.filterset is not None:
                queryset = self.filterset(request.GET, queryset, request=request).qs
            return list(queryset.values_list("pk", flat=True))
        try:
            return [int(pk) for pk in request.POST.getlist("pk")]
        except (TypeError, ValueError) as exc:
            raise PermissionDenied from exc

    def _render_confirmation(self, request, selected_ids, selected, form=None):
        if form is None:
            form = self._delete_form(
                initial={"pk": selected_ids, "return_url": self.get_return_url(request)}
            )
        return render(
            request,
            self.template_name,
            {
                "model": BackupTarget,
                "form": form,
                "table": self.table(selected, orderable=False),
                "return_url": self.get_return_url(request),
                **self.get_extra_context(request),
            },
        )

    @staticmethod
    def _delete_form(*args, **kwargs):
        """Build a synchronous target-deletion form.

        NetBox's generic bulk form offers a background-job switch. A queued
        view job can be picked up by a worker image which does not contain the
        plugin, leaving the selected backup devices untouched. Target deletion
        already reports progress and failures explicitly, so keep this action
        deterministic and execute it in the current request.
        """
        form = BulkDeleteForm(BackupTarget, *args, **kwargs)
        form.fields.pop("background_job", None)
        if hasattr(form, "meta_fields"):
            form.meta_fields = [
                field_name
                for field_name in form.meta_fields
                if field_name != "background_job"
            ]
        return form

    def post(self, request, **kwargs):
        selected_ids = self._selected_ids(request)
        selected = self.queryset.filter(pk__in=selected_ids)
        if selected.count() != len(set(selected_ids)):
            raise PermissionDenied

        if "_confirm" not in request.POST:
            if not selected_ids:
                messages.warning(request, "No backup devices were selected.")
                return redirect(self.get_return_url(request))
            return self._render_confirmation(request, selected_ids, selected)

        form = self._delete_form(request.POST)
        if not form.is_valid():
            return self._render_confirmation(request, selected_ids, selected, form)

        confirmed_ids = set(form.cleaned_data["pk"].values_list("pk", flat=True))
        if confirmed_ids != set(selected_ids):
            raise PermissionDenied
        _assert_target_delete_permissions(selected, request.user)

        if BackupRun.objects.filter(
            target_id__in=selected_ids,
            status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
        ).exists():
            message = "Bulk deletion was cancelled because a selected device has an active backup."
            if is_background_request(request):
                request.job.logger.error(message)
                raise JobFailed
            messages.error(request, message)
            return redirect(self.get_return_url(request))

        totals = {"targets": 0, "runs": 0, "revisions": 0, "artifacts": 0}
        changelog_message = form.cleaned_data.get("changelog_message", "")
        logger = logging.getLogger("netbox_config_backup.views.BackupTargetBulkDeleteView")
        for target in selected.order_by("pk"):
            if hasattr(target, "snapshot"):
                target.snapshot()
            target._changelog_message = changelog_message
            try:
                summary = delete_backup_target(target)
            except TargetDeletionError as exc:
                logger.info("Bulk target deletion was safely aborted: %s", exc)
                if is_background_request(request):
                    request.job.logger.error(str(exc))
                    raise JobFailed from exc
                messages.error(
                    request,
                    f"Deletion stopped after {totals['targets']} devices: {exc}",
                )
                return redirect(self.get_return_url(request))
            totals["targets"] += 1
            totals["runs"] += summary.run_count
            totals["revisions"] += summary.revision_count
            totals["artifacts"] += summary.artifact_count

        message = (
            f"Deleted {totals['targets']} backup devices, {totals['runs']} runs, "
            f"{totals['revisions']} revisions, and {totals['artifacts']} artifacts."
        )
        logger.info(message)
        if is_background_request(request):
            request.job.logger.info(message)
            return None
        messages.success(request, message)
        return redirect(self.get_return_url(request))


@register_model_view(BackupTarget, "run", path="run")
class BackupTargetRunView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.add_backuprun",
    )
    raise_exception = True

    def post(self, request, pk):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related("device"),
            pk=pk,
        )
        if not target.enabled:
            messages.error(request, "Backup target is disabled.")
            return redirect(target.get_absolute_url())

        mapping = (
            PlatformMapping.objects.filter(
                platform_id=target.device.platform_id, enabled=True
            ).first()
            if target.device.platform_id
            else None
        )
        driver_id = target.driver_override or (mapping.driver_id if mapping else "")
        if not driver_id or not driver_registry.contains(driver_id):
            messages.error(request, "No supported backup driver is configured for this target.")
            return redirect(target.get_absolute_url())

        worker_available = _backup_worker_available()
        if worker_available is not True:
            messages.error(
                request,
                (
                    "No live Config Backup worker is listening on the backup queue. "
                    "The backup was not queued; start config-backup-worker and try again."
                    if worker_available is False
                    else "The Config Backup worker state could not be verified. The backup was not queued."
                ),
            )
            return redirect(target.get_absolute_url())

        try:
            run = enqueue_backup_run(
                target,
                source=RunSourceChoices.MANUAL,
                user=request.user,
            )
        except IntegrityError:
            messages.warning(request, "This target already has a queued or running backup.")
            return redirect(target.get_absolute_url())

        messages.info(
            request,
            (
                f"Configuration backup for {target.device} was queued. "
                "The run status will update when collection finishes."
            ),
        )
        return redirect(run.get_absolute_url())


@register_model_view(BackupTarget, "test_connection", path="test-connection")
class BackupTargetConnectionTestView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.add_backuprun",
    )
    raise_exception = True

    def post(self, request, pk):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related("device"),
            pk=pk,
        )
        mapping = (
            PlatformMapping.objects.filter(
                platform_id=target.device.platform_id, enabled=True
            ).first()
            if target.device.platform_id
            else None
        )
        driver_id = target.driver_override or (mapping.driver_id if mapping else "")
        if not driver_id or not driver_registry.contains(driver_id):
            messages.error(request, "No supported backup driver is configured for this target.")
            return redirect(target.get_absolute_url())

        active_job = (
            ConnectionTestJob.get_jobs(target)
            .filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)
            .order_by("-created")
            .first()
        )
        if active_job:
            messages.info(request, "A connection test is already in progress for this device.")
            return redirect(
                "plugins:netbox_config_backup:backuptarget_connection_test_result",
                pk=target.pk,
                job_id=active_job.job_id,
            )

        job = ConnectionTestJob.enqueue(
            target_id=target.pk,
            instance=target,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.info(
            request,
            (
                f"Connection test for {target.device} was queued. "
                "This page will update when the test finishes."
            ),
        )
        return redirect(
            "plugins:netbox_config_backup:backuptarget_connection_test_result",
            pk=target.pk,
            job_id=job.job_id,
        )


class BackupTargetConnectionTestResultView(
    PermissionRequiredMixin,
    LoginRequiredMixin,
    TemplateView,
):
    template_name = "netbox_config_backup/connection_test.html"
    permission_required = "netbox_config_backup.view_backuptarget"
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(
            BackupTarget.objects.restrict(self.request.user, "view").select_related("device"),
            pk=self.kwargs["pk"],
        )
        job = _connection_test_job(target, self.kwargs["job_id"])
        context.update(
            {
                "target": target,
                "job": job,
                "status_payload": _connection_test_status(job, target),
                "status_url": reverse(
                    "plugins:netbox_config_backup:backuptarget_connection_test_status",
                    kwargs={"pk": target.pk, "job_id": job.job_id},
                ),
                "can_test": self.request.user.has_perm("netbox_config_backup.add_backuprun"),
                "can_view_job": self.request.user.has_perm("core.view_job"),
                "can_manage_host_keys": self.request.user.has_perm(
                    "netbox_config_backup.change_sshhostkey"
                ),
                "trust_host_key_url": reverse(
                    "plugins:netbox_config_backup:backuptarget_trust_host_key",
                    kwargs={"pk": target.pk, "job_id": job.job_id},
                ),
                "reject_host_key_url": reverse(
                    "plugins:netbox_config_backup:backuptarget_reject_host_key",
                    kwargs={"pk": target.pk, "job_id": job.job_id},
                ),
            }
        )
        return context


class BackupTargetConnectionTestStatusView(
    PermissionRequiredMixin,
    LoginRequiredMixin,
    View,
):
    permission_required = "netbox_config_backup.view_backuptarget"
    raise_exception = True

    def get(self, request, pk, job_id):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view"),
            pk=pk,
        )
        job = _connection_test_job(target, job_id)
        response = JsonResponse(_connection_test_status(job, target))
        response["Cache-Control"] = "no-store"
        return response


def _job_host_key_candidate(request, target, job):
    candidate_id = (
        ((job.data or {}).get("connection_test") or {}).get("host_key_candidate", {}).get("id")
    )
    if not candidate_id:
        raise Http404
    return get_object_or_404(
        SSHHostKey.objects.restrict(request.user, "change"),
        pk=candidate_id,
        target=target,
    )


class BackupTargetTrustHostKeyView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.change_sshhostkey",
        "netbox_config_backup.add_backuprun",
    )
    raise_exception = True

    def post(self, request, pk, job_id):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related("device"),
            pk=pk,
        )
        job = _connection_test_job(target, job_id)
        candidate = _job_host_key_candidate(request, target, job)
        trust_host_key(candidate.pk, user=request.user)
        retry_job = ConnectionTestJob.enqueue(
            target_id=target.pk,
            instance=target,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.success(
            request,
            "SSH host key was approved. The connection test is running again.",
        )
        return redirect(
            "plugins:netbox_config_backup:backuptarget_connection_test_result",
            pk=target.pk,
            job_id=retry_job.job_id,
        )


class BackupTargetRejectHostKeyView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.change_sshhostkey",
    )
    raise_exception = True

    def post(self, request, pk, job_id):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view"),
            pk=pk,
        )
        job = _connection_test_job(target, job_id)
        candidate = _job_host_key_candidate(request, target, job)
        reject_host_key(candidate.pk, user=request.user)
        job_data = dict(job.data or {})
        result_data = dict(job_data.get("connection_test") or {})
        candidate_data = dict(result_data.get("host_key_candidate") or {})
        candidate_data["status"] = "rejected"
        result_data["host_key_candidate"] = candidate_data
        job_data["connection_test"] = result_data
        job.data = job_data
        job.save(update_fields=("data",))
        messages.warning(request, "SSH host key was rejected and was not trusted.")
        return redirect(
            "plugins:netbox_config_backup:backuptarget_connection_test_result",
            pk=target.pk,
            job_id=job.job_id,
        )


class SSHHostKeyListView(PermissionRequiredMixin, LoginRequiredMixin, TemplateView):
    template_name = "netbox_config_backup/ssh_host_keys.html"
    permission_required = "netbox_config_backup.view_sshhostkey"
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visible_targets = BackupTarget.objects.restrict(self.request.user, "view")
        keys = (
            SSHHostKey.objects.restrict(self.request.user, "view")
            .filter(target_id__in=visible_targets.values("pk"))
            .select_related("target__device", "approved_by")
        )
        context.update(
            {
                "host_keys": keys,
                "pending_count": keys.filter(status="pending").count(),
                "trusted_count": keys.filter(status="trusted").count(),
                "rejected_count": keys.filter(status="rejected").count(),
                "can_manage_host_keys": self.request.user.has_perm(
                    "netbox_config_backup.change_sshhostkey"
                ),
                "can_scan_host_keys": self.request.user.has_perm(
                    "netbox_config_backup.add_sshhostkey"
                ),
            }
        )
        return context


class SSHHostKeyScanView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.add_sshhostkey",
    )
    raise_exception = True

    def post(self, request):
        target_ids = list(
            BackupTarget.objects.restrict(request.user, "view")
            .filter(enabled=True)
            .values_list("pk", flat=True)[:1000]
        )
        if not target_ids:
            messages.info(request, "There are no enabled backup devices to scan.")
            return redirect("plugins:netbox_config_backup:ssh_host_key_list")
        SSHHostKeyScanJob.enqueue(
            target_ids=target_ids,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.info(
            request,
            "SSH identity discovery was queued. Refresh this page to see new fingerprints.",
        )
        return redirect("plugins:netbox_config_backup:ssh_host_key_list")


def _manageable_host_key(request, pk):
    visible_targets = BackupTarget.objects.restrict(request.user, "view").values("pk")
    return get_object_or_404(
        SSHHostKey.objects.restrict(request.user, "change"),
        pk=pk,
        target_id__in=visible_targets,
    )


class SSHHostKeyTrustView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_sshhostkey"
    raise_exception = True

    def post(self, request, pk):
        candidate = _manageable_host_key(request, pk)
        trust_host_key(candidate.pk, user=request.user)
        messages.success(request, "SSH host key was approved.")
        return redirect("plugins:netbox_config_backup:ssh_host_key_list")


class SSHHostKeyRejectView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_sshhostkey"
    raise_exception = True

    def post(self, request, pk):
        candidate = _manageable_host_key(request, pk)
        reject_host_key(candidate.pk, user=request.user)
        messages.warning(request, "SSH host key was rejected.")
        return redirect("plugins:netbox_config_backup:ssh_host_key_list")


@register_model_view(BackupTarget, "reschedule", path="reschedule")
class BackupTargetRescheduleView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_backuptarget"

    def post(self, request, pk):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "change").select_related(
                "policy_override", "device__site"
            ),
            pk=pk,
        )
        apply_target_schedule(target, now=timezone.now())
        if target.next_run_at:
            messages.success(request, f"Next backup scheduled for {target.next_run_at}.")
        else:
            messages.warning(request, "Target has no enabled backup policy; schedule is disabled.")
        return redirect(target.get_absolute_url())


@register_model_view(BackupTarget, "retention_preview", path="retention-preview")
class BackupTargetRetentionPreviewView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_backuprun",
        "netbox_config_backup.view_retentionpolicy",
        "netbox_config_backup.view_remoteretentionpolicy",
        "netbox_config_backup.view_revisionreplica",
        "netbox_config_backup.view_backupdestination",
    )
    raise_exception = True
    template_name = "netbox_config_backup/retention_preview.html"

    def get(self, request, pk):
        target = get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related(
                "device",
                "retention_override",
                "remote_retention_policy",
                "policy_override__retention_policy",
            ),
            pk=pk,
        )
        context = _retention_preview_context(target, now=timezone.now(), user=request.user)
        policy = context["local_policy"]
        if (
            policy
            and not RetentionPolicy.objects.restrict(request.user, "view")
            .filter(pk=policy.pk)
            .exists()
        ):
            raise PermissionDenied
        active_ftp_replicas = _remote_retention_replicas(target)
        if not _queryset_fully_permitted(active_ftp_replicas, request.user, "view"):
            raise PermissionDenied
        visible_remote_policy_ids = {
            item["policy"].pk
            for item in context["remote_storage_plans"]
            if item["policy"] is not None
        }
        if visible_remote_policy_ids and (
            RemoteRetentionPolicy.objects.restrict(request.user, "view")
            .filter(pk__in=visible_remote_policy_ids)
            .count()
            != len(visible_remote_policy_ids)
        ):
            raise PermissionDenied
        revisions = target.revisions.all()
        runs = target.runs.all()
        artifacts = ConfigArtifact.objects.filter(revision__target=target)
        context.update(
            {
                "object": target,
                "can_apply_retention": (
                    request.user.has_perm("netbox_config_backup.delete_revisionreplica")
                    and _queryset_fully_permitted(
                        RevisionReplica.objects.filter(revision__target=target),
                        request.user,
                        "delete",
                    )
                    and _queryset_fully_permitted(revisions, request.user, "delete")
                    and _queryset_fully_permitted(runs, request.user, "delete")
                    and _queryset_fully_permitted(artifacts, request.user, "delete")
                ),
                "can_apply_remote_retention": (
                    request.user.has_perm("netbox_config_backup.delete_revisionreplica")
                    and request.user.has_perm("netbox_config_backup.delete_configrevision")
                    and request.user.has_perm("netbox_config_backup.delete_configartifact")
                    and _queryset_fully_permitted(
                        RevisionReplica.objects.filter(revision__target=target),
                        request.user,
                        "delete",
                    )
                    and _queryset_fully_permitted(
                        target.revisions.all(),
                        request.user,
                        "delete",
                    )
                    and _queryset_fully_permitted(
                        ConfigArtifact.objects.filter(revision__target=target),
                        request.user,
                        "delete",
                    )
                ),
            }
        )
        return render(
            request,
            self.template_name,
            context,
        )


@register_model_view(BackupTarget, "retention_cleanup", path="retention-cleanup")
class BackupTargetRetentionCleanupView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_backuprun",
        "netbox_config_backup.view_retentionpolicy",
        "netbox_config_backup.delete_configrevision",
        "netbox_config_backup.delete_configartifact",
        "netbox_config_backup.delete_backuprun",
        "netbox_config_backup.delete_revisionreplica",
    )
    raise_exception = True
    template_name = "netbox_config_backup/retention_cleanup_confirm.html"

    def _target(self, request, pk):
        return get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related(
                "device",
                "retention_override",
                "policy_override__retention_policy",
            ),
            pk=pk,
        )

    @staticmethod
    def _assert_history_permissions(request, target):
        checks = (
            (target.revisions.all(), "view"),
            (target.revisions.all(), "delete"),
            (target.runs.all(), "view"),
            (target.runs.all(), "delete"),
            (ConfigArtifact.objects.filter(revision__target=target), "delete"),
            (RevisionReplica.objects.filter(revision__target=target), "delete"),
        )
        if not all(
            _queryset_fully_permitted(queryset, request.user, action) for queryset, action in checks
        ):
            raise PermissionDenied
        policy = effective_retention_policy(target)
        local_storage = (
            BackupDestination.objects.filter(
                protocol=DestinationProtocolChoices.LOCAL,
                is_default=True,
            )
            .select_related("local_retention_policy")
            .first()
        )
        if local_storage is not None:
            if (
                not BackupDestination.objects.restrict(request.user, "view")
                .filter(pk=local_storage.pk)
                .exists()
            ):
                raise PermissionDenied
            policy = effective_local_retention_policy(target, local_storage)
        if (
            policy
            and not RetentionPolicy.objects.restrict(request.user, "view")
            .filter(pk=policy.pk)
            .exists()
        ):
            raise PermissionDenied

    def get(self, request, pk):
        target = self._target(request, pk)
        self._assert_history_permissions(request, target)
        context = _retention_preview_context(target, now=timezone.now())
        context.update(
            {
                "object": target,
                "form": forms.RetentionCleanupConfirmationForm(),
            }
        )
        return render(request, self.template_name, context)

    def post(self, request, pk):
        target = self._target(request, pk)
        self._assert_history_permissions(request, target)
        form = forms.RetentionCleanupConfirmationForm(request.POST)
        context = _retention_preview_context(target, now=timezone.now())
        if not form.is_valid():
            context.update({"object": target, "form": form})
            return render(request, self.template_name, context, status=400)
        plan = context["plan"]
        if plan is None:
            messages.error(request, "The backup target has no effective Local retention profile.")
            return redirect(target.get_absolute_url())
        if not plan.revisions_to_delete and not plan.runs_to_delete:
            messages.info(request, "The current retention plan has nothing to delete.")
            return redirect(
                "plugins:netbox_config_backup:backuptarget_retention_preview",
                pk=target.pk,
            )
        if target.runs.filter(status__in=("queued", "running")).exists():
            messages.error(request, "Retention cleanup cannot run during an active backup.")
            return redirect(
                "plugins:netbox_config_backup:backuptarget_retention_preview",
                pk=target.pk,
            )

        job = RetentionCleanupJob.enqueue(
            target_id=target.pk,
            instance=target,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.info(
            request,
            (
                f"Retention cleanup for {target.device} was queued. "
                "The plan will be recomputed safely when the job starts."
            ),
        )
        if request.user.has_perm("core.view_job"):
            return redirect(job.get_absolute_url())
        return redirect(target.get_absolute_url())


@register_model_view(BackupTarget, "remote_retention_cleanup", path="ftp-retention-cleanup")
class BackupTargetRemoteRetentionCleanupView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuptarget",
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_remoteretentionpolicy",
        "netbox_config_backup.view_revisionreplica",
        "netbox_config_backup.delete_configartifact",
        "netbox_config_backup.delete_revisionreplica",
        "netbox_config_backup.delete_configrevision",
    )
    raise_exception = True
    template_name = "netbox_config_backup/remote_retention_cleanup_confirm.html"

    def _target(self, request, pk):
        return get_object_or_404(
            BackupTarget.objects.restrict(request.user, "view").select_related(
                "device",
                "retention_override",
                "remote_retention_policy",
                "policy_override__retention_policy",
            ),
            pk=pk,
        )

    @staticmethod
    def _assert_permissions(request, target):
        replicas = _remote_retention_replicas(target)
        cascade_checks = (
            (target.revisions.all(), "delete"),
            (ConfigArtifact.objects.filter(revision__target=target), "delete"),
            (RevisionReplica.objects.filter(revision__target=target), "delete"),
        )
        if not all(
            _queryset_fully_permitted(queryset, request.user, action)
            for queryset, action in cascade_checks
        ):
            raise PermissionDenied
        storages = (
            BackupDestination.objects.filter(
                protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
                enabled=True,
                replicas__in=replicas,
            )
            .select_related("remote_retention_policy")
            .distinct()
        )
        if not _queryset_fully_permitted(storages, request.user, "view"):
            raise PermissionDenied
        policy_ids = {
            policy.pk
            for storage in storages
            if (policy := effective_remote_retention_policy(target, storage)) is not None
        }
        if policy_ids and (
            RemoteRetentionPolicy.objects.restrict(request.user, "view")
            .filter(pk__in=policy_ids)
            .count()
            != len(policy_ids)
        ):
            raise PermissionDenied

    def get(self, request, pk):
        target = self._target(request, pk)
        self._assert_permissions(request, target)
        context = _retention_preview_context(target, now=timezone.now())
        context.update(
            {
                "object": target,
                "form": forms.RemoteRetentionCleanupConfirmationForm(),
            }
        )
        return render(request, self.template_name, context)

    def post(self, request, pk):
        target = self._target(request, pk)
        self._assert_permissions(request, target)
        form = forms.RemoteRetentionCleanupConfirmationForm(request.POST)
        context = _retention_preview_context(target, now=timezone.now())
        if not form.is_valid():
            context.update({"object": target, "form": form})
            return render(request, self.template_name, context, status=400)
        if not context["remote_has_policy"]:
            messages.error(request, "No enabled remote storage has an effective retention profile.")
            return redirect(target.get_absolute_url())
        if not context["remote_revisions_to_delete"]:
            messages.info(request, "The current remote retention plan has nothing to delete.")
            return redirect(
                "plugins:netbox_config_backup:backuptarget_retention_preview",
                pk=target.pk,
            )
        if target.runs.filter(status__in=("queued", "running")).exists():
            messages.error(request, "Remote retention cannot run during an active backup.")
            return redirect(
                "plugins:netbox_config_backup:backuptarget_retention_preview",
                pk=target.pk,
            )

        job = RemoteRetentionCleanupJob.enqueue(
            target_id=target.pk,
            instance=target,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.info(
            request,
            f"Remote retention cleanup for {target.device} was queued and will recompute its plan.",
        )
        if request.user.has_perm("core.view_job"):
            return redirect(job.get_absolute_url())
        return redirect(target.get_absolute_url())


@register_model_view(BackupRun, "list", path="", detail=False)
class BackupRunListView(generic.ObjectListView):
    queryset = BackupRun.objects.select_related("target__device", "revision")
    table = tables.BackupRunTable
    filterset = filtersets.BackupRunFilterSet
    filterset_form = forms.BackupRunFilterForm
    actions = ()


@register_model_view(BackupRun)
class BackupRunView(generic.ObjectView):
    queryset = BackupRun.objects.select_related("target__device", "revision", "triggered_by")
    template_name = "netbox_config_backup/backuprun.html"
    actions = ()

    def get_extra_context(self, request, instance):
        timeout_minutes = settings.PLUGINS_CONFIG["netbox_config_backup"]["stale_run_minutes"]
        can_change_target = (
            request.user.has_perm("netbox_config_backup.change_backuptarget")
            and BackupTarget.objects.restrict(request.user, "change")
            .filter(pk=instance.target_id)
            .exists()
        )
        return {
            "is_stuck": is_run_stuck(
                instance,
                now=timezone.now(),
                timeout_minutes=timeout_minutes,
            ),
            "stuck_run_minutes": timeout_minutes,
            "can_cancel": instance.status == RunStatusChoices.QUEUED and can_change_target,
        }


class BackupRunCancelView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_backuprun",
        "netbox_config_backup.change_backuptarget",
    )
    raise_exception = True

    def post(self, request, pk):
        run = get_object_or_404(
            BackupRun.objects.restrict(request.user, "view").select_related("target__device"),
            pk=pk,
        )
        if not (
            BackupTarget.objects.restrict(request.user, "change")
            .filter(pk=run.target_id)
            .exists()
        ):
            raise PermissionDenied
        try:
            result = cancel_queued_backup_run(run.pk)
        except BackupRunCancellationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                (
                    f"Queued backup for {run.target.device} was cancelled."
                    + (" Its background job was removed." if result.job_removed else "")
                ),
            )
        return redirect(run.get_absolute_url())


@register_model_view(ConfigRevision, "list", path="", detail=False)
class ConfigRevisionListView(generic.ObjectListView):
    queryset = ConfigRevision.objects.select_related("target__device")
    table = tables.ConfigRevisionTable
    filterset = filtersets.ConfigRevisionFilterSet
    filterset_form = forms.ConfigRevisionFilterForm
    actions = ()


@register_model_view(ConfigRevision)
class ConfigRevisionView(generic.ObjectView):
    queryset = ConfigRevision.objects.select_related(
        "target__device", "previous_revision"
    ).prefetch_related("artifacts")
    template_name = "netbox_config_backup/configrevision.html"
    actions = ()

    def get_extra_context(self, request, instance):
        artifact_queryset = ConfigArtifact.objects.filter(revision=instance)
        replica_queryset = RevisionReplica.objects.filter(revision=instance)
        local_artifact_queryset = artifact_queryset.filter(local_available=True)
        local_primary_artifact_queryset = local_artifact_queryset.filter(is_primary=True)
        can_view_all_artifacts = bool(
            artifact_queryset.exists()
            and _queryset_fully_permitted(artifact_queryset, request.user, "view")
        )
        visible_destination_ids = (
            BackupDestination.objects.restrict(request.user, "view")
            .filter(protocol=DestinationProtocolChoices.FTP)
            .values("pk")
        )
        ftp_replicas = (
            RevisionReplica.objects.restrict(request.user, "view")
            .filter(
                revision=instance,
                destination_id__in=visible_destination_ids,
                destination__protocol=DestinationProtocolChoices.FTP,
                status="success",
                remote_available=True,
                remote_deleted_at__isnull=True,
            )
            .select_related("destination")
            .order_by("destination__name")
        )
        return {
            "can_view_content": bool(
                local_primary_artifact_queryset.exists()
                and request.user.has_perm("netbox_config_backup.view_configartifact")
            ),
            "can_download_artifacts": bool(
                local_artifact_queryset.filter(
                    Q(is_primary=True) | Q(artifact_type="native_backup")
                ).exists()
                and request.user.has_perm("netbox_config_backup.view_configartifact")
            ),
            "local_copy_available": local_artifact_queryset.exists(),
            "can_change_protection": request.user.has_perm(
                "netbox_config_backup.change_configrevision"
            ),
            "can_delete_everywhere": (
                not instance.protected
                and request.user.has_perms(_REVISION_DELETE_PERMISSIONS)
                and _queryset_fully_permitted(
                    ConfigRevision.objects.filter(pk=instance.pk),
                    request.user,
                    "delete",
                )
                and _queryset_fully_permitted(
                    artifact_queryset,
                    request.user,
                    "delete",
                )
                and _queryset_fully_permitted(
                    replica_queryset,
                    request.user,
                    "delete",
                )
            ),
            "can_prepare_ftp_recovery": request.user.has_perms(
                (
                    "netbox_config_backup.view_configartifact",
                    "netbox_config_backup.view_revisionreplica",
                    "netbox_config_backup.view_backupdestination",
                )
            )
            and can_view_all_artifacts,
            "ftp_replicas": ftp_replicas if can_view_all_artifacts else (),
        }


_REVISION_DELETE_PERMISSIONS = (
    "netbox_config_backup.view_configrevision",
    "netbox_config_backup.delete_configrevision",
    "netbox_config_backup.delete_configartifact",
    "netbox_config_backup.delete_revisionreplica",
    "netbox_config_backup.view_backupdestination",
)


class ConfigRevisionDeleteEverywhereView(
    PermissionRequiredMixin,
    LoginRequiredMixin,
    View,
):
    permission_required = _REVISION_DELETE_PERMISSIONS
    raise_exception = True
    template_name = "netbox_config_backup/configrevision_delete_everywhere.html"

    @staticmethod
    def _revision(request, pk):
        revision = get_object_or_404(
            ConfigRevision.objects.restrict(request.user, "view")
            .select_related("target__device")
            .prefetch_related("artifacts", "replicas__destination"),
            pk=pk,
        )
        checks = (
            (ConfigRevision.objects.filter(pk=revision.pk), "delete"),
            (ConfigArtifact.objects.filter(revision=revision), "delete"),
            (RevisionReplica.objects.filter(revision=revision), "delete"),
            (
                BackupDestination.objects.filter(replicas__revision=revision).distinct(),
                "view",
            ),
        )
        if not all(
            _queryset_fully_permitted(queryset, request.user, action)
            for queryset, action in checks
        ):
            raise PermissionDenied
        return revision

    @staticmethod
    def _context(revision):
        artifacts = list(revision.artifacts.all())
        replicas = list(revision.replicas.all())
        recorded_replicas = [
            replica
            for replica in replicas
            if replica.remote_deleted_at is None
            and (replica.remote_available or replica.remote_path)
        ]
        return {
            "object": revision,
            "artifacts": artifacts,
            "artifact_bytes": sum(artifact.size for artifact in artifacts),
            "local_copy_count": sum(artifact.local_available for artifact in artifacts),
            "recorded_replicas": recorded_replicas,
            "linked_run_count": BackupRun.objects.filter(revision=revision).count(),
            "has_active_run": BackupRun.objects.filter(
                target=revision.target,
                status__in=(RunStatusChoices.QUEUED, RunStatusChoices.RUNNING),
            ).exists(),
            "has_active_replica": any(
                replica.status
                in {
                    ReplicaStatusChoices.QUEUED,
                    ReplicaStatusChoices.RUNNING,
                }
                or (
                    replica.status == ReplicaStatusChoices.FAILED
                    and replica.next_retry_at is not None
                )
                for replica in replicas
            ),
        }

    def get(self, request, pk):
        revision = self._revision(request, pk)
        return render(request, self.template_name, self._context(revision))

    def post(self, request, pk):
        revision = self._revision(request, pk)
        if request.POST.get("confirm") != "yes":
            context = self._context(revision)
            context["confirmation_error"] = True
            return render(request, self.template_name, context, status=400)

        revision_label = str(revision)
        target_id = revision.target_id
        try:
            summary = delete_config_revision_everywhere(revision.pk)
        except RevisionDeletionError as exc:
            logging.getLogger(
                "netbox_config_backup.views.ConfigRevisionDeleteEverywhereView"
            ).info("Revision deletion was safely aborted: %s", exc)
            messages.error(request, str(exc))
            return redirect(
                "plugins:netbox_config_backup:configrevision_delete_everywhere",
                pk=revision.pk,
            )

        message = (
            f"Deleted revision {revision_label}, {summary.artifact_count} database artifact "
            f"record(s), {summary.local_file_count} local file(s), and "
            f"{summary.replica_count} remote copy/copies."
        )
        if summary.quarantine_purge_failures:
            message += (
                f" {summary.quarantine_purge_failures} quarantined local file(s) still "
                "require administrator cleanup."
            )
        messages.success(request, message)
        return redirect(
            f"{reverse('plugins:netbox_config_backup:configrevision_list')}"
            f"?target_id={target_id}"
        )


_FTP_RECOVERY_PERMISSIONS = (
    "netbox_config_backup.view_configrevision",
    "netbox_config_backup.view_configartifact",
    "netbox_config_backup.view_revisionreplica",
    "netbox_config_backup.view_backupdestination",
)


def _ftp_recovery_revision(request, pk):
    revision = get_object_or_404(
        ConfigRevision.objects.restrict(request.user, "view").select_related("target__device"),
        pk=pk,
    )
    artifacts = ConfigArtifact.objects.filter(revision=revision)
    if not artifacts.exists() or not _queryset_fully_permitted(artifacts, request.user, "view"):
        raise PermissionDenied
    return revision


def _ftp_recovery_replica(request, revision, replica_pk):
    visible_destinations = BackupDestination.objects.restrict(request.user, "view").filter(
        protocol=DestinationProtocolChoices.FTP
    )
    return get_object_or_404(
        RevisionReplica.objects.restrict(request.user, "view").select_related(
            "destination", "revision__target__device"
        ),
        pk=replica_pk,
        revision=revision,
        remote_available=True,
        remote_deleted_at__isnull=True,
        destination_id__in=visible_destinations.values("pk"),
        destination__protocol=DestinationProtocolChoices.FTP,
        status="success",
    )


def _ftp_recovery_job(revision, job_id):
    job = get_object_or_404(FtpRecoveryPackageJob.get_jobs(revision.target), job_id=job_id)
    job_revision_id = ((job.data or {}).get("ftp_recovery_package") or {}).get("revision_id")
    if job_revision_id is not None and job_revision_id != revision.pk:
        raise Http404
    return job


class ConfigRevisionFtpRecoveryPrepareView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = _FTP_RECOVERY_PERMISSIONS
    raise_exception = True

    def post(self, request, pk, replica_pk):
        revision = _ftp_recovery_revision(request, pk)
        replica = _ftp_recovery_replica(request, revision, replica_pk)
        job = FtpRecoveryPackageJob.enqueue(
            replica_id=replica.pk,
            package_token=str(uuid4()),
            instance=revision.target,
            user=request.user,
            queue_name=BACKUP_QUEUE,
        )
        messages.info(
            request,
            "The read-only FTP download and integrity verification were queued.",
        )
        return redirect(
            "plugins:netbox_config_backup:configrevision_ftp_recovery_result",
            pk=revision.pk,
            job_id=job.job_id,
        )


class ConfigRevisionFtpRecoveryResultView(
    PermissionRequiredMixin, LoginRequiredMixin, TemplateView
):
    template_name = "netbox_config_backup/ftp_recovery.html"
    permission_required = _FTP_RECOVERY_PERMISSIONS
    raise_exception = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        revision = _ftp_recovery_revision(self.request, self.kwargs["pk"])
        job = _ftp_recovery_job(revision, self.kwargs["job_id"])
        context.update(
            {
                "revision": revision,
                "job": job,
                "status_payload": ftp_recovery_status_payload(job),
                "status_url": reverse(
                    "plugins:netbox_config_backup:configrevision_ftp_recovery_status",
                    kwargs={"pk": revision.pk, "job_id": job.job_id},
                ),
                "download_url": reverse(
                    "plugins:netbox_config_backup:configrevision_ftp_recovery_download",
                    kwargs={"pk": revision.pk, "job_id": job.job_id},
                ),
                "can_view_job": self.request.user.has_perm("core.view_job"),
            }
        )
        return context


class ConfigRevisionFtpRecoveryStatusView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = _FTP_RECOVERY_PERMISSIONS
    raise_exception = True

    def get(self, request, pk, job_id):
        revision = _ftp_recovery_revision(request, pk)
        job = _ftp_recovery_job(revision, job_id)
        response = JsonResponse(ftp_recovery_status_payload(job))
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        return response


class ConfigRevisionFtpRecoveryDownloadView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = _FTP_RECOVERY_PERMISSIONS
    raise_exception = True

    def get(self, request, pk, job_id):
        revision = _ftp_recovery_revision(request, pk)
        job = _ftp_recovery_job(revision, job_id)
        result = (job.data or {}).get("ftp_recovery_package") or {}
        if (
            job.status != "completed"
            or not result.get("ready")
            or result.get("revision_id") != revision.pk
            or recovery_package_is_expired(result.get("expires_at"))
        ):
            raise Http404
        replica = _ftp_recovery_replica(request, revision, result.get("replica_id"))
        if replica.destination_id != result.get("destination_id"):
            raise Http404

        try:
            package_path = validate_recovery_package(
                storage_root=settings.PLUGINS_CONFIG["netbox_config_backup"]["storage_root"],
                package_token=result.get("token"),
                expected_size=int(result.get("size") or -1),
                expected_sha256=str(result.get("sha256") or ""),
            )
            package_handle = package_path.open("rb")
        except (DestinationError, OSError, TypeError, ValueError) as exc:
            logging.getLogger(
                "netbox_config_backup.views.ConfigRevisionFtpRecoveryDownloadView"
            ).warning(
                "Verified FTP package download was denied for revision %s: %s.",
                revision.pk,
                getattr(exc, "error_code", "RECOVERY_PACKAGE_UNAVAILABLE"),
            )
            raise Http404 from exc

        with transaction.atomic():
            locked_job = job.__class__.objects.select_for_update().get(pk=job.pk)
            job_data = dict(locked_job.data or {})
            package_data = dict(job_data.get("ftp_recovery_package") or {})
            downloads = list(package_data.get("downloads") or [])[-99:]
            downloads.append(
                {
                    "user_id": request.user.pk,
                    "username": request.user.get_username(),
                    "issued_at": timezone.now().isoformat(),
                }
            )
            package_data["downloads"] = downloads
            package_data["download_count"] = int(package_data.get("download_count") or 0) + 1
            job_data["ftp_recovery_package"] = package_data
            locked_job.data = job_data
            locked_job.save(update_fields=("data",))

        logging.getLogger("netbox_config_backup.views.ConfigRevisionFtpRecoveryDownloadView").info(
            "User %s downloaded verified FTP package for revision %s from replica %s.",
            request.user.get_username(),
            revision.pk,
            replica.pk,
        )
        response = FileResponse(
            package_handle,
            as_attachment=True,
            filename=result["filename"],
            content_type="application/zip",
        )
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response


@register_model_view(ConfigRevision, "set_protection", path="set-protection")
class ConfigRevisionProtectionView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "netbox_config_backup.change_configrevision"
    raise_exception = True

    def post(self, request, pk):
        desired = request.POST.get("protected", "")
        if desired not in {"true", "false"}:
            return HttpResponseBadRequest("Invalid protection state.")
        with transaction.atomic():
            revision = get_object_or_404(
                ConfigRevision.objects.restrict(request.user, "change")
                .select_for_update()
                .select_related("target__device"),
                pk=pk,
            )
            new_value = desired == "true"
            if revision.protected != new_value:
                if hasattr(revision, "snapshot"):
                    revision.snapshot()
                revision._changelog_message = (
                    "Protected from retention cleanup."
                    if new_value
                    else "Removed retention cleanup protection."
                )
                revision.protected = new_value
                revision.save(update_fields=("protected", "last_updated"))
        messages.success(
            request,
            "Revision is protected from cleanup."
            if new_value
            else "Revision protection was removed.",
        )
        return redirect(revision.get_absolute_url())


@register_model_view(ConfigRevision, "content", path="content")
class ConfigRevisionContentView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_configartifact",
    )
    raise_exception = True
    template_name = "netbox_config_backup/configrevision_content.html"

    def get(self, request, pk):
        revision = get_object_or_404(
            ConfigRevision.objects.restrict(request.user, "view")
            .select_related("target__device")
            .prefetch_related("artifacts"),
            pk=pk,
        )
        if (
            not ConfigArtifact.objects.restrict(request.user, "view")
            .filter(
                revision=revision,
                is_primary=True,
            )
            .exists()
        ):
            raise Http404
        display_error = ""
        content = None
        try:
            content = load_revision_content(revision, allow_truncate=True)
        except RevisionDisplayError as exc:
            display_error = str(exc)
        downloadable_artifacts = list(
            ConfigArtifact.objects.restrict(request.user, "view")
            .filter(revision=revision, local_available=True)
            .filter(Q(is_primary=True) | Q(artifact_type="native_backup"))
            .order_by("is_primary", "artifact_type")
        )
        return render(
            request,
            self.template_name,
            {
                "object": revision,
                "content": content,
                "display_error": display_error,
                "downloadable_artifacts": downloadable_artifacts,
            },
        )


class ConfigRevisionArtifactDownloadView(
    PermissionRequiredMixin,
    LoginRequiredMixin,
    View,
):
    permission_required = (
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_configartifact",
    )
    raise_exception = True

    def get(self, request, pk, artifact_pk):
        revision = get_object_or_404(
            ConfigRevision.objects.restrict(request.user, "view"),
            pk=pk,
        )
        artifact = get_object_or_404(
            ConfigArtifact.objects.restrict(request.user, "view"),
            pk=artifact_pk,
            revision=revision,
            local_available=True,
        )
        if not (artifact.is_primary or artifact.artifact_type == "native_backup"):
            raise Http404
        try:
            content = load_artifact_content(artifact)
        except RevisionDisplayError as exc:
            raise Http404(str(exc)) from exc

        filename = readable_artifact_filename(revision, artifact)
        content_type = (
            "application/zip" if artifact.format.endswith("_zip") else "application/octet-stream"
        )
        response = FileResponse(
            io.BytesIO(content),
            as_attachment=True,
            filename=filename,
            content_type=content_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response


@register_model_view(ConfigRevision, "diff", path="diff")
class ConfigRevisionDiffView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = (
        "netbox_config_backup.view_configrevision",
        "netbox_config_backup.view_configartifact",
    )
    raise_exception = True
    template_name = "netbox_config_backup/configrevision_diff.html"

    def get(self, request, pk):
        visible_primary_revision_ids = (
            ConfigArtifact.objects.restrict(request.user, "view")
            .filter(is_primary=True)
            .values("revision_id")
        )
        visible_revisions = ConfigRevision.objects.restrict(request.user, "view").filter(
            pk__in=visible_primary_revision_ids
        )
        revision = get_object_or_404(
            visible_revisions.select_related(
                "target__device", "previous_revision"
            ).prefetch_related("artifacts"),
            pk=pk,
        )
        candidates = list(
            visible_revisions.filter(target=revision.target)
            .exclude(pk=revision.pk)
            .order_by("-created")[:100]
        )
        compare_id = request.GET.get("compare", "").strip()
        if compare_id:
            if not compare_id.isdecimal():
                raise Http404
            base_revision = get_object_or_404(
                visible_revisions.filter(target=revision.target).prefetch_related("artifacts"),
                pk=int(compare_id),
            )
        else:
            base_revision = (
                visible_revisions.filter(pk=revision.previous_revision_id)
                .prefetch_related("artifacts")
                .first()
                if revision.previous_revision_id
                else None
            )

        display_error = ""
        display_diff = None
        if base_revision is not None:
            try:
                diff_input_max_bytes = settings.PLUGINS_CONFIG["netbox_config_backup"][
                    "diff_input_max_bytes"
                ]
                before = load_revision_content(
                    base_revision,
                    max_bytes=diff_input_max_bytes,
                    normalize_for_comparison=True,
                )
                after = load_revision_content(
                    revision,
                    max_bytes=diff_input_max_bytes,
                    normalize_for_comparison=True,
                )
                display_diff = build_display_diff(
                    before,
                    after,
                    before_label=str(base_revision.revision_uuid),
                    after_label=str(revision.revision_uuid),
                    max_lines=settings.PLUGINS_CONFIG["netbox_config_backup"]["diff_max_lines"],
                )
            except RevisionDisplayError as exc:
                display_error = str(exc)

        return render(
            request,
            self.template_name,
            {
                "object": revision,
                "base_revision": base_revision,
                "comparison_candidates": candidates,
                "display_diff": display_diff,
                "display_error": display_error,
            },
        )
