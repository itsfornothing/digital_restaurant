"""
Property-Based Tests: Subscription Resource Limit Enforcement (Property 4)

For any SubscriptionPlan with limit N on a given resource type (branches,
menu_items, or staff_accounts), a tenant on that plan shall be able to create
exactly N resources; the (N+1)th creation attempt shall be rejected with an
error identifying the exceeded limit.

Sub-properties tested:
  4a — Under-limit creation always succeeds
  4b — At-limit creation is rejected
  4c — Exception carries correct metadata
  4d — Over-limit is also rejected
  4e — Archived/inactive resources don't count toward the limit

Validates: Requirements 2.3
"""

import uuid
from datetime import date

from django.contrib.auth import get_user_model
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.billing.exceptions import ResourceLimitExceeded
from apps.billing.services import BillingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tenant(slug: str):
    from apps.tenants.models import Tenant
    return Tenant.objects.create(
        schema_name=slug,
        name=f"Test {slug}",
        slug=slug,
        is_active=True,
    )


def _make_plan(name, max_branches=99, max_menu_items=99, max_staff_accounts=99):
    from apps.billing.models import SubscriptionPlan
    return SubscriptionPlan.objects.create(
        name=name,
        max_branches=max_branches,
        max_menu_items=max_menu_items,
        max_staff_accounts=max_staff_accounts,
        price_etb="0.00",
    )


def _make_subscription(tenant, plan):
    from apps.billing.models import TenantSubscription
    return TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        status=TenantSubscription.Status.ACTIVE,
        current_period_start=date.today(),
        current_period_end=date(9999, 12, 31),
    )


def _plan_kwargs(resource_type: str, limit: int) -> dict:
    """Return the SubscriptionPlan kwargs that set only the given resource's limit."""
    mapping = {
        "branches": {"max_branches": limit},
        "menu_items": {"max_menu_items": limit},
        "staff_accounts": {"max_staff_accounts": limit},
    }
    return mapping[resource_type]


def _create_resources(resource_type: str, count: int, **kwargs):
    """Create `count` resources of the given type.

    kwargs accepted:
      is_archived (bool) — for menu_items
      is_active   (bool) — for staff_accounts
    """
    User = get_user_model()
    for _ in range(count):
        uid = uuid.uuid4().hex[:8]
        if resource_type == "branches":
            from apps.branches.models import Branch
            Branch.objects.create(name=f"Branch-{uid}")
        elif resource_type == "menu_items":
            from apps.menus.models import MenuItem
            from apps.branches.models import Branch
            import decimal as _decimal
            is_archived = kwargs.get("is_archived", False)
            # Get or create a shared test branch for menu item creation
            branch, _ = Branch.objects.get_or_create(
                name="__billing_test_branch__",
                defaults={
                    "address": "Test Address",
                    "phone": "0900000000",
                    "email": "billing_test@test.com",
                },
            )
            MenuItem.objects.create(
                name=f"Item-{uid}",
                branch=branch,
                price=_decimal.Decimal("10.00"),
                prep_time_minutes=5,
                is_archived=is_archived,
            )
        elif resource_type == "staff_accounts":
            is_active = kwargs.get("is_active", True)
            User.objects.create_user(
                email=f"u{uid}@test.com",
                password="Pass123!",
                role="Receptionist",
                is_active=is_active,
            )


# ---------------------------------------------------------------------------
# Property 4 Tests
#
# Inheriting from django.test.TestCase wraps each test METHOD in a
# transaction that is rolled back after the method completes.  Hypothesis
# runs all its examples inside that one method, so every example starts from
# an empty DB state as Hypothesis reuses the same DB transaction.
#
# To isolate individual Hypothesis examples from each other we use unique
# slugs per iteration (uuid-based) so objects created in one example don't
# interfere with assertions in another even if they coexist in the DB during
# that method's transaction.
# ---------------------------------------------------------------------------

