"""
inventory/tasks.py — Celery tasks for inventory management.

Tasks:
  deduct_inventory         — Deduct stock for all ingredients in an order
                              (triggered when order transitions to Preparing)
  check_inventory_thresholds — Scan a branch's inventory and fire alerts for
                              low stock, expiry warnings, and out-of-stock items
  send_inventory_alert     — Push a WebSocket alert to the branch channel group
                              (and optionally send email)

Requirements: 11.2, 11.3, 11.4, 11.5, 11.7
"""

from __future__ import annotations

import logging
from datetime import date

from asgiref.sync import async_to_sync
from celery import shared_task
from django.db.models import F

logger = logging.getLogger(__name__)

try:
    from django.core.cache import cache as _cache
except ImportError:
    _cache = None

try:
    from apps.webhooks.dispatch import dispatch_webhook_event as _dispatch_webhook
except ImportError:
    _dispatch_webhook = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Task 12.2 — deduct_inventory
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def deduct_inventory(self, order_id: str):
    """
    Deduct inventory quantities for all items in the order.

    For each OrderItem in the order, looks up the MenuItem's Recipe Ingredient
    records and decrements each InventoryItem.quantity by
    (ingredient.quantity × order_item.quantity).

    Allows negative quantities (records and alerts, does not block)
    per Requirement 11.7.

    Triggers check_inventory_thresholds after all deductions are applied.

    Requirements: 11.2, 11.7
    """
    try:
        from apps.inventory.models import InventoryItem
        from apps.orders.models import Order

        # 1. Fetch the Order with deep prefetch for recipe ingredients
        order = (
            Order.objects.prefetch_related(
                "items__menu_item__recipe__ingredients__inventory_item"
            )
            .get(id=order_id)
        )

        # 2. For each OrderItem, deduct inventory
        for order_item in order.items.all():
            menu_item = order_item.menu_item

            # Try to get the recipe; skip if none exists
            try:
                recipe = menu_item.recipe
            except Exception:
                # No recipe associated — nothing to deduct
                continue

            for ingredient in recipe.ingredients.all():
                inventory_item = ingredient.inventory_item
                deduction = ingredient.quantity * order_item.quantity

                # Atomic F() expression prevents race conditions
                # Negative quantities are allowed per Requirement 11.7
                InventoryItem.objects.filter(pk=inventory_item.pk).update(
                    quantity=F("quantity") - deduction
                )

                logger.info(
                    "Deducted %.4f %s of '%s' (InventoryItem %s) for Order %s",
                    deduction,
                    ingredient.unit,
                    inventory_item.name,
                    inventory_item.pk,
                    order_id,
                )

        # Check for items that hit zero and auto-mark menu items as unavailable
        for order_item in order.items.all():
            menu_item = order_item.menu_item
            try:
                recipe = menu_item.recipe
            except Exception:
                continue
            for ingredient in recipe.ingredients.all():
                inventory_item = ingredient.inventory_item
                # Refresh from DB after F() update
                inventory_item.refresh_from_db()
                if inventory_item.quantity <= 0:
                    from apps.menus.models import MenuItem
                    affected_items = MenuItem.objects.filter(
                        branch_id=order.branch_id,
                        recipe__ingredients__inventory_item=inventory_item,
                        status="available",
                    )
                    for item in affected_items:
                        item.status = "unavailable"
                        item.save(update_fields=["status"])
                        logger.info(
                            "Auto-unavailable: '%s' (MenuItem %s) — '%s' stock depleted",
                            item.name, item.id, inventory_item.name,
                        )
                    # Invalidate menu cache for the branch
                    if _cache is not None:
                        _cache.delete(f"menu:branch:{order.branch_id}")

        # 3. Trigger threshold checks for the branch
        check_inventory_thresholds.delay(str(order.branch_id))

    except Exception as exc:
        logger.error(
            "deduct_inventory failed for order %s (attempt %d): %s",
            order_id,
            self.request.retries + 1,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)


