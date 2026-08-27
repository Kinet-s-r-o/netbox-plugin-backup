from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from netbox_config_backup.choices import REPLICATED_DESTINATION_PROTOCOLS


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def calculate_next_ftp_audit(
    destination,
    *,
    after: datetime,
    now: datetime | None = None,
    timezone_name: str = "UTC",
) -> datetime:
    """Return the next configured daily or weekly audit strictly after now."""

    reference = now or after
    if after.tzinfo is None or reference.tzinfo is None:
        raise ValueError("FTP audit scheduling requires timezone-aware datetimes.")

    zone = _zone(timezone_name)
    local_reference = reference.astimezone(zone)
    candidate_date: date = local_reference.date()

    if destination.integrity_audit_frequency == "daily":
        candidate = datetime.combine(
            candidate_date,
            destination.integrity_audit_time,
            tzinfo=zone,
        )
        if candidate <= local_reference:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if destination.integrity_audit_frequency != "weekly":
        raise ValueError("Unsupported FTP audit frequency.")

    days_ahead = (destination.integrity_audit_weekday - candidate_date.weekday()) % 7
    candidate = datetime.combine(
        candidate_date + timedelta(days=days_ahead),
        destination.integrity_audit_time,
        tzinfo=zone,
    )
    if candidate <= local_reference:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC)


def calculate_destination_next_ftp_audit(
    destination,
    *,
    now: datetime,
    timezone_name: str = "UTC",
) -> datetime | None:
    if (
        not destination.enabled
        or not destination.integrity_audit_enabled
        or destination.protocol not in REPLICATED_DESTINATION_PROTOCOLS
    ):
        return None
    return calculate_next_ftp_audit(
        destination,
        after=now,
        now=now,
        timezone_name=timezone_name,
    )
