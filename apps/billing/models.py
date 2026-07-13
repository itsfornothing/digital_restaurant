"""
billing/models.py — Subscription plans and tenant subscription records.

Both models live in the public (shared) PostgreSQL schema via SHARED_APPS.
They are never copied into tenant schemas.
"""

from django.db import models


class SubscriptionPlan(models.Model):
    """
    Defines a subscription tier available on the platform.

    Each plan caps the number of branches, menu items, and staff accounts
    a tenant may create, and enables or disables optional feature flags
    (e.g. white-label domain, advanced analytics).
    """

    name = models.CharField(max_length=100, unique=True)
    max_branches = models.PositiveIntegerField(
        help_text="Maximum number of branches a tenant on this plan may create."
    )
    max_menu_items = models.PositiveIntegerField(
        help_text="Maximum total menu items across all branches."
    )
    max_staff_accounts = models.PositiveIntegerField(
        help_text="Maximum number of active staff accounts across all branches."
    )
    # Arbitrary key/value flags, e.g. {"white_label_domain": true, "advanced_analytics": false}
    feature_flags = models.JSONField(default=dict)
    # Price in Ethiopian Birr (ETB)
    price_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Monthly subscription price in Ethiopian Birr (ETB).",
    )

    class Meta:
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"
        ordering = ["price_etb"]

    def __str__(self) -> str:
        return f"{self.name} (ETB {self.price_etb})"


class TenantSubscription(models.Model):
    """
    Links a Tenant to its current SubscriptionPlan and tracks billing status.

    One-to-one with Tenant because a tenant has exactly one active subscription
    at any point in time.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        OVERDUE = "overdue", "Overdue"

    # Importing Tenant via string reference avoids a circular import at module
    # load time (tenants.models imports nothing from billing).
    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    current_period_start = models.DateField()
    current_period_end = models.DateField()

    class Meta:
        verbose_name = "Tenant Subscription"
        verbose_name_plural = "Tenant Subscriptions"
        ordering = ["-current_period_start"]

    def __str__(self) -> str:
        return f"{self.tenant} — {self.plan.name} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        """Convenience check used by billing enforcement logic."""
        return self.status == self.Status.ACTIVE
