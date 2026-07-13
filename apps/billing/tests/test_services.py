"""
apps/billing/tests/test_services.py

Unit tests for BillingService.check_resource_limit.

Covers:
  - Creating a resource when under the plan limit → succeeds (no exception)
  - Creating a resource when AT the plan limit → raises ResourceLimitExceeded
  - Tenant with no subscription → raises ResourceLimitExceeded (limit=-1)
  - All three resource types: branches, menu_items, staff_accounts

The tests use SQLite in-memory (config.settings.testing) and do NOT rely on
PostgreSQL schema switching.  Resource counts are driven directly by creating
model objects in the default test DB.

Requirements: 2.3
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model

from apps.billing.exceptions import ResourceLimitExceeded
from apps.billing.models import SubscriptionPlan, TenantSubscription
from apps.billing.services import BillingService
from apps.tenants.models import Tenant

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_tenant(slug: str) -> Tenant:
    """Create a minimal Tenant in the public schema."""
    return Tenant.objects.create(
        schema_name=slug,
        name=f"Test Tenant {slug}",
        slug=slug,
        is_active=True,
    )


def _make_plan(
    name: str = "Starter",
    max_branches: int = 3,
    max_menu_items: int = 50,
    max_staff_accounts: int = 5,
) -> SubscriptionPlan:
    return SubscriptionPlan.objects.create(
        name=name,
        max_branches=max_branches,
        max_menu_items=max_menu_items,
        max_staff_accounts=max_staff_accounts,
        price_etb="0.00",
    )


def _make_subscription(tenant: Tenant, plan: SubscriptionPlan) -> TenantSubscription:
    return TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        status=TenantSubscription.Status.ACTIVE,
        current_period_start=date.today(),
        current_period_end=date(9999, 12, 31),
    )


def _make_branch(name: str = "Branch A"):
    from apps.branches.models import Branch

    return Branch.objects.create(name=name)


def _make_menu_item(name: str = "Item A", is_archived: bool = False):
    from apps.menus.models import MenuItem
    from apps.branches.models import Branch
    import decimal

    # Get or create a shared test branch for billing service tests
    branch, _ = Branch.objects.get_or_create(
        name="__billing_svc_test_branch__",
        defaults={
            "address": "Test Address",
            "phone": "0900000000",
            "email": "billing_svc_test@test.com",
        },
    )
    return MenuItem.objects.create(
        name=name,
        branch=branch,
        price=decimal.Decimal("10.00"),
        prep_time_minutes=5,
        is_archived=is_archived,
    )


def _make_staff_user(email: str, is_active: bool = True) -> User:
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        role="Receptionist",
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# Tests — branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckResourceLimitBranches:
    """Tests for resource_type='branches'."""

    def test_under_limit_does_not_raise(self):
        """0 branches, limit=3 → should not raise."""
        tenant = _make_tenant("tenant_a")
        plan = _make_plan(name="StarterA", max_branches=3)
        _make_subscription(tenant, plan)

        # No branches created yet
        # Should succeed silently
        BillingService.check_resource_limit(tenant, "branches")

    def test_one_below_limit_does_not_raise(self):
        """2 branches, limit=3 → should not raise."""
        tenant = _make_tenant("tenant_b")
        plan = _make_plan(name="StarterB", max_branches=3)
        _make_subscription(tenant, plan)

        _make_branch("B1")
        _make_branch("B2")

        BillingService.check_resource_limit(tenant, "branches")

    def test_at_limit_raises(self):
        """3 branches, limit=3 → should raise ResourceLimitExceeded."""
        tenant = _make_tenant("tenant_c")
        plan = _make_plan(name="StarterC", max_branches=3)
        _make_subscription(tenant, plan)

        _make_branch("C1")
        _make_branch("C2")
        _make_branch("C3")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        exc = exc_info.value
        assert exc.resource_type == "branches"
        assert exc.current_count == 3
        assert exc.limit == 3

    def test_over_limit_raises(self):
        """4 branches, limit=3 → should raise ResourceLimitExceeded."""
        tenant = _make_tenant("tenant_d")
        plan = _make_plan(name="StarterD", max_branches=3)
        _make_subscription(tenant, plan)

        for i in range(4):
            _make_branch(f"D{i}")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        assert exc_info.value.current_count == 4
        assert exc_info.value.limit == 3

    def test_limit_zero_always_raises(self):
        """Limit=0 → any count (even 0 branches) must raise."""
        tenant = _make_tenant("tenant_e")
        plan = _make_plan(name="StarterE", max_branches=0)
        _make_subscription(tenant, plan)

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        assert exc_info.value.limit == 0
        assert exc_info.value.current_count == 0


# ---------------------------------------------------------------------------
# Tests — menu_items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckResourceLimitMenuItems:
    """Tests for resource_type='menu_items'."""

    def test_under_limit_does_not_raise(self):
        tenant = _make_tenant("menu_tenant_a")
        plan = _make_plan(name="MenuPlanA", max_menu_items=10)
        _make_subscription(tenant, plan)

        for i in range(5):
            _make_menu_item(f"Item A{i}")

        BillingService.check_resource_limit(tenant, "menu_items")

    def test_at_limit_raises(self):
        tenant = _make_tenant("menu_tenant_b")
        plan = _make_plan(name="MenuPlanB", max_menu_items=3)
        _make_subscription(tenant, plan)

        for i in range(3):
            _make_menu_item(f"Item B{i}")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "menu_items")

        exc = exc_info.value
        assert exc.resource_type == "menu_items"
        assert exc.current_count == 3
        assert exc.limit == 3

    def test_archived_items_not_counted(self):
        """Archived menu items (is_archived=True) must NOT count toward the limit."""
        tenant = _make_tenant("menu_tenant_c")
        plan = _make_plan(name="MenuPlanC", max_menu_items=2)
        _make_subscription(tenant, plan)

        # 2 active items + 5 archived — only 2 should count
        for i in range(2):
            _make_menu_item(f"Active C{i}", is_archived=False)
        for i in range(5):
            _make_menu_item(f"Archived C{i}", is_archived=True)

        # Should not raise: 2 active == limit 2, but we're exactly AT the limit
        with pytest.raises(ResourceLimitExceeded):
            BillingService.check_resource_limit(tenant, "menu_items")

    def test_archived_items_not_counted_under_limit(self):
        """Archived items don't count; 1 active vs limit 2 → no error."""
        tenant = _make_tenant("menu_tenant_d")
        plan = _make_plan(name="MenuPlanD", max_menu_items=2)
        _make_subscription(tenant, plan)

        _make_menu_item("Active D0", is_archived=False)
        for i in range(10):
            _make_menu_item(f"Archived D{i}", is_archived=True)

        # 1 active < limit 2 → should not raise
        BillingService.check_resource_limit(tenant, "menu_items")


