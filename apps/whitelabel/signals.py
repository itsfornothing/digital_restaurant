"""
whitelabel/signals.py

Post-save signal for TenantConfig.

Cache Invalidation (Task 20.2 — Requirements 19.1, 19.2):
    Whenever the TenantConfig record is saved (by any code path — ViewSet,
    admin, management command, or test), the Redis cache key
    ``tenant_config:{schema_name}`` (TTL 300 s) is invalidated so that:
      - TenantConfigViewSet.retrieve() reads fresh data on the next GET.
      - The whitelabel_context processor reloads from DB on the next request.

    The same key is also deleted explicitly inside TenantConfigViewSet.partial_update()
    (apps/whitelabel/views.py) — this signal is the safety-net for other save paths.

    Cache key format: ``tenant_config:{schema_name}``
    Matches the key returned by _tenant_cache_key() in apps/whitelabel/views.py
    and used by the whitelabel_context processor.

Requirements: 7.2, 19.1, 19.2
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.whitelabel.models import TenantConfig

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TenantConfig)
def on_tenant_config_saved(sender, instance: TenantConfig, **kwargs) -> None:
    """
    Invalidate the tenant config cache whenever TenantConfig is saved.

    Uses the same cache key as TenantConfigViewSet._tenant_cache_key()
    (``tenant_config:{schema_name}``) so both the view cache and the
    whitelabel_context processor cache are cleared in one operation.

    Requirements: 7.2, 19.1, 19.2
    """
    from django.core.cache import cache

    try:
        from django.db import connection
        schema = getattr(connection, "schema_name", "public")
    except Exception:
        schema = "public"

    cache_key = f"tenant_config:{schema}"
    try:
        cache.delete(cache_key)
        logger.debug("Invalidated tenant config cache: %s", cache_key)
    except Exception as exc:
        logger.warning(
            "Failed to invalidate tenant config cache key %s: %s",
            cache_key,
            exc,
        )
