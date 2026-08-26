from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})
UNCHANGED_RUN_STATUSES = frozenset({"success_unchanged"})
CHANGED_RUN_STATUSES = frozenset({"success_changed"})
FAILED_RUN_STATUSES = frozenset({"partial", "failed", "errored", "skipped"})
KNOWN_RUN_STATUSES = (
    ACTIVE_RUN_STATUSES | UNCHANGED_RUN_STATUSES | CHANGED_RUN_STATUSES | FAILED_RUN_STATUSES
)


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    keep_all_days: int
    daily_days: int
    weekly_weeks: int
    monthly_months: int
    minimum_changed_revisions: int
    unchanged_run_days: int
    changed_run_days: int
    failed_run_days: int
    max_runs_per_target: int
    max_revisions_per_target: int | None = None


@dataclass(frozen=True, slots=True)
class RevisionCandidate:
    object_id: int
    created: datetime
    protected: bool
    content_changed: bool


@dataclass(frozen=True, slots=True)
class RunCandidate:
    object_id: int
    timestamp: datetime
    status: str


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    object_id: int
    timestamp: datetime
    keep: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    generated_at: datetime
    revision_decisions: tuple[RetentionDecision, ...]
    run_decisions: tuple[RetentionDecision, ...]

    @property
    def revisions_to_keep(self) -> int:
        return sum(decision.keep for decision in self.revision_decisions)

    @property
    def revisions_to_delete(self) -> int:
        return len(self.revision_decisions) - self.revisions_to_keep

    @property
    def runs_to_keep(self) -> int:
        return sum(decision.keep for decision in self.run_decisions)

    @property
    def runs_to_delete(self) -> int:
        return len(self.run_decisions) - self.runs_to_keep


def settings_from_policy(policy) -> RetentionSettings:
    return RetentionSettings(
        keep_all_days=policy.keep_all_days,
        daily_days=policy.daily_days,
        weekly_weeks=policy.weekly_weeks,
        monthly_months=policy.monthly_months,
        minimum_changed_revisions=policy.minimum_changed_revisions,
        unchanged_run_days=policy.unchanged_run_days,
        changed_run_days=policy.changed_run_days,
        failed_run_days=policy.failed_run_days,
        max_runs_per_target=policy.max_runs_per_target,
    )


def settings_from_remote_policy(policy) -> RetentionSettings:
    """Translate an FTP retention profile into the shared revision planner.

    Backup-run retention is deliberately disabled here: FTP retention owns only
    immutable revision copies and must never remove NetBox run history.
    """

    return RetentionSettings(
        keep_all_days=policy.keep_all_days,
        daily_days=policy.daily_days,
        weekly_weeks=policy.weekly_weeks,
        monthly_months=policy.monthly_months,
        minimum_changed_revisions=policy.minimum_changed_revisions,
        unchanged_run_days=0,
        changed_run_days=0,
        failed_run_days=0,
        max_runs_per_target=1,
        max_revisions_per_target=policy.max_copies_per_target,
    )


def effective_retention_policy(target):
    if target.retention_override_id:
        return target.retention_override
    if target.policy_override_id:
        return target.policy_override.retention_policy
    return None


def has_recorded_remote_copy(replicas: Iterable[object]) -> bool:
    """Return whether revision metadata owns a non-tombstoned remote pointer.

    ``remote_available`` is deliberately not the only signal. A final failed
    repair can clear that flag while retaining ``remote_path`` to an older
    complete copy or an interrupted upload which FTP retention must still be
    able to reconcile exactly.
    """

    return any(
        getattr(replica, "remote_deleted_at", None) is None
        and (
            bool(getattr(replica, "remote_available", False))
            or bool(getattr(replica, "remote_path", ""))
        )
        for replica in replicas
    )


def build_retention_plan(
    settings: RetentionSettings,
    *,
    revisions: Iterable[RevisionCandidate],
    runs: Iterable[RunCandidate],
    now: datetime,
) -> RetentionPlan:
    _require_aware(now)
    revision_decisions = _plan_revisions(settings, revisions, now=now)
    run_decisions = _plan_runs(settings, runs, now=now)
    return RetentionPlan(
        generated_at=now,
        revision_decisions=revision_decisions,
        run_decisions=run_decisions,
    )


