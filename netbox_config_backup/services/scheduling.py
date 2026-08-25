from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _deterministic_jitter_minutes(
    *, target_key: object, policy_key: object, local_date: date, maximum: int
) -> int:
    if maximum <= 0:
        return 0
    seed = f"{target_key}:{policy_key}:{local_date.isoformat()}".encode()
    return int.from_bytes(sha256(seed).digest()[:8], "big") % (maximum + 1)


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def calculate_next_run(
    policy,
    *,
    after: datetime,
    target_key: object,
    timezone_name: str = "UTC",
    now: datetime | None = None,
) -> datetime:
    """Return the next regular run strictly after ``now`` (or ``after``)."""
    reference = now or after
    if after.tzinfo is None or reference.tzinfo is None:
        raise ValueError("Scheduling requires timezone-aware datetimes.")

    if policy.schedule_type == "interval":
        if not policy.interval_minutes or policy.interval_minutes <= 0:
            raise ValueError("Interval policies require a positive interval_minutes value.")
        interval = timedelta(minutes=policy.interval_minutes)
        candidate = after + interval
        if candidate <= reference:
            missed = ((reference - candidate) // interval) + 1
            candidate += missed * interval
        return candidate.astimezone(UTC)

    if policy.schedule_type != "daily" or policy.time_of_day is None:
        raise ValueError("Daily policies require time_of_day.")

    zone = _zone(timezone_name)
    local_reference = reference.astimezone(zone)
    candidate_date = local_reference.date()
    candidate = _daily_candidate(policy, candidate_date, target_key, zone)
    if candidate <= local_reference:
        candidate_date += timedelta(days=1)
        candidate = _daily_candidate(policy, candidate_date, target_key, zone)
    return candidate.astimezone(UTC)


def _daily_candidate(policy, candidate_date: date, target_key: object, zone: ZoneInfo) -> datetime:
    local_time: time = policy.time_of_day
    candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
    jitter = _deterministic_jitter_minutes(
        target_key=target_key,
        policy_key=getattr(policy, "pk", getattr(policy, "name", "policy")),
        local_date=candidate_date,
        maximum=policy.jitter_minutes,
    )
    return candidate + timedelta(minutes=jitter)


def calculate_failure_next_run(
    policy,
    *,
    failed_at: datetime,
    consecutive_failures: int,
    target_key: object,
    timezone_name: str = "UTC",
) -> datetime | None:
    """Return a retry deadline, or the next regular run if retries are exhausted."""
    if not policy or not policy.enabled:
        return None
    backoff = policy.retry_backoff_minutes
    if consecutive_failures <= policy.max_retries and backoff:
        index = min(consecutive_failures - 1, len(backoff) - 1)
        return failed_at + timedelta(minutes=backoff[index])
    return calculate_next_run(
        policy,
        after=failed_at,
        target_key=target_key,
        timezone_name=timezone_name,
    )


def is_retry_scheduled(policy, consecutive_failures: int) -> bool:
    return bool(
        policy
        and policy.enabled
        and policy.retry_backoff_minutes
        and 0 < consecutive_failures <= policy.max_retries
    )


def target_timezone_name(target) -> str:
    if target.policy_override and target.policy_override.timezone_mode == "site":
        site_timezone = getattr(target.device.site, "time_zone", None)
        if site_timezone:
            return str(site_timezone)
    from django.conf import settings

    return settings.TIME_ZONE


def calculate_target_next_run(target, *, now: datetime) -> datetime | None:
    policy = target.policy_override
    if not target.enabled or not policy or not policy.enabled:
        return None
    return calculate_next_run(
        policy,
        after=now,
        target_key=target.pk or target.device_id,
        timezone_name=target_timezone_name(target),
    )


def apply_target_schedule(target, *, now: datetime) -> None:
    from netbox_config_backup.choices import TargetStatusChoices

    target.next_run_at = calculate_target_next_run(target, now=now)
    if not target.enabled:
        target.status = TargetStatusChoices.DISABLED
    elif target.status == TargetStatusChoices.DISABLED:
        if target.consecutive_failures:
            target.status = TargetStatusChoices.FAILED
        else:
            target.status = (
                TargetStatusChoices.HEALTHY if target.last_success_at else TargetStatusChoices.NEVER
            )
    target.save(update_fields=("next_run_at", "status", "last_updated"))
