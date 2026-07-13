"""
Migration 0002 — InventoryItem and Supplier models.

These models are required by the Ingredient FK in apps/menus (Task 10.3).
Full inventory management API endpoints are implemented in Task 12.

Requirements: 11.1
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
        ("branches", "0003_full_branch_table"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Supplier
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Supplier",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("contact", models.TextField(blank=True, default="")),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="suppliers",
                        to="branches.branch",
                    ),
                ),
            ],
            options={
                "verbose_name": "Supplier",
                "verbose_name_plural": "Suppliers",
                "ordering": ["name"],
                "app_label": "inventory",
            },
        ),
        # ------------------------------------------------------------------
        # InventoryItem
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="InventoryItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("category", models.CharField(blank=True, default="", max_length=100)),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=4,
                        max_digits=12,
                        help_text="Current stock quantity. May be negative (Requirement 11.7).",
                    ),
                ),
                ("unit", models.CharField(max_length=20)),
                (
                    "purchase_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        help_text="Cost per unit in branch currency.",
                    ),
                ),
                (
                    "expiration_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        help_text="Expiration date for perishable items.",
                    ),
                ),
                (
                    "reorder_threshold",
                    models.DecimalField(
                        decimal_places=4,
                        max_digits=12,
                        help_text="Quantity level at which a Low Stock alert is triggered.",
                    ),
                ),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inventory_items",
                        to="branches.branch",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inventory_items",
                        to="inventory.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Inventory Item",
                "verbose_name_plural": "Inventory Items",
                "ordering": ["name"],
                "app_label": "inventory",
            },
        ),
    ]
