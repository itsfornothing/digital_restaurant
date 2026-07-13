"""
billing/services.py

BillingService — subscription resource-limit enforcement.

``BillingService.check_resource_limit(tenant, resource_type)`` is the single
entry point called by create views for Branch, MenuItem, and User objects.

It counts the tenant's current active resources inside the tenant schema
(via django-tenants' ``connection.set_tenant`` context, which is already
active on every tenant-schema request), compares the count against the plan
limit, and raises ``ResourceLimitExceeded`` when the limit has been reached.

Requirements: 2.3
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.tenants.models import Tenant

from apps.billing.exceptions import ResourceLimitExceeded


class BillingService:
    """
    Stateless service class for billing-enforcement logic.

    All methods are static so callers can invoke them without instantiating
    the class:  ``BillingService.check_resource_limit(tenant, 'branches')``.
    """

    # Maps resource_type → the SubscriptionPlan field that holds the limit.
    RESOURCE_LIMITS: dict[str, str] = {
        "branches": "max_branches",
        "menu_items": "max_menu_items",
        "staff_accounts": "max_staff_accounts",
    }

    @staticmethod
    def _get_current_count(resource_type: str) -> int:
        """
        Return the current active count of *resource_type* within the
        tenant schema that is currently active on the DB connection.

        The caller is responsible for ensuring the correct tenant schema is
        active before calling this method (django-tenants middleware does this
        automatically on every tenant-schema HTTP request).

        Raises:
            ValueError: if *resource_type* is not one of the recognised keys.
        """
        if resource_type not in BillingService.RESOURCE_LIMITS:
            raise ValueError(
                f"Unknown resource_type {resource_type!r}. "
                f"Valid values: {list(BillingService.RESOURCE_LIMITS)}"
            )

        if resource_type == "branches":
            from apps.branches.models import Branch

            return Branch.objects.count()

        if resource_type == "menu_items":
            from apps.menus.models import MenuItem

            return MenuItem.objects.filter(is_archived=False).count()

        if resource_type == "staff_accounts":
            from django.contrib.auth import get_user_model

            User = get_user_model()
            return User.objects.filter(is_active=True).count()

        raise ValueError(
            f"Unknown resource_type {resource_type!r}. "
            f"Valid values: {list(BillingService.RESOURCE_LIMITS)}"
        )

    @staticmethod
    def check_resource_limit(tenant: "Tenant", resource_type: str) -> None:
        """
        Check whether *tenant* has reached its plan limit for *resource_type*.

        If the current count >= the plan limit, raises ``ResourceLimitExceeded``.
        If the tenant has no subscription, raises ``ResourceLimitExceeded`` with
        limit=-1 to signal "no subscription found".

        Args:
            tenant:        The Tenant instance (public schema).
            resource_type: One of 'branches', 'menu_items', 'staff_accounts'.

        Raises:
            ResourceLimitExceeded: when the limit is reached or no subscription
                exists.
            ValueError: when *resource_type* is not a recognised key.
        """
        from apps.billing.models import TenantSubscription

        if resource_type not in BillingService.RESOURCE_LIMITS:
            raise ValueError(
                f"Unknown resource_type {resource_type!r}. "
                f"Valid values: {list(BillingService.RESOURCE_LIMITS)}"
            )

        # --- 1. Resolve the subscription and plan ----------------------------
        # TenantSubscription lives in the public schema. Use the public tenant
        # connection to look it up, regardless of the current schema context.
        try:
            from django_tenants.utils import get_public_schema_name, schema_context

            with schema_context(get_public_schema_name()):
                subscription = TenantSubscription.objects.select_related("plan").get(
                    tenant_id=tenant.pk
                )
        except TenantSubscription.DoesNotExist:
            # No subscription → treat as exceeded with sentinel limit value
            current = BillingService._get_current_count(resource_type)
            raise ResourceLimitExceeded(
                resource_type=resource_type,
                current_count=current,
                limit=-1,
            ) from None

        plan = subscription.plan
        plan_field = BillingService.RESOURCE_LIMITS[resource_type]
        limit: int = getattr(plan, plan_field)

        # --- 2. Count current active resources -------------------------------
        current_count = BillingService._get_current_count(resource_type)

        # --- 3. Enforce limit ------------------------------------------------
        if current_count >= limit:
            raise ResourceLimitExceeded(
                resource_type=resource_type,
                current_count=current_count,
                limit=limit,
            )
