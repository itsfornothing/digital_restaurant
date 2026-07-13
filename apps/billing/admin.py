"""
billing/admin.py — Register SubscriptionPlan and TenantSubscription in the
Django admin so Super_Admins can manage plans and subscriptions via the UI.
"""

from django.contrib import admin

from .models import SubscriptionPlan, TenantSubscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    """Admin for subscription tiers."""

    list_display = ("name", "price_etb", "max_branches", "max_menu_items", "max_staff_accounts")
    search_fields = ("name",)
    ordering = ("price_etb",)


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    """Admin for per-tenant subscription records."""

    list_display = ("tenant", "plan", "status", "current_period_start", "current_period_end")
    list_filter = ("status", "plan")
    search_fields = ("tenant__name", "tenant__slug", "plan__name")
    raw_id_fields = ("tenant",)
    ordering = ("-current_period_start",)
