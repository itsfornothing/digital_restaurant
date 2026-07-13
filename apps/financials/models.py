"""
financials/models.py

Income and ProfitRecord models.

Income tracks all revenue flowing into a Branch — whether from an order,
event, catering, or another source.

ProfitRecord is a pre-computed snapshot of net profit for a given branch
and time period (daily / weekly / monthly / annual).  It is updated by the
`update_profit` Celery task after every income or expense change.

Requirements: 13.1, 13.2
"""

import uuid

from django.db import models


INCOME_SOURCE_CHOICES = [
    ("order", "Order"),
    ("event", "Event"),
    ("catering", "Catering"),
    ("other", "Other"),
]

PERIOD_TYPE_CHOICES = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("annual", "Annual"),
]


class Income(models.Model):
    """
    Records a single revenue event for a Branch.

    source='order' entries are created automatically by the `record_income`
    Celery task when an Order transitions to 'served'.  Other sources can be
    created manually via the IncomeViewSet (IsBranchManager).

    Fields
    ------
    id          — UUID primary key
    branch      — FK to the Branch this income belongs to (CASCADE)
    source      — Revenue source (order / event / catering / other)
    order       — Optional FK to the originating Order (SET_NULL on delete)
    amount      — Positive revenue amount (max_digits=12)
    description — Optional free-text description
    date        — Calendar date the income was earned
    created_at  — Auto-set creation timestamp

    Requirements: 13.1, 13.2
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="income_records",
    )
    source = models.CharField(
        max_length=20,
        choices=INCOME_SOURCE_CHOICES,
        default="order",
    )
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="income_records",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True, default="")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "financials"
        verbose_name = "Income"
        verbose_name_plural = "Income Records"
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["branch", "date"]),
            models.Index(fields=["branch", "source"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()} – {self.amount} ({self.date})"


class ProfitRecord(models.Model):
    """
    Pre-computed profit snapshot for a Branch over a specific time period.

    Upserted by the `update_profit` Celery task.

    Fields
    ------
    id             — UUID primary key
    branch         — FK to the Branch (CASCADE)
    period_type    — Aggregation granularity (daily / weekly / monthly / annual)
    period_start   — First day of the period (inclusive)
    period_end     — Last day of the period (inclusive)
    total_income   — Sum of Income.amount for the period
    total_expenses — Sum of Expense.amount for the period
    net_profit     — total_income − total_expenses
    last_updated   — Auto-updated on every upsert

    Requirements: 13.2
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="profit_records",
    )
    period_type = models.CharField(
        max_length=20,
        choices=PERIOD_TYPE_CHOICES,
    )
    period_start = models.DateField()
    period_end = models.DateField()
    total_income = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    total_expenses = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    net_profit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "financials"
        verbose_name = "Profit Record"
        verbose_name_plural = "Profit Records"
        unique_together = [("branch", "period_type", "period_start")]
        ordering = ["-period_start"]

    def __str__(self) -> str:
        return (
            f"ProfitRecord({self.branch_id}, {self.period_type}, "
            f"{self.period_start}–{self.period_end}, net={self.net_profit})"
        )
