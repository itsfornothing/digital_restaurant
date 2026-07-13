"""
billing/serializers.py

Serializers for subscription plan and tenant subscription management.

Requirements: 2.1, 2.2, 2.5, 2.6
"""

from datetime import date

from rest_framework import serializers

from apps.billing.models import SubscriptionPlan, TenantSubscription


# ---------------------------------------------------------------------------
# SubscriptionPlanSerializer
# ---------------------------------------------------------------------------


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for SubscriptionPlan.

    Used by:
      - GET  /api/v1/plans/       → list all plans
      - POST /api/v1/plans/       → create a new plan
      - PATCH /api/v1/plans/{id}/ → update an existing plan
    """

    class Meta:
        model = SubscriptionPlan
        fields = [
            "id",
            "name",
            "max_branches",
            "max_menu_items",
            "max_staff_accounts",
            "feature_flags",
            "price_etb",
        ]


# ---------------------------------------------------------------------------
# TenantSubscriptionSerializer
# ---------------------------------------------------------------------------


class TenantSubscriptionSerializer(serializers.ModelSerializer):
    """
    Serializer for creating or replacing a tenant's subscription.

    Used by:
      - POST /api/v1/tenants/{id}/subscription/

    Input fields:
      - plan_id            (int)  — the SubscriptionPlan to assign
      - status             (str)  — optional, defaults to 'active'
      - current_period_start (date) — optional, defaults to today
      - current_period_end   (date) — required
    """

    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.all(),
        source="plan",
        write_only=True,
    )
    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = TenantSubscription
        fields = [
            "id",
            "plan_id",
            "plan",
            "status",
            "current_period_start",
            "current_period_end",
        ]
        read_only_fields = ["id"]

    def get_fields(self):
        fields = super().get_fields()
        # Make current_period_start optional (defaults to today in create)
        fields["current_period_start"].required = False
        return fields


# ---------------------------------------------------------------------------
# TenantUsageSerializer  (plain dict serializer — no model)
# ---------------------------------------------------------------------------


class ResourceUsageSerializer(serializers.Serializer):
    """Represents used/limit for a single resource type."""

    used = serializers.IntegerField()
    limit = serializers.IntegerField()


class TenantUsageSerializer(serializers.Serializer):
    """
    Read-only serializer for tenant usage metrics.

    Response shape::

        {
            "tenant_id": "...",
            "plan": "Starter",
            "branches": {"used": 2, "limit": 5},
            "menu_items": {"used": 20, "limit": 50},
            "staff_accounts": {"used": 3, "limit": 10},
            "subscription_status": "active"
        }
    """

    tenant_id = serializers.CharField()
    plan = serializers.CharField()
    branches = ResourceUsageSerializer()
    menu_items = ResourceUsageSerializer()
    staff_accounts = ResourceUsageSerializer()
    subscription_status = serializers.CharField()
