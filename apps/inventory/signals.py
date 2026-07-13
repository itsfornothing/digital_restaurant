"""
inventory/signals.py — Signal handlers for InventoryItem.

Auto-updates MenuItem availability when inventory stock changes:
  - quantity <= 0  → mark linked MenuItems as unavailable
  - quantity > 0   → restore linked MenuItems to available if ALL
                     ingredients now have sufficient stock
"""

import logging

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from apps.inventory.models import InventoryItem

logger = logging.getLogger(__name__)

try:
    from django.core.cache import cache as _cache
except ImportError:
    _cache = None


@receiver(post_init, sender=InventoryItem)
def _capture_old_quantity(sender, instance, **kwargs):
    """Stash the original quantity so post_save can detect changes."""
    instance._old_quantity = instance.quantity


@receiver(post_save, sender=InventoryItem)
def _inventory_availability_handler(sender, instance, **kwargs):
    """
    When an InventoryItem's quantity crosses zero, update all linked
    MenuItems' availability status accordingly.

    Only touches items whose status is "available" (when marking
    unavailable) or "unavailable" (when restoring).  Manually set
    "seasonal" / "archived" items are left alone.
    """
    old_qty = getattr(instance, "_old_quantity", instance.quantity)
    new_qty = instance.quantity

    if old_qty == new_qty:
        return

    from apps.menus.models import MenuItem

    branch_id = instance.branch_id
    changed = False

    if new_qty <= 0:
        # Stock depleted → mark linked available items as unavailable
        affected = MenuItem.objects.filter(
            branch_id=branch_id,
            recipe__ingredients__inventory_item=instance,
            status="available",
        )
        updated = affected.update(status="unavailable")
        if updated:
            logger.info(
                "Signal: marked %d MenuItem(s) unavailable due to '%s' stock depleted (qty=%.4f)",
                updated, instance.name, new_qty,
            )
            changed = True

    elif old_qty <= 0 < new_qty:
        # Stock restored → find linked unavailable items where ALL
        # ingredients now have sufficient quantity
        affected = MenuItem.objects.filter(
            branch_id=branch_id,
            recipe__ingredients__inventory_item=instance,
            status="unavailable",
        )
        restored_count = 0
        for item in affected:
            # Check every ingredient for this item
            all_ok = all(
                ing.inventory_item.quantity > 0
                for ing in item.recipe.ingredients.all()
            )
            if all_ok:
                item.status = "available"
                item.save(update_fields=["status"])
                restored_count += 1
        if restored_count:
            logger.info(
                "Signal: restored %d MenuItem(s) to available after '%s' restocked (qty=%.4f)",
                restored_count, instance.name, new_qty,
            )
            changed = True

    if changed and _cache is not None:
        _cache.delete(f"menu:branch:{branch_id}")