# ---------------------------------------------------------------------------
# Tests — staff_accounts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckResourceLimitStaffAccounts:
    """Tests for resource_type='staff_accounts'."""

    def test_under_limit_does_not_raise(self):
        tenant = _make_tenant("staff_tenant_a")
        plan = _make_plan(name="StaffPlanA", max_staff_accounts=5)
        _make_subscription(tenant, plan)

        for i in range(3):
            _make_staff_user(f"staff_a{i}@example.com")

        BillingService.check_resource_limit(tenant, "staff_accounts")

    def test_at_limit_raises(self):
        tenant = _make_tenant("staff_tenant_b")
        plan = _make_plan(name="StaffPlanB", max_staff_accounts=2)
        _make_subscription(tenant, plan)

        for i in range(2):
            _make_staff_user(f"staff_b{i}@example.com")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "staff_accounts")

        exc = exc_info.value
        assert exc.resource_type == "staff_accounts"
        assert exc.current_count == 2
        assert exc.limit == 2

    def test_inactive_users_not_counted(self):
        """Inactive users (is_active=False) must NOT count toward staff_accounts."""
        tenant = _make_tenant("staff_tenant_c")
        plan = _make_plan(name="StaffPlanC", max_staff_accounts=2)
        _make_subscription(tenant, plan)

        # 1 active + 5 inactive
        _make_staff_user("active_c0@example.com", is_active=True)
        for i in range(5):
            _make_staff_user(f"inactive_c{i}@example.com", is_active=False)

        # 1 active < limit 2 → should not raise
        BillingService.check_resource_limit(tenant, "staff_accounts")


# ---------------------------------------------------------------------------
# Tests — no subscription
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckResourceLimitNoSubscription:
    """Tenant with no TenantSubscription must raise for all resource types."""

    def test_no_subscription_raises_for_branches(self):
        tenant = _make_tenant("nosub_tenant_a")
        # No subscription created

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        exc = exc_info.value
        assert exc.limit == -1, "Sentinel -1 signals no subscription"
        assert exc.resource_type == "branches"

    def test_no_subscription_raises_for_menu_items(self):
        tenant = _make_tenant("nosub_tenant_b")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "menu_items")

        assert exc_info.value.limit == -1

    def test_no_subscription_raises_for_staff_accounts(self):
        tenant = _make_tenant("nosub_tenant_c")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "staff_accounts")

        assert exc_info.value.limit == -1

    def test_exception_message_is_descriptive(self):
        tenant = _make_tenant("nosub_tenant_d")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        assert "branches" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests — error details on the exception object
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResourceLimitExceededException:
    """Verify the exception carries the expected attributes."""

    def test_exception_attributes(self):
        tenant = _make_tenant("exc_tenant_a")
        plan = _make_plan(name="ExcPlanA", max_branches=1)
        _make_subscription(tenant, plan)

        _make_branch("Exc A1")

        with pytest.raises(ResourceLimitExceeded) as exc_info:
            BillingService.check_resource_limit(tenant, "branches")

        exc = exc_info.value
        assert exc.resource_type == "branches"
        assert exc.current_count == 1
        assert exc.limit == 1
        assert "1/1" in str(exc)
        assert "branches" in str(exc)

    def test_invalid_resource_type_raises_value_error(self):
        tenant = _make_tenant("exc_tenant_b")
        plan = _make_plan(name="ExcPlanB")
        _make_subscription(tenant, plan)

        with pytest.raises(ValueError, match="Unknown resource_type"):
            BillingService.check_resource_limit(tenant, "invalid_type")
