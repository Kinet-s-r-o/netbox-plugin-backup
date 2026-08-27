from __future__ import annotations

from dcim.models import Device, Site
from django import forms
from netbox.forms import NetBoxModelFilterSetForm
from utilities.forms import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.fields import DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DateTimePicker

from .choices import RunSourceChoices, RunStatusChoices, TargetStatusChoices
from .models import (
    BackupPolicy,
    BackupRun,
    BackupTarget,
    ConfigRevision,
    RemoteRetentionPolicy,
    RetentionPolicy,
)
from .services.reporting_period import REPORTING_PERIOD_CHOICES

REPORTING_PERIOD_FORM_CHOICES = (("", "Any time"), *REPORTING_PERIOD_CHOICES)


class BackupTargetFilterForm(NetBoxModelFilterSetForm):
    model = BackupTarget
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet(
            "status",
            "enabled",
            "device_id",
            "site_id",
            "policy_override_id",
            "retention_override_id",
            "remote_retention_policy_id",
            "driver_override",
            name="Health and device",
        ),
    )
    status = forms.MultipleChoiceField(
        choices=TargetStatusChoices.choices,
        required=False,
    )
    enabled = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    policy_override_id = DynamicModelMultipleChoiceField(
        queryset=BackupPolicy.objects.all(),
        required=False,
        label="Backup policy",
    )
    retention_override_id = DynamicModelMultipleChoiceField(
        queryset=RetentionPolicy.objects.all(),
        required=False,
        label="Local retention profile",
    )
    remote_retention_policy_id = DynamicModelMultipleChoiceField(
        queryset=RemoteRetentionPolicy.objects.all(),
        required=False,
        label="Remote retention profile",
    )
    driver_override = forms.CharField(required=False, label="Driver override")


class BackupRunFilterForm(NetBoxModelFilterSetForm):
    model = BackupRun
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("status", "source", "failed", "stuck", "error_code", name="Result"),
        FieldSet("target_id", "device_id", "site_id", name="Target"),
        FieldSet("period", "date_from", "date_to", name="Period"),
        FieldSet("queued_at_after", "queued_at_before", name="Exact queued time"),
    )
    status = forms.MultipleChoiceField(
        choices=RunStatusChoices.choices,
        required=False,
    )
    source = forms.MultipleChoiceField(
        choices=RunSourceChoices.choices,
        required=False,
    )
    failed = forms.NullBooleanField(
        required=False,
        label="Failure",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    stuck = forms.NullBooleanField(
        required=False,
        label="Stuck",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    error_code = forms.CharField(required=False, label="Error code contains")
    period = forms.ChoiceField(
        choices=REPORTING_PERIOD_FORM_CHOICES,
        required=False,
        label="Period",
    )
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="To date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_id = DynamicModelMultipleChoiceField(
        queryset=BackupTarget.objects.all(),
        required=False,
        label="Backup target",
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    queued_at_after = forms.DateTimeField(
        required=False,
        label="Queued after",
        widget=DateTimePicker(),
    )
    queued_at_before = forms.DateTimeField(
        required=False,
        label="Queued before",
        widget=DateTimePicker(),
    )


class ConfigRevisionFilterForm(NetBoxModelFilterSetForm):
    model = ConfigRevision
    fieldsets = (
        FieldSet("q", "filter_id"),
        FieldSet("period", "date_from", "date_to", name="Period"),
        FieldSet("target_id", "device_id", "site_id", name="Target"),
        FieldSet("driver_id", "content_changed", "protected", name="Revision"),
    )
    period = forms.ChoiceField(
        choices=REPORTING_PERIOD_FORM_CHOICES,
        required=False,
        label="Period",
    )
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="To date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_id = DynamicModelMultipleChoiceField(
        queryset=BackupTarget.objects.all(),
        required=False,
        label="Backup target",
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    driver_id = forms.CharField(required=False, label="Driver")
    content_changed = forms.NullBooleanField(
        required=False,
        label="Configuration changed",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    protected = forms.NullBooleanField(
        required=False,
        label="Protected",
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
