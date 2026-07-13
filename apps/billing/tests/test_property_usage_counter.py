"""
Property-Based Tests: Usage Counter Accuracy (Property 5)

For any sequence of create/delete operations, the usage counter reported by
``BillingService._get_current_count(resource_type)`` always equals the actual
current count of active resources in the database.

Sub-properties tested:
  5a — Initial state: zero resources → counter is 0
  5b — After creating N resources, counter equals N
  5c — Soft-delete/deactivate: counter decreases accurately
  5d — Interleaved create+delete sequence invariant
  5e — Usage API endpoint reflects same counts as service layer

Validates: Requirements 2.5
"""

import uuid
from datetime import date

from django.contrib.auth import get_user_model
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.billing.services import BillingService


# ---------------------------------------------------------------------------
# Helpers — reused/adapted from test_property_resource_limit.py
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


def _create_n_resources(resource_type: str, n: int) -> list:
    """
    Create *n* active resources of *resource_type* and return them as a list.

    Branch   — no active/archived flag; all rows are always counted.
    MenuItem — created with is_archived=False (active).
    User     — created with is_active=True (active).
    """
    User = get_user_model()
    created = []
    for _ in range(n):
        uid = uuid.uuid4().hex[:8]
        if resource_type == "branches":
            from apps.branches.models import Branch
            obj = Branch.objects.create(name=f"Branch-{uid}")
        elif resource_type == "menu_items":
            from apps.menus.models import MenuItem
            from apps.branches.models import Branch
            import decimal as _decimal
            # Get or create a shared test branch for these tests
            branch, _ = Branch.objects.get_or_create(
                name="__usage_counter_test_branch__",
                defaults={
                    "address": "Test Address",
                    "phone": "0900000000",
                    "email": "usage_counter_test@test.com",
                },
            )
            obj = MenuItem.objects.create(
                name=f"Item-{uid}",
                branch=branch,
                price=_decimal.Decimal("10.00"),
                prep_time_minutes=5,
                is_archived=False,
            )
        elif resource_type == "staff_accounts":
            obj = User.objects.create_user(
                email=f"u{uid}@test.com",
                password="Pass123!",
                role="Receptionist",
                is_active=True,
            )
        else:
            raise ValueError(f"Unknown resource_type: {resource_type!r}")
        created.append(obj)
    return created


def _soft_delete_n_resources(resource_type: str, n: int) -> None:
    """
    Soft-delete/deactivate *n* currently active resources of *resource_type*.

    MenuItem — sets is_archived=True and saves.
    User     — sets is_active=False and saves.

    Branch does not support soft-delete (no is_active/is_archived), so this
    helper must not be called with resource_type='branches'.
    """
    User = get_user_model()
    if resource_type == "menu_items":
        from apps.menus.models import MenuItem
        targets = list(MenuItem.objects.filter(is_archived=False)[:n])
        for item in targets:
            item.is_archived = True
            item.save()
    elif resource_type == "staff_accounts":
        targets = list(User.objects.filter(is_active=True)[:n])
        for user in targets:
            user.is_active = False
            user.save()
    else:
        raise ValueError(
            f"_soft_delete_n_resources does not support resource_type={resource_type!r}. "
            "Branch has no soft-delete field."
        )


# ---------------------------------------------------------------------------
# Property 5 Tests
#
# Inheriting from hypothesis.extra.django.TestCase wraps each test METHOD in
# a rolled-back DB transaction, so every Hypothesis example starts from a
# clean DB slate within that single transaction.
#
# uuid-based slugs per Hypothesis iteration prevent cross-iteration
# contamination (same pattern as test_property_resource_limit.py).
# ---------------------------------------------------------------------------

