"""What needs an operator's attention, for the failure marker in NetBox's side menu.

The side menu is rendered on every NetBox page, so this module must be cheap and must
never raise: results are cached briefly per user and any error yields "nothing to show".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CACHE_SECONDS = 30
_CACHE_KEY = "netbox_config_backup:attention:{user_id}"
_REQUEST_ATTRIBUTE = "_netbox_config_backup_attention"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Attention:
    failed_targets: int = 0
    storages: int = 0
    stuck_runs: int = 0

    @property
    def total(self) -> int:
        return self.failed_targets + self.storages + self.stuck_runs


def compute_attention(user: Any) -> Attention:
    """Count problems the user may see, using the same definitions as the Overview page."""

    from django.conf import settings
    from django.db.models import Q
    from django.utils import timezone

    from netbox_config_backup.choices import REPLICATED_DESTINATION_PROTOCOLS, TargetStatusChoices
    from netbox_config_backup.models import BackupDestination, BackupRun, BackupTarget
    from netbox_config_backup.services.health import stuck_run_queryset

    targets = BackupTarget.objects.restrict(user, "view")
    runs = BackupRun.objects.restrict(user, "view").filter(target_id__in=targets.values("pk"))
    destinations = BackupDestination.objects.restrict(user, "view").filter(
        protocol__in=REPLICATED_DESTINATION_PROTOCOLS,
        enabled=True,
    )
    plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
    return Attention(
        failed_targets=targets.filter(status=TargetStatusChoices.FAILED).count(),
        storages=destinations.filter(
            Q(last_error_code__gt="") | Q(last_integrity_audit_status__in=("failed", "problems"))
        ).count(),
        stuck_runs=stuck_run_queryset(
            runs,
            now=timezone.now(),
            timeout_minutes=plugin_settings["stale_run_minutes"],
        ).count(),
    )


def attention_for_user(
    user: Any,
    *,
    compute: Callable[[Any], Attention] = compute_attention,
    cache: Any = None,
) -> Attention:
    """Cached, failure-tolerant attention counts for one user."""

    if not getattr(user, "is_authenticated", False):
        return Attention()

    # The menu renders on every page, so nothing below may raise. A cache outage only
    # costs a fresh query; a failed query only hides the marker.
    key = _CACHE_KEY.format(user_id=user.pk)
    cached = None
    try:
        if cache is None:
            from django.core.cache import cache as default_cache

            cache = default_cache
        cached = cache.get(key)
    except Exception:
        logger.warning("Config Backup attention cache is unavailable.", exc_info=True)
        cache = None
    if cached is not None:
        return Attention(*cached)

    try:
        result = compute(user)
    except Exception:
        logger.warning("Could not determine Config Backup attention state.", exc_info=True)
        return Attention()
    if cache is not None:
        try:
            cache.set(
                key, (result.failed_targets, result.storages, result.stuck_runs), CACHE_SECONDS
            )
        except Exception:
            logger.warning("Config Backup attention cache is unavailable.", exc_info=True)
    return result


def current_attention() -> Attention:
    """Attention for the user of the request being rendered, computed once per request."""

    from netbox.context import current_request

    request = current_request.get()
    if request is None:
        return Attention()
    existing = getattr(request, _REQUEST_ATTRIBUTE, None)
    if existing is None:
        existing = attention_for_user(getattr(request, "user", None))
        setattr(request, _REQUEST_ATTRIBUTE, existing)
    return existing
