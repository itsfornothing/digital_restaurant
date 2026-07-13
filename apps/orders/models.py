"""
orders/models.py

Order and OrderItem models for the kitchen workflow and order management.

Order lifecycle state machine (Requirement 10.3):
    confirmed → received → preparing → ready → served
    confirmed → cancelled

order_number auto-generated as BR{branch_id_short}-{YYYYMMDD}-{sequence}
where branch_id_short = first 8 hex chars of the branch UUID (dashes stripped).

unit_price on OrderItem is a price snapshot taken at placement time to ensure
price immutability for active orders (Requirement 14.8).

Requirements: 10.3, 14.8
"""

import uuid

from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Order status constants
# ---------------------------------------------------------------------------

ORDER_STATUS_CHOICES = [
    ("confirmed", "Confirmed"),
    ("received", "Received"),
    ("preparing", "Preparing"),
    ("ready", "Ready"),
    ("served", "Served"),
    ("cancelled", "Cancelled"),
]

# State machine: maps current status -> set of valid next statuses
VALID_TRANSITIONS: dict[str, set[str]] = {
    "confirmed": {"received", "cancelled"},
    "received": {"preparing", "cancelled"},
    "preparing": {"ready", "cancelled"},
    "ready": {"served"},
    "served": set(),    # terminal
    "cancelled": set(), # terminal
}


# ---------------------------------------------------------------------------
# Order model
# ---------------------------------------------------------------------------

class Order(models.Model):
    """
    Represents a customer's food order at a Branch table or room.

    State Machine
    -------------
    confirmed → received → preparing → ready → served
    confirmed → cancelled

    Fields
    ------
    id              — UUID primary key
    order_number    — Auto-generated human-readable identifier: BR<branch_short>-<YYYYMMDD>-<seq>
    branch          — FK to the Branch where the order was placed (PROTECT)
    table           — FK to the specific Table (PROTECT, null for room orders)
    room            — FK to the specific Room (PROTECT, null for table orders)
    status          — Current order lifecycle status (db_index for KDS queries)
    customer_name   — Optional customer name (blank for anonymous orders)
    customer_phone  — Optional customer phone (blank for anonymous orders)
    is_anonymized   — Set True by the nightly anonymization Celery task (Task 16)
    placed_at       — Auto-set when the order is first created
    total_amount    — Sum of (unit_price × quantity) for all OrderItems at placement

    Requirements: 10.3, 14.8
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        help_text=(
            "Auto-generated: BR<branch_id_short>-<YYYYMMDD>-<sequence>. "
            "Leave blank — populated automatically on first save."
        ),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    table = models.ForeignKey(
        "branches.Table",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    room = models.ForeignKey(
        "branches.Room",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ORDER_STATUS_CHOICES,
        default="confirmed",
        db_index=True,
    )
    customer_name = models.CharField(max_length=200, blank=True, default="")
    customer_phone = models.CharField(max_length=30, blank=True, default="")
    is_anonymized = models.BooleanField(
        default=False,
        help_text="Set True by the nightly anonymization task; clears customer PII.",
    )
    placed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total order value in branch currency at placement time.",
    )

    class Meta:
        app_label = "orders"
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["placed_at"]
        indexes = [
            # Composite index for kitchen feed queries: filter by branch,
            # sort by placed_at. Requirement 19.2 (Task 20.3).
            models.Index(
                fields=["branch", "placed_at"],
                name="ord_branch_placed_at_idx",
            ),
        ]

    # ------------------------------------------------------------------
    # Auto-generate order_number on first save
    # ------------------------------------------------------------------

    def _generate_order_number(self) -> str:
        """
        Generate a unique order number in the format:
            BR{branch_id_short}-{YYYYMMDD}-{sequence:04d}

        branch_id_short = first 8 hex chars of branch UUID (dashes removed, uppercased).
        sequence        = count of orders for this branch today + 1 (1-based, 4-digit zero-padded).
        """
        branch_short = str(self.branch_id).replace("-", "")[:8].upper()
        date_str = timezone.now().strftime("%Y%m%d")
        prefix = f"BR{branch_short}-{date_str}-"
        # Count today's orders for this branch to derive the sequence number.
        today_count = Order.objects.filter(
            branch=self.branch,
            order_number__startswith=prefix,
        ).count()
        return f"{prefix}{today_count + 1:04d}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # State machine helper
    # ------------------------------------------------------------------

    def is_valid_transition(self, new_status: str) -> bool:
        """
        Return True if transitioning from the current status to *new_status*
        is a legal move in the order lifecycle state machine.

        Valid paths:
            confirmed → received | cancelled
            received  → preparing
            preparing → ready
            ready     → served
            served    → (terminal — no further transitions)
            cancelled → (terminal — no further transitions)
        """
        return new_status in VALID_TRANSITIONS.get(self.status, set())

    def __str__(self) -> str:
        return f"Order {self.order_number} [{self.status}]"


# ---------------------------------------------------------------------------
# OrderItem model
# ---------------------------------------------------------------------------

class OrderItem(models.Model):
    """
    A single line item within an Order.

    unit_price is a snapshot of MenuItem.price at the time the order is placed.
    This ensures that subsequent price changes to the MenuItem do not affect
    historical or active orders (Requirement 14.8).

    Fields
    ------
    id                   — UUID primary key
    order                — FK to the parent Order (CASCADE delete)
    menu_item            — FK to the ordered MenuItem (PROTECT against accidental deletion)
    quantity             — Number of this item ordered (positive integer)
    unit_price           — Price snapshot at placement time (immutable after creation)
    special_instructions — Free-text notes from the customer (e.g. "no onions")
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    menu_item = models.ForeignKey(
        "menus.MenuItem",
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    quantity = models.PositiveSmallIntegerField(
        help_text="Number of servings of this item in the order.",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text=(
            "Snapshot of MenuItem.price at the moment this order was placed. "
            "Must not be updated after creation (Requirement 14.8)."
        ),
    )
    special_instructions = models.TextField(
        blank=True,
        default="",
        help_text="Customer notes for preparation (e.g. 'extra spicy', 'no nuts').",
    )

    class Meta:
        app_label = "orders"
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        ordering = ["id"]

    def __str__(self) -> str:
        return (
            f"{self.quantity}× {getattr(self.menu_item, 'name', self.menu_item_id)} "
            f"(Order: {self.order_id})"
        )