class TestPropertyResourceLimitEnforcement(TestCase):
    """
    **Validates: Requirements 2.3**

    Property-based tests for subscription resource limit enforcement.
    Each test method creates its own isolated tenant + plan per Hypothesis
    iteration via unique uuid-based slugs to prevent cross-iteration
    contamination inside a single transaction.
    """

    # -----------------------------------------------------------------------
    # 4a — Under-limit creation always succeeds
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=1, max_value=20),
        k=st.integers(min_value=0, max_value=19),
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    @settings(max_examples=500)
    def test_property_4a_under_limit_does_not_raise(self, n, k, resource_type):
        """
        **Validates: Requirements 2.3**

        For any limit N >= 1 and count k in [0, N-1], calling
        check_resource_limit with k existing resources must NOT raise.
        """
        assume(k < n)

        slug = f"4a-{resource_type}-{n}-{k}-{uuid.uuid4().hex[:8]}"
        tenant = _make_tenant(slug)
        plan = _make_plan(f"Plan-{slug}", **_plan_kwargs(resource_type, n))
        _make_subscription(tenant, plan)

        _create_resources(resource_type, k)

        # Must NOT raise — k resources are strictly under limit n
        BillingService.check_resource_limit(tenant, resource_type)

    # -----------------------------------------------------------------------
    # 4b — At-limit creation is rejected
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=0, max_value=10),
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    @settings(max_examples=500)
    def test_property_4b_at_limit_raises(self, n, resource_type):
        """
        **Validates: Requirements 2.3**

        For any limit N >= 0, creating exactly N resources then calling
        check_resource_limit MUST raise ResourceLimitExceeded.
        """
        slug = f"4b-{resource_type}-{n}-{uuid.uuid4().hex[:8]}"
        tenant = _make_tenant(slug)
        plan = _make_plan(f"Plan-{slug}", **_plan_kwargs(resource_type, n))
        _make_subscription(tenant, plan)

        _create_resources(resource_type, n)

        with self.assertRaises(ResourceLimitExceeded):
            BillingService.check_resource_limit(tenant, resource_type)

    # -----------------------------------------------------------------------
    # 4c — Exception carries correct metadata
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=0, max_value=10),
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    @settings(max_examples=500)
    def test_property_4c_exception_carries_correct_metadata(self, n, resource_type):
        """
        **Validates: Requirements 2.3**

        When ResourceLimitExceeded is raised, the exception's resource_type,
        current_count, and limit fields must exactly match the plan and
        current state.
        """
        slug = f"4c-{resource_type}-{n}-{uuid.uuid4().hex[:8]}"
        tenant = _make_tenant(slug)
        plan = _make_plan(f"Plan-{slug}", **_plan_kwargs(resource_type, n))
        _make_subscription(tenant, plan)

        _create_resources(resource_type, n)

        with self.assertRaises(ResourceLimitExceeded) as ctx:
            BillingService.check_resource_limit(tenant, resource_type)

        exc = ctx.exception
        self.assertEqual(exc.resource_type, resource_type,
            f"Expected resource_type={resource_type!r}, got {exc.resource_type!r}")

        # Count the resources this iteration created (tenant-specific slug
        # ensures resources for *this* tenant only, but _get_current_count
        # counts all objects in the DB for this resource type).
        # We count what the service sees: all objects in the shared test DB
        # that are active/not-archived.
        if resource_type == "branches":
            from apps.branches.models import Branch
            expected_count = Branch.objects.count()
        elif resource_type == "menu_items":
            from apps.menus.models import MenuItem
            expected_count = MenuItem.objects.filter(is_archived=False).count()
        else:
            User = get_user_model()
            expected_count = User.objects.filter(is_active=True).count()

        self.assertEqual(exc.current_count, expected_count,
            f"current_count mismatch: expected {expected_count}, got {exc.current_count}")
        self.assertEqual(exc.limit, n,
            f"Expected limit={n}, got {exc.limit}")

    # -----------------------------------------------------------------------
    # 4d — Over-limit is also rejected
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=0, max_value=8),
        extra=st.integers(min_value=1, max_value=5),
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    @settings(max_examples=500)
    def test_property_4d_over_limit_raises(self, n, extra, resource_type):
        """
        **Validates: Requirements 2.3**

        For any limit N, having more than N resources (N+1 through N+5)
        still raises ResourceLimitExceeded.
        """
        slug = f"4d-{resource_type}-{n}-{extra}-{uuid.uuid4().hex[:8]}"
        tenant = _make_tenant(slug)
        plan = _make_plan(f"Plan-{slug}", **_plan_kwargs(resource_type, n))
        _make_subscription(tenant, plan)

        _create_resources(resource_type, n + extra)

        with self.assertRaises(ResourceLimitExceeded) as ctx:
            BillingService.check_resource_limit(tenant, resource_type)

        exc = ctx.exception
        self.assertEqual(exc.resource_type, resource_type)
        self.assertEqual(exc.limit, n)

    # -----------------------------------------------------------------------
    # 4e — Archived/inactive resources don't count toward the limit
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=1, max_value=10),
        soft_deleted_count=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=500)
    def test_property_4e_soft_deleted_resources_not_counted(self, n, soft_deleted_count):
        """
        **Validates: Requirements 2.3**

        Archived menu items (is_archived=True) and inactive staff accounts
        (is_active=False) must NOT count toward plan limits.

        For menu_items: n-1 active + soft_deleted_count archived → still under
        limit n → must NOT raise.

        For staff_accounts: n-1 active + soft_deleted_count inactive → still
        under limit n → must NOT raise.
        """
        # --- menu_items ---
        slug_menu = f"4e-menu-{n}-{soft_deleted_count}-{uuid.uuid4().hex[:8]}"
        tenant_menu = _make_tenant(slug_menu)
        plan_menu = _make_plan(
            f"Plan-{slug_menu}",
            **_plan_kwargs("menu_items", n),
        )
        _make_subscription(tenant_menu, plan_menu)

        # (n-1) active items — strictly under limit n
        _create_resources("menu_items", n - 1, is_archived=False)
        # archived items must NOT count
        _create_resources("menu_items", soft_deleted_count, is_archived=True)

        # Should NOT raise: only (n-1) active items < limit n
        BillingService.check_resource_limit(tenant_menu, "menu_items")

        # --- staff_accounts ---
        slug_staff = f"4e-staff-{n}-{soft_deleted_count}-{uuid.uuid4().hex[:8]}"
        tenant_staff = _make_tenant(slug_staff)
        plan_staff = _make_plan(
            f"Plan-{slug_staff}",
            **_plan_kwargs("staff_accounts", n),
        )
        _make_subscription(tenant_staff, plan_staff)

        # (n-1) active users — strictly under limit n
        _create_resources("staff_accounts", n - 1, is_active=True)
        # inactive users must NOT count
        _create_resources("staff_accounts", soft_deleted_count, is_active=False)

        # Should NOT raise: only (n-1) active users < limit n
        BillingService.check_resource_limit(tenant_staff, "staff_accounts")
