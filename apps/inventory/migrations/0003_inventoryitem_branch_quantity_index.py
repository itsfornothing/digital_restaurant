"""
Migration 0003 — Add composite index on InventoryItem(branch_id, quantity).

This index optimises low-stock queries that filter inventory items by branch
and sort/filter on quantity — used by check_inventory_thresholds Celery task
and the inventory report endpoint.

Index name: inventory_item_branch_quantity_idx

Requirements: 19.2 (Task 20.3)
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_inventoryitem_supplier"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="inventoryitem",
            index=models.Index(
                fields=["branch", "quantity"],
                name="inv_item_branch_qty_idx",
            ),
        ),
    ]
