import django_filters
from dcim.models import Device, Site
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from netbox.filtersets import NetBoxModelFilterSet
from utilities.filtersets import register_filterset

from .choices import RunSourceChoices, RunStatusChoices, TargetStatusChoices
from .models import BackupPolicy, BackupRun, BackupTarget, ConfigRevision
from .services.health import FAILURE_RUN_STATUSES, stuck_run_query
from .services.reporting_period import REPORTING_PERIOD_CHOICES, resolve_reporting_period


@register_filterset
class BackupTargetFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")
    status = django_filters.MultipleChoiceFilter(
        choices=TargetStatusChoices.choices,
        distinct=False,
    )
    device_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Device.objects.all(),
        distinct=False,
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="device__site_id",
        queryset=Site.objects.all(),
        distinct=False,
    )
    policy_override_id = django_filters.ModelMultipleChoiceFilter(
        queryset=BackupPolicy.objects.all(),
        distinct=False,
    )

    class Meta:
        model = BackupTarget
        fields = (
            "id",
            "enabled",
            "status",
            "device_id",
            "site_id",
            "policy_override_id",
            "driver_override",
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(device__name__icontains=value)
            | Q(driver_override__icontains=value)
            | Q(policy_override__name__icontains=value)
        )


@register_filterset
class BackupRunFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")
    status = django_filters.MultipleChoiceFilter(
        choices=RunStatusChoices.choices,
        distinct=False,
    )
    source = django_filters.MultipleChoiceFilter(
        choices=RunSourceChoices.choices,
        distinct=False,
    )
    target_id = django_filters.ModelMultipleChoiceFilter(
        queryset=BackupTarget.objects.all(),
        distinct=False,
    )
    device_id = django_filters.ModelMultipleChoiceFilter(
        field_name="target__device_id",
        queryset=Device.objects.all(),
        distinct=False,
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="target__device__site_id",
        queryset=Site.objects.all(),
        distinct=False,
    )
    queued_at = django_filters.DateTimeFromToRangeFilter()
    period = django_filters.ChoiceFilter(
        choices=REPORTING_PERIOD_CHOICES,
        method="filter_reporting_period",
        label="Period",
    )
    date_from = django_filters.DateFilter(
        field_name="queued_at",
        lookup_expr="date__gte",
        label="From date",
    )
    date_to = django_filters.DateFilter(
        field_name="queued_at",
        lookup_expr="date__lte",
        label="To date",
    )
    error_code = django_filters.CharFilter(lookup_expr="icontains")
    failed = django_filters.BooleanFilter(method="filter_failed", label="Failure")
    stuck = django_filters.BooleanFilter(method="filter_stuck", label="Stuck")

    class Meta:
        model = BackupRun
        fields = (
            "id",
            "target_id",
            "device_id",
            "site_id",
            "status",
            "source",
            "queued_at",
            "period",
            "date_from",
            "date_to",
            "error_code",
            "failed",
            "stuck",
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(target__device__name__icontains=value)
            | Q(error_code__icontains=value)
            | Q(error_message__icontains=value)
        )

    def filter_failed(self, queryset, name, value):
        if value is None:
            return queryset
        query = Q(status__in=FAILURE_RUN_STATUSES)
        return queryset.filter(query) if value else queryset.exclude(query)

    def filter_stuck(self, queryset, name, value):
        if value is None:
            return queryset
        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        query = stuck_run_query(
            now=timezone.now(),
            timeout_minutes=plugin_settings["stale_run_minutes"],
        )
        return queryset.filter(query) if value else queryset.exclude(query)

    def filter_reporting_period(self, queryset, name, value):
        if value in {"all", "custom"}:
            return queryset
        return resolve_reporting_period({"period": value}).filter(queryset, "queued_at")


@register_filterset
class ConfigRevisionFilterSet(NetBoxModelFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")
    target_id = django_filters.ModelMultipleChoiceFilter(
        queryset=BackupTarget.objects.all(),
        distinct=False,
    )
    device_id = django_filters.ModelMultipleChoiceFilter(
        field_name="target__device_id",
        queryset=Device.objects.all(),
        distinct=False,
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="target__device__site_id",
        queryset=Site.objects.all(),
        distinct=False,
    )
    period = django_filters.ChoiceFilter(
        choices=REPORTING_PERIOD_CHOICES,
        method="filter_reporting_period",
        label="Period",
    )
    date_from = django_filters.DateFilter(
        field_name="created",
        lookup_expr="date__gte",
        label="From date",
    )
    date_to = django_filters.DateFilter(
        field_name="created",
        lookup_expr="date__lte",
        label="To date",
    )

    class Meta:
        model = ConfigRevision
        fields = (
            "id",
            "target_id",
            "device_id",
            "site_id",
            "driver_id",
            "content_changed",
            "protected",
            "period",
            "date_from",
            "date_to",
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(target__device__name__icontains=value)
            | Q(driver_id__icontains=value)
            | Q(label__icontains=value)
        )

    def filter_reporting_period(self, queryset, name, value):
        if value in {"all", "custom"}:
            return queryset
        return resolve_reporting_period({"period": value}).filter(queryset, "created")