# ---------------------------------------------------------------------------
# Task 12.4 — check_inventory_thresholds
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def check_inventory_thresholds(self, branch_id: str):
    """
    Check inventory thresholds for a branch and generate alerts:
      - Out of Stock:   quantity <= 0                       (Req 11.5)
      - Low Stock:      0 < quantity <= reorder_threshold   (Req 11.3)
      - Expiry Warning: expiration_date within 3 days       (Req 11.4)

    Multiple alert types can fire for the same item in one run.

    Requirements: 11.3, 11.4, 11.5
    """
    try:
        from apps.inventory.models import InventoryItem

        today = date.today()
        items = InventoryItem.objects.filter(branch_id=branch_id)

        for item in items:
            # Out of stock (checked first — takes precedence over low stock)
            if item.quantity <= 0:
                send_inventory_alert.delay(
                    branch_id=branch_id,
                    alert_type="out_of_stock",
                    item_id=str(item.id),
                    item_name=item.name,
                    details={
                        "quantity": str(item.quantity),
                        "unit": item.unit,
                        "reorder_threshold": str(item.reorder_threshold),
                    },
                )
            elif item.quantity <= item.reorder_threshold:
                # Low stock (only when not already out of stock)
                send_inventory_alert.delay(
                    branch_id=branch_id,
                    alert_type="low_stock",
                    item_id=str(item.id),
                    item_name=item.name,
                    details={
                        "quantity": str(item.quantity),
                        "unit": item.unit,
                        "reorder_threshold": str(item.reorder_threshold),
                    },
                )

            # Expiry warning — independent of stock level
            if item.expiration_date is not None:
                days_until_expiry = (item.expiration_date - today).days
                if days_until_expiry <= 3:
                    send_inventory_alert.delay(
                        branch_id=branch_id,
                        alert_type="expiry_warning",
                        item_id=str(item.id),
                        item_name=item.name,
                        details={
                            "expiration_date": str(item.expiration_date),
                            "days_until_expiry": days_until_expiry,
                            "quantity": str(item.quantity),
                            "unit": item.unit,
                        },
                    )

    except Exception as exc:
        logger.error(
            "check_inventory_thresholds failed for branch %s (attempt %d): %s",
            branch_id,
            self.request.retries + 1,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)


# ---------------------------------------------------------------------------
# Task 12.4 — send_inventory_alert
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_inventory_alert(
    self,
    branch_id: str,
    alert_type: str,
    item_id: str,
    item_name: str,
    details: dict,
):
    """
    Push a WebSocket alert to the ``branch_{branch_id}_inventory`` channel
    group and optionally send an email.

    Uses ``async_to_sync`` so that the synchronous Celery task can call the
    async ``channel_layer.group_send`` coroutine.

    Alert types: ``low_stock``, ``expiry_warning``, ``out_of_stock``

    Requirements: 11.3, 11.4, 11.5
    """
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning(
                "No channel layer configured — skipping WebSocket alert for branch %s",
                branch_id,
            )
            return

        group_name = f"branch_{branch_id}_inventory"

        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "inventory.alert",
                "alert_type": alert_type,
                "item_id": item_id,
                "item_name": item_name,
                "details": details,
            },
        )

        logger.info(
            "Sent '%s' WebSocket alert for item '%s' (branch %s)",
            alert_type,
            item_name,
            branch_id,
        )

        if _dispatch_webhook is not None and alert_type == "low_stock":
            _dispatch_webhook(
                branch_id=branch_id,
                event_type="inventory.low_stock",
                payload={
                    "item_id": item_id,
                    "item_name": item_name,
                    "alert_type": alert_type,
                    "branch_id": branch_id,
                    "details": details,
                },
            )

    except Exception as exc:
        logger.error(
            "send_inventory_alert failed for branch %s item %s (attempt %d): %s",
            branch_id,
            item_id,
            self.request.retries + 1,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)


# ---------------------------------------------------------------------------
# Task — reconcile_menu_availability
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2)
def reconcile_menu_availability(self):
    """
    Periodic task that scans all branches and reconciles MenuItem status
    against current inventory stock levels.

    For each MenuItem that has a Recipe:
      - If ANY ingredient InventoryItem.quantity <= 0 → set
        MenuItem.status = "unavailable" (only if currently "available")
      - If ALL ingredient InventoryItem.quantity > 0 → set
        MenuItem.status = "available" (only if currently "unavailable")

    Manually set "seasonal" / "archived" items are left untouched.

    Scheduled via Celery Beat every 5 minutes.
    """
    from apps.menus.models import MenuItem

    logger.info("Starting reconcile_menu_availability …")

    branch_ids = list(
        MenuItem.objects.filter(is_archived=False)
        .values_list("branch_id", flat=True)
        .distinct()
    )

    total_marked_unavailable = 0
    total_restored = 0

    for branch_id in branch_ids:
        items = MenuItem.objects.filter(
            branch_id=branch_id,
            is_archived=False,
        ).exclude(
            status__in=("seasonal", "archived"),
        ).prefetch_related(
            "recipe__ingredients__inventory_item",
        )

        for item in items:
            try:
                recipe = item.recipe
            except Exception:
                continue

            ingredients = list(recipe.ingredients.all())
            if not ingredients:
                continue

            any_empty = any(
                ing.inventory_item.quantity <= 0
                for ing in ingredients
            )

            if any_empty and item.status == "available":
                item.status = "unavailable"
                item.save(update_fields=["status"])
                total_marked_unavailable += 1
            elif not any_empty and item.status == "unavailable":
                all_ok = all(
                    ing.inventory_item.quantity > 0
                    for ing in ingredients
                )
                if all_ok:
                    item.status = "available"
                    item.save(update_fields=["status"])
                    total_restored += 1

    if total_marked_unavailable or total_restored:
        logger.info(
            "reconcile_menu_availability: %d marked unavailable, %d restored across %d branches",
            total_marked_unavailable, total_restored, len(branch_ids),
        )
        if _cache is not None:
            for bid in branch_ids:
                _cache.delete(f"menu:branch:{bid}")
    else:
        logger.info("reconcile_menu_availability: no changes needed")
