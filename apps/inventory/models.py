"""
inventory/models.py

InventoryItem and Supplier models for stock management.

Full CRUD implementation in Task 12. This file provides the InventoryItem
model definition required by the Ingredient FK in apps/menus/models.py
(Requirement 9.7) and by the BillingService usage counter.

All models live in tenant schemas (TENANT_APPS).

Requirements: 11.1 (full implementation Task 12)
"""

import uuid

from django.db import models


class Supplier(models.Model):
    """
    A supplier contact for inventory items.

    Fields:
        id      — UUID primary key
        branch  — FK to owning Branch (CASCADE delete)
        name    — Supplier company/person name
        contact — Contact details (phone, email, address as free text)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="suppliers",
    )
    name = models.CharField(max_length=200)
    contact = models.TextField(blank=True, default="")

    class Meta:
        app_label = "inventory"
        verbose_name = "Supplier"
        verbose_name_plural = "Suppliers"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class InventoryItem(models.Model):
    """
    A stock item tracked by the Inventory_Service.

    Each InventoryItem belongs to a Branch and can be linked to MenuItem
    recipes via the Ingredient model.  When an order transitions to the
    Preparing state, the Celery task ``deduct_inventory`` decrements the
    ``quantity`` field by the recipe-specified amount for each ingredient
    (Requirements 11.1, 11.2).

    Automatic alerts are generated for:
        - Low Stock  : quantity <= reorder_threshold  (Req 11.3)
        - Expiry Warn: expiration_date within 3 days   (Req 11.4)
        - Out of Stock: quantity <= 0                  (Req 11.5)

    Fields:
        id               — UUID primary key
        branch           — FK to owning Branch (CASCADE delete)
        name             — Item name (e.g. "Chicken Breast")
        category         — Free-text category (e.g. "Protein", "Vegetables")
        quantity         — Current stock quantity (may go negative per Req 11.7)
        unit             — Unit of measure (e.g. "kg", "litres", "pieces")
        purchase_price   — Cost per unit in branch currency
        supplier         — Optional FK to Supplier (SET_NULL on delete)
        expiration_date  — Optional expiration date for perishables
        reorder_threshold — Minimum quantity before Low Stock alert fires
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="inventory_items",
    )
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default="")
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="Current stock quantity. May be negative (Requirement 11.7).",
    )
    unit = models.CharField(max_length=20)
    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Cost per unit in branch currency.",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_items",
    )
    expiration_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expiration date for perishable items.",
    )
    reorder_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="Quantity level at which a Low Stock alert is triggered.",
    )

    class Meta:
        app_label = "inventory"
        verbose_name = "Inventory Item"
        verbose_name_plural = "Inventory Items"
        ordering = ["name"]
        indexes = [
            # Composite index for low-stock queries: filter by branch,
            # sort/filter on quantity. Requirement 19.2 (Task 20.3).
            models.Index(
                fields=["branch", "quantity"],
                name="inv_item_branch_qty_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.unit})"