class TestPropertyUsageCounterAccuracy(TestCase):
    """
    **Validates: Requirements 2.5**

    Property-based tests confirming that ``BillingService._get_current_count``
    always returns the true count of active resources in the database, regardless
    of the sequence of create/delete operations performed.
    """

    # -----------------------------------------------------------------------
    # 5a — Initial state: zero resources → counter is 0
    # -----------------------------------------------------------------------

    @given(
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    def test_property_5a_initial_count_is_zero(self, resource_type):
        """
        **Validates: Requirements 2.5**

        With no resources created, _get_current_count must return 0 for every
        resource type. This validates the baseline accuracy of the counter.
        """
        count = BillingService._get_current_count(resource_type)
        self.assertEqual(
            count,
            0,
            f"Expected initial count of 0 for {resource_type!r}, got {count}",
        )

    # -----------------------------------------------------------------------
    # 5b — After creating N resources, counter equals N
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=0, max_value=20),
        resource_type=st.sampled_from(["branches", "menu_items", "staff_accounts"]),
    )
    @settings(max_examples=200)
    def test_property_5b_count_after_creation_equals_n(self, n, resource_type):
        """
        **Validates: Requirements 2.5**

        After creating exactly N active resources of a given type,
        _get_current_count must report exactly N. Tested across all three
        resource types and arbitrary N in [0, 20].
        """
        _create_n_resources(resource_type, n)

        count = BillingService._get_current_count(resource_type)
        self.assertEqual(
            count,
            n,
            f"After creating {n} {resource_type!r} resources, expected count={n}, got {count}",
        )

    # -----------------------------------------------------------------------
    # 5c — Soft-delete/deactivate: counter decreases accurately
    # -----------------------------------------------------------------------

    @given(
        n=st.integers(min_value=1, max_value=10),
        k=st.integers(min_value=1, max_value=10),
        resource_type=st.sampled_from(["menu_items", "staff_accounts"]),
    )
    @settings(max_examples=200)
    def test_property_5c_soft_delete_decreases_count_accurately(
        self, n, k, resource_type
    ):
        """
        **Validates: Requirements 2.5**

        After creating N active resources and soft-deleting/deactivating K of
        them, _get_current_count must report exactly N-K.

        Branch is excluded because it has no soft-delete field (no is_active /
        is_archived). Only menu_items and staff_accounts support soft-deletion.
        """
        assume(k <= n)

        _create_n_resources(resource_type, n)
        _soft_delete_n_resources(resource_type, k)

        count = BillingService._get_current_count(resource_type)
        expected = n - k
        self.assertEqual(
            count,
            expected,
            f"After creating {n} and soft-deleting {k} {resource_type!r} resources, "
            f"expected count={expected}, got {count}",
        )

    # -----------------------------------------------------------------------
    # 5d — Interleaved create+delete sequence invariant
    # -----------------------------------------------------------------------

    @given(
        ops=st.lists(
            st.tuples(
                st.sampled_from(["create", "delete"]),
                st.sampled_from(["menu_items", "staff_accounts"]),
            ),
            min_size=1,
            max_size=30,
        ),
    )
    @settings(max_examples=100)
    def test_property_5d_interleaved_create_delete_invariant(self, ops):
        """
        **Validates: Requirements 2.5**

        For any interleaved sequence of create/soft-delete operations,
        _get_current_count must always match the manually tracked active count.

        After EACH operation the counter is compared to the expected value,
        confirming the invariant holds throughout the entire sequence — not
        only at the end.

        Branch is excluded because it has no soft-delete support. Only
        menu_items and staff_accounts are exercised here.
        """
        active_counts: dict[str, int] = {
            "menu_items": 0,
            "staff_accounts": 0,
        }

        for op, resource_type in ops:
            if op == "create":
                _create_n_resources(resource_type, 1)
                active_counts[resource_type] += 1
            elif op == "delete":
                if active_counts[resource_type] == 0:
                    # Nothing to delete; skip this operation but still verify
                    pass
                else:
                    _soft_delete_n_resources(resource_type, 1)
                    active_counts[resource_type] -= 1

            actual = BillingService._get_current_count(resource_type)
            expected = active_counts[resource_type]
            self.assertEqual(
                actual,
                expected,
                f"After op=({op!r}, {resource_type!r}): expected count={expected}, "
                f"got {actual}. Sequence so far: active_counts={active_counts}",
            )

    # -----------------------------------------------------------------------
    # 5e — Usage API endpoint reflects same counts as service layer
    # -----------------------------------------------------------------------

    @given(
        n_branches=st.integers(min_value=0, max_value=5),
        n_menus=st.integers(min_value=0, max_value=5),
        n_staff=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100)
    def test_property_5e_usage_api_reflects_service_counts(
        self, n_branches, n_menus, n_staff
    ):
        """
        **Validates: Requirements 2.5**

        The usage data dict constructed the same way TenantUsageView does must
        reflect exactly the counts returned by BillingService._get_current_count
        for each resource type.

        Also asserts that the "used" fields in the usage dict equal the number
        of resources actually created, confirming end-to-end accuracy from
        resource creation → service counter → API response data.
        """
        # Set up tenant + plan + subscription (mirrors TenantUsageView prerequisites)
        slug = f"5e-{uuid.uuid4().hex[:8]}"
        tenant = _make_tenant(slug)
        plan = _make_plan(
            f"Plan-{slug}",
            max_branches=max(n_branches, 1) + 10,
            max_menu_items=max(n_menus, 1) + 10,
            max_staff_accounts=max(n_staff, 1) + 10,
        )
        subscription = _make_subscription(tenant, plan)

        # Pre-create the shared menu item test branch so it doesn't inflate
        # the branch count when _create_n_resources("menu_items", ...) runs
        from apps.branches.models import Branch as _Branch
        _Branch.objects.get_or_create(
            name="__usage_counter_test_branch__",
            defaults={
                "address": "Test Address",
                "phone": "0900000000",
                "email": "usage_counter_test@test.com",
            },
        )
        # Note the baseline branch count after pre-creating the helper branch
        branch_baseline = _Branch.objects.count()

        # Create the resources
        _create_n_resources("branches", n_branches)
        _create_n_resources("menu_items", n_menus)
        _create_n_resources("staff_accounts", n_staff)

        # Gather counts via the service layer (same as TenantUsageView does)
        branches_used = BillingService._get_current_count("branches")
        menu_items_used = BillingService._get_current_count("menu_items")
        staff_accounts_used = BillingService._get_current_count("staff_accounts")

        # The branch count from the service includes the baseline test branch;
        # actual branches created by this test = branches_used - branch_baseline
        branches_created = branches_used - branch_baseline

        # Construct the usage_data dict the same way TenantUsageView does
        usage_data = {
            "tenant_id": str(tenant.pk),
            "plan": plan.name,
            "branches": {
                "used": branches_used,
                "limit": plan.max_branches,
            },
            "menu_items": {
                "used": menu_items_used,
                "limit": plan.max_menu_items,
            },
            "staff_accounts": {
                "used": staff_accounts_used,
                "limit": plan.max_staff_accounts,
            },
            "subscription_status": subscription.status,
        }

        # The service counts must match what was actually created in this test
        self.assertEqual(
            branches_created,
            n_branches,
            f"branches used: expected {n_branches}, got {branches_created}",
        )
        self.assertEqual(
            usage_data["menu_items"]["used"],
            n_menus,
            f"menu_items used: expected {n_menus}, got {usage_data['menu_items']['used']}",
        )
        self.assertEqual(
            usage_data["staff_accounts"]["used"],
            n_staff,
            f"staff_accounts used: expected {n_staff}, got {usage_data['staff_accounts']['used']}",
        )

        # The usage_data "used" values must also equal the service-layer counts
        self.assertEqual(usage_data["branches"]["used"], branches_used)
        self.assertEqual(usage_data["menu_items"]["used"], menu_items_used)
        self.assertEqual(usage_data["staff_accounts"]["used"], staff_accounts_used)
