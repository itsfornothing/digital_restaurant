"""
menus/signals.py

Post-save and post-delete signals for the MenuItem model.

Cache Invalidation (Task 20.2 — Requirements 19.1, 19.2):
    Whenever a MenuItem is saved or deleted, the Redis cache key
    ``menu:branch:{branch_id}`` (TTL 30 s) must be invalidated so that
    the next CustomerMenuView GET returns fresh data.

    This complements the explicit invalidation already performed in
    MenuItemViewSet.perform_create / partial_update / archive
    (apps/menus/views.py → _invalidate_branch_menu_cache) — the signal
    provides a safety net for any save path that does not go through the
    ViewSet (e.g. management commands, admin, tests, migrations).

    Cache key format: ``menu:branch:{branch_id}``
    This matches the key written by CustomerMenuView.get() in
    apps/qr/customer_views.py (Task 20.2).

Requirements: 19.1, 19.2
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.menus.models import MenuItem

logger = logging.getLogger(__name__)

try:
    from apps.webhooks.dispatch import dispatch_webhook_event as _dispatch_webhook
except ImportError:
    _dispatch_webhook = None  # type: ignore[assignment]


def _invalidate_menu_cache(branch_id: str) -> None:
    """
    Delete the Redis cache key ``menu:branch:{branch_id}``.

    Swallows all exceptions so that a cache backend failure never
    prevents a MenuItem save from completing.
    """
    from django.core.cache import cache

    cache_key = f"menu:branch:{branch_id}"
    try:
        cache.delete(cache_key)
        logger.debug("Invalidated menu cache: %s", cache_key)
    except Exception as exc:
        logger.warning(
            "Failed to invalidate menu cache key %s: %s",
            cache_key,
            exc,
        )


@receiver(post_save, sender=MenuItem)
def on_menu_item_saved(sender, instance: MenuItem, **kwargs) -> None:
    """
    Invalidate the branch menu cache whenever a MenuItem is saved.

    Covers: creation, price update, status change, archive, and any other
    field change that passes through MenuItem.save().

    Requirements: 9.2, 19.1, 19.2
    """
    if instance.branch_id:
        _invalidate_menu_cache(str(instance.branch_id))
        if _dispatch_webhook is not None:
            _dispatch_webhook(
                branch_id=str(instance.branch_id),
                event_type="menu.updated",
                payload={
                    "menu_item_id": str(instance.id),
                    "name": instance.name,
                    "status": instance.status,
                    "price": str(instance.price),
                    "is_archived": instance.is_archived,
                    "branch_id": str(instance.branch_id),
                },
            )


@receiver(post_delete, sender=MenuItem)
def on_menu_item_deleted(sender, instance: MenuItem, **kwargs) -> None:
    """
    Invalidate the branch menu cache whenever a MenuItem is deleted.

    In normal operation MenuItem records are soft-archived (is_archived=True)
    rather than hard-deleted.  This signal handles the hard-delete case.

    Requirements: 9.3, 19.2
    """
    if instance.branch_id:
        _invalidate_menu_cache(str(instance.branch_id))
