"""
Migration 0003 — Add composite index on Order(branch_id, placed_at).

This index optimises the kitchen feed query that filters orders by branch
and sorts them chronologically — the most frequent query pattern executed
by the KDS and reception dashboard.

Index name: orders_order_branch_placed_at_idx

Requirements: 19.2 (Task 20.3)
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["branch", "placed_at"],
                name="ord_branch_placed_at_idx",
            ),
        ),
    ]
