from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

REPORTING_PERIOD_CHOICES = (
    ("24h", "Last 24 hours"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
    ("all", "All time"),
    ("custom", "Custom dates"),
)

_PRESET_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
_LABELS = dict(REPORTING_PERIOD_CHOICES)


@dataclass(frozen=True, slots=True)
class ReportingPeriod:
    key: str
    label: str
    start: datetime | None
    end: datetime | None
    date_from: date | None = None
    date_to: date | None = None
    error: str = ""

    def filter(self, queryset, field_name: str):
        filters = {}
        if self.start is not None:
            filters[f"{field_name}__gte"] = self.start
        if self.end is not None:
            filters[f"{field_name}__lt"] = self.end
        return queryset.filter(**filters) if filters else queryset

    @property
    def query_string(self) -> str:
        values: dict[str, str] = {"period": self.key}
        if self.key == "custom" and self.date_from and self.date_to:
            values["date_from"] = self.date_from.isoformat()
            values["date_to"] = self.date_to.isoformat()
        return urlencode(values)


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def resolve_reporting_period(params, *, now: datetime | None = None) -> ReportingPeriod:
    now = now or timezone.now()
    key = str(params.get("period") or "30d")
    if key not in _LABELS:
        key = "30d"

    if key in _PRESET_DELTAS:
        return ReportingPeriod(
            key=key,
            label=_LABELS[key],
            start=now - _PRESET_DELTAS[key],
            end=now + timedelta(microseconds=1),
        )
    if key == "all":
        return ReportingPeriod(key=key, label=_LABELS[key], start=None, end=None)

    date_from = _parse_date(params.get("date_from"))
    date_to = _parse_date(params.get("date_to"))
    if date_from is None or date_to is None or date_from > date_to:
        fallback = resolve_reporting_period({"period": "30d"}, now=now)
        return ReportingPeriod(
            key=fallback.key,
            label=fallback.label,
            start=fallback.start,
            end=fallback.end,
            error="Enter a valid custom start and end date.",
        )

    current_timezone = (
        timezone.get_current_timezone() if settings.configured else (now.tzinfo or UTC)
    )
    start = timezone.make_aware(datetime.combine(date_from, time.min), current_timezone)
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min),
        current_timezone,
    )
    return ReportingPeriod(
        key="custom",
        label=f"{date_from.isoformat()} – {date_to.isoformat()}",
        start=start,
        end=end,
        date_from=date_from,
        date_to=date_to,
    )