def _plan_revisions(
    settings: RetentionSettings,
    revisions: Iterable[RevisionCandidate],
    *,
    now: datetime,
) -> tuple[RetentionDecision, ...]:
    ordered = sorted(revisions, key=lambda item: (item.created, item.object_id), reverse=True)
    for revision in ordered:
        _require_aware(revision.created)

    reasons: dict[int, list[str]] = {revision.object_id: [] for revision in ordered}
    if ordered:
        reasons[ordered[0].object_id].append("Latest revision")

    for revision in ordered:
        if revision.protected:
            reasons[revision.object_id].append("Protected revision")

    changed = [revision for revision in ordered if revision.content_changed]
    for revision in changed[: settings.minimum_changed_revisions]:
        reasons[revision.object_id].append("Minimum changed revisions")

    keep_all_cutoff = now - timedelta(days=settings.keep_all_days)
    if settings.keep_all_days:
        for revision in ordered:
            if revision.created >= keep_all_cutoff:
                reasons[revision.object_id].append("Recent revision")

    _mark_latest_per_bucket(
        ordered,
        reasons,
        reason="Daily sample",
        eligible=lambda item: _within_days(item.created, now, settings.daily_days),
        bucket=lambda item: item.created.astimezone(UTC).date(),
    )
    _mark_latest_per_bucket(
        ordered,
        reasons,
        reason="Weekly sample",
        eligible=lambda item: _within_weeks(item.created, now, settings.weekly_weeks),
        bucket=lambda item: item.created.astimezone(UTC).date().isocalendar()[:2],
    )
    _mark_latest_per_bucket(
        ordered,
        reasons,
        reason="Monthly sample",
        eligible=lambda item: _within_months(item.created, now, settings.monthly_months),
        bucket=lambda item: (
            item.created.astimezone(UTC).year,
            item.created.astimezone(UTC).month,
        ),
    )

    decisions = tuple(
        RetentionDecision(
            object_id=revision.object_id,
            timestamp=revision.created,
            keep=bool(reasons[revision.object_id]),
            reasons=tuple(reasons[revision.object_id]) or ("Outside retention windows",),
        )
        for revision in ordered
    )
    if not settings.max_revisions_per_target:
        return decisions

    # A hard copy cap is a final safety valve for remote storage. The newest
    # and explicitly protected revisions always win, even if protected records
    # make the configured cap impossible to meet.
    mandatory = {
        decision.object_id
        for decision in decisions
        if "Latest revision" in decision.reasons or "Protected revision" in decision.reasons
    }
    remaining = max(settings.max_revisions_per_target - len(mandatory), 0)
    capped: list[RetentionDecision] = []
    for decision in decisions:
        if not decision.keep or decision.object_id in mandatory:
            capped.append(decision)
            continue
        if remaining:
            capped.append(decision)
            remaining -= 1
            continue
        capped.append(
            RetentionDecision(
                object_id=decision.object_id,
                timestamp=decision.timestamp,
                keep=False,
                reasons=("Per-target revision limit exceeded",),
            )
        )
    return tuple(capped)


def _plan_runs(
    settings: RetentionSettings,
    runs: Iterable[RunCandidate],
    *,
    now: datetime,
) -> tuple[RetentionDecision, ...]:
    ordered = sorted(runs, key=lambda item: (item.timestamp, item.object_id), reverse=True)
    decisions = []
    retained_completed_runs = 0
    for run in ordered:
        _require_aware(run.timestamp)
        if run.status in ACTIVE_RUN_STATUSES:
            keep, reason = True, "Active run"
        elif run.status not in KNOWN_RUN_STATUSES:
            keep, reason = True, "Unknown status (kept safely)"
        elif run.status in UNCHANGED_RUN_STATUSES:
            keep = _within_exact_days(run.timestamp, now, settings.unchanged_run_days)
            reason = "Unchanged run retention" if keep else "Unchanged run expired"
        elif run.status in CHANGED_RUN_STATUSES:
            keep = _within_exact_days(run.timestamp, now, settings.changed_run_days)
            reason = "Changed run retention" if keep else "Changed run expired"
        else:
            keep = _within_exact_days(run.timestamp, now, settings.failed_run_days)
            reason = "Failed run retention" if keep else "Failed run expired"

        if run.status in KNOWN_RUN_STATUSES - ACTIVE_RUN_STATUSES and keep:
            if retained_completed_runs >= settings.max_runs_per_target:
                keep = False
                reason = "Per-target run limit exceeded"
            else:
                retained_completed_runs += 1
        decisions.append(
            RetentionDecision(
                object_id=run.object_id,
                timestamp=run.timestamp,
                keep=keep,
                reasons=(reason,),
            )
        )
    return tuple(decisions)


def _mark_latest_per_bucket(ordered, reasons, *, reason, eligible, bucket) -> None:
    seen = set()
    for item in ordered:
        if not eligible(item):
            continue
        key = bucket(item)
        if key in seen:
            continue
        seen.add(key)
        reasons[item.object_id].append(reason)


def _within_exact_days(timestamp: datetime, now: datetime, days: int) -> bool:
    return bool(days) and timestamp >= now - timedelta(days=days)


def _within_days(timestamp: datetime, now: datetime, days: int) -> bool:
    if days <= 0:
        return False
    timestamp_date = timestamp.astimezone(UTC).date()
    current_date = now.astimezone(UTC).date()
    return 0 <= (current_date - timestamp_date).days < days


def _within_weeks(timestamp: datetime, now: datetime, weeks: int) -> bool:
    if weeks <= 0:
        return False
    timestamp_date = timestamp.astimezone(UTC).date()
    current_date = now.astimezone(UTC).date()
    timestamp_week = timestamp_date - timedelta(days=timestamp_date.weekday())
    current_week = current_date - timedelta(days=current_date.weekday())
    return 0 <= (current_week - timestamp_week).days < weeks * 7


def _within_months(timestamp: datetime, now: datetime, months: int) -> bool:
    if months <= 0:
        return False
    timestamp_utc = timestamp.astimezone(UTC)
    now_utc = now.astimezone(UTC)
    age = (now_utc.year - timestamp_utc.year) * 12 + now_utc.month - timestamp_utc.month
    return 0 <= age < months


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Retention planning requires timezone-aware datetimes.")
