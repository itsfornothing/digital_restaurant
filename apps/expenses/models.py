"""
expenses/models.py

Expense model for tracking branch operating costs.

EXPENSE_CATEGORIES covers all standard restaurant cost centres.
The `amount` field is guarded by MinValueValidator to ensure only positive
values are accepted (Requirement 12.1).

Requirements: 12.1, 12.2, 12.3
"""

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from shared.storage import R2Storage

EXPENSE_CATEGORIES = [
    ("food_purchases", "Food Purchases"),
    ("staff_salaries", "Staff Salaries"),
    ("utilities", "Utilities"),
    ("rent", "Rent"),
    ("maintenance", "Maintenance"),
    ("marketing", "Marketing"),
    ("transportation", "Transportation"),
    ("miscellaneous", "Miscellaneous"),
]


class Expense(models.Model):
    """
    Records a single operating expense for a Branch.

    Fields
    ------
    id               — UUID primary key
    branch           — FK to the Branch this expense belongs to (CASCADE)
    description      — Short description of the expense (max 500 chars)
    category         — Standardised expense category from EXPENSE_CATEGORIES
    amount           — Positive monetary value (min 0.01)
    date_incurred    — The calendar date the expense was incurred
    notes            — Free-text additional context (optional)
    reference_number — External reference / invoice number (optional)
    attachment       — Optional file attachment stored in Cloudflare R2
    created_at       — Auto-set creation timestamp
    updated_at       — Auto-updated on every save

    Requirements: 12.1, 12.2, 12.3
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    description = models.CharField(max_length=500)
    category = models.CharField(
        max_length=30,
        choices=EXPENSE_CATEGORIES,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date_incurred = models.DateField()
    notes = models.TextField(blank=True, default="")
    reference_number = models.CharField(max_length=100, blank=True, default="")
    attachment = models.FileField(
        storage=R2Storage(),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "expenses"
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"
        ordering = ["-date_incurred", "-created_at"]
        indexes = [
            models.Index(fields=["branch", "date_incurred"]),
            models.Index(fields=["branch", "category"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_category_display()} – {self.amount} ({self.date_incurred})"
