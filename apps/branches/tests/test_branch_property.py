"""
Property-Based Tests: Branch Data Scope Isolation (Property 18)

For any Branch created under a tenant, all operational data records (orders,
inventory items, expenses, income records) created with that branch assigned
shall be retrievable only when queried with the correct branch filter; queries
scoped to a different branch shall not return those records.

Sub-properties tested:
  18a — MenuItem created under Branch A is not returned when filtering by Branch B
  18b — InventoryItem created under Branch A is not returned when filtering by Branch B
  18c — Isolation is symmetric: data from B is also invisible under A's filter
  18d — Multiple records under A remain fully isolated from B (none leak)
  18e — Records under both branches coexist without cross-contamination

Validates: Requirements 8.2, 8.3
"""

import decimal
import uuid

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.branches.models import Branch


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate positive decimal prices (1.00 – 999.99)
price_strategy = st.decimals(
    min_value=decimal.Decimal("1.00"),
    max_value=decimal.Decimal("999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Generate small positive quantities for inventory
quantity_strategy = st.decimals(
    min_value=decimal.Decimal("0.0001"),
    max_value=decimal.Decimal("9999.9999"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

# Generate reorder thresholds (lower than quantity range)
threshold_strategy = st.decimals(
    min_value=decimal.Decimal("0.0001"),
    max_value=decimal.Decimal("100.0000"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

# Short name strings (avoid empty or overlong values)
name_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=" -_",
    ),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip())  # ensure non-blank after stripping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_branch(suffix: str = "") -> Branch:
    """Create and return a Branch with unique identifying fields."""
    uid = uuid.uuid4().hex[:8]
    return Branch.objects.create(
        name=f"Branch-{uid}{suffix}",
        address=f"{uid} Test Street",
        phone=f"09{uid[:8]}",
        email=f"branch_{uid}@test.com",
    )


def _make_menu_item(branch: Branch, price: decimal.Decimal, name: str) -> object:
    """Create a MenuItem scoped to `branch`."""
    from apps.menus.models import MenuItem
    uid = uuid.uuid4().hex[:6]
    # Sanitize name: ensure non-empty, max_length=200
    safe_name = (name.strip()[:190] or "Item") + f"-{uid}"
    return MenuItem.objects.create(
        branch=branch,
        name=safe_name,
        price=price,
        prep_time_minutes=5,
        status="available",
    )


def _make_inventory_item(
    branch: Branch,
    quantity: decimal.Decimal,
    threshold: decimal.Decimal,
    name: str,
) -> object:
    """Create an InventoryItem scoped to `branch`."""
    from apps.inventory.models import InventoryItem
    uid = uuid.uuid4().hex[:6]
    safe_name = (name.strip()[:190] or "Ingredient") + f"-{uid}"
    return InventoryItem.objects.create(
        branch=branch,
        name=safe_name,
        category="General",
        quantity=quantity,
        unit="kg",
        purchase_price=decimal.Decimal("5.00"),
        reorder_threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Property 18 Tests
#
# Using hypothesis.extra.django.TestCase wraps each test METHOD in a rolled-
# back transaction so each Hypothesis example starts from a clean DB state.
# This matches the pattern used in test_property_resource_limit.py and
# test_property_usage_counter.py.
# ---------------------------------------------------------------------------


class TestBranchDataScopeIsolationProperty(TestCase):
    """
    **Validates: Requirements 8.2, 8.3**

    Property-based tests confirming that operational data created under
    Branch A is never returned when filtering by Branch B, and vice versa.
    """

    # -----------------------------------------------------------------------
    # Property 18a — MenuItem scoped to Branch A is invisible under Branch B filter
    # -----------------------------------------------------------------------

    @given(
        price=price_strategy,
        item_name=name_strategy,
    )
    @settings(max_examples=50)
    def test_property_18a_menu_item_scoped_to_branch_a_not_visible_in_branch_b(
        self, price, item_name
    ):
        """
        **Validates: Requirements 8.2, 8.3**

        A MenuItem created with branch=A must not appear in a queryset filtered
        by branch=B.  The cross-branch query must return an empty result set.
        """
        from apps.menus.models import MenuItem

        branch_a = _make_branch("-A")
        branch_b = _make_branch("-B")

        # Create a MenuItem scoped exclusively to Branch A
        _make_menu_item(branch_a, price, item_name)

        # Query using Branch B's filter — must return zero results
        items_in_b = MenuItem.objects.filter(branch=branch_b)
        self.assertEqual(
            items_in_b.count(),
            0,
            f"Branch scope isolation violated for MenuItem: "
            f"expected 0 results when filtering by Branch B (id={branch_b.id}), "
            f"but got {items_in_b.count()} item(s) created under Branch A "
            f"(id={branch_a.id}).",
        )
        self.assertFalse(
            items_in_b.exists(),
            f"MenuItem.objects.filter(branch=branch_b).exists() returned True "
            f"despite no items being created under Branch B — cross-branch "
            f"data leakage detected (Requirements 8.2, 8.3).",
        )

    # -----------------------------------------------------------------------
    # Property 18b — InventoryItem scoped to Branch A is invisible under Branch B filter
    # -----------------------------------------------------------------------

    @given(
        quantity=quantity_strategy,
        threshold=threshold_strategy,
        item_name=name_strategy,
    )
    @settings(max_examples=50)
    def test_property_18b_inventory_item_scoped_to_branch_a_not_visible_in_branch_b(
        self, quantity, threshold, item_name
    ):
        """
        **Validates: Requirements 8.2, 8.3**

        An InventoryItem created with branch=A must not appear in a queryset
        filtered by branch=B.
        """
        from apps.inventory.models import InventoryItem

        branch_a = _make_branch("-A")
        branch_b = _make_branch("-B")

        # Create an InventoryItem scoped exclusively to Branch A
        _make_inventory_item(branch_a, quantity, threshold, item_name)

        # Query using Branch B's filter — must return zero results
        inv_in_b = InventoryItem.objects.filter(branch=branch_b)
        self.assertEqual(
            inv_in_b.count(),
            0,
            f"Branch scope isolation violated for InventoryItem: "
            f"expected 0 results when filtering by Branch B (id={branch_b.id}), "
            f"but got {inv_in_b.count()} item(s) created under Branch A "
            f"(id={branch_a.id}).",
        )
        self.assertFalse(
            inv_in_b.exists(),
            f"InventoryItem.objects.filter(branch=branch_b).exists() returned True "
            f"despite no items being created under Branch B — cross-branch "
            f"data leakage detected (Requirements 8.2, 8.3).",
        )

    # -----------------------------------------------------------------------
    # Property 18c — Isolation is symmetric (B's data is also invisible from A)
    # -----------------------------------------------------------------------

    @given(
        price_a=price_strategy,
        price_b=price_strategy,
        name_a=name_strategy,
        name_b=name_strategy,
    )
    @settings(max_examples=50)
    def test_property_18c_isolation_is_symmetric_menu_items(
        self, price_a, price_b, name_a, name_b
    ):
        """
        **Validates: Requirements 8.2, 8.3**

        Isolation holds in both directions:
        - MenuItem created under Branch A is invisible when filtering by Branch B.
        - MenuItem created under Branch B is invisible when filtering by Branch A.

        Both branches can coexist with their own data; neither sees the other's records.
        """
        from apps.menus.models import MenuItem

        branch_a = _make_branch("-A")
        branch_b = _make_branch("-B")

        # Create one MenuItem per branch
        item_a = _make_menu_item(branch_a, price_a, name_a)
        item_b = _make_menu_item(branch_b, price_b, name_b)

        # Direction A → B: A's item must not appear in B's filter
        items_via_b_filter = MenuItem.objects.filter(branch=branch_b)
        a_in_b = items_via_b_filter.filter(id=item_a.id)
        self.assertEqual(
            a_in_b.count(),
            0,
            f"Symmetry violation (A→B): Branch A's MenuItem (id={item_a.id}) "
            f"appeared in Branch B's filtered queryset.",
        )

        # Direction B → A: B's item must not appear in A's filter
        items_via_a_filter = MenuItem.objects.filter(branch=branch_a)
        b_in_a = items_via_a_filter.filter(id=item_b.id)
        self.assertEqual(
            b_in_a.count(),
            0,
            f"Symmetry violation (B→A): Branch B's MenuItem (id={item_b.id}) "
            f"appeared in Branch A's filtered queryset.",
        )

        # Each branch sees exactly its own record
        self.assertEqual(
            items_via_a_filter.count(),
            1,
            f"Branch A filter should return exactly 1 MenuItem (its own), "
            f"got {items_via_a_filter.count()}.",
        )
        self.assertEqual(
            items_via_b_filter.count(),
            1,
            f"Branch B filter should return exactly 1 MenuItem (its own), "
            f"got {items_via_b_filter.count()}.",
        )

    # -----------------------------------------------------------------------
    # Property 18d — Multiple records under A are all isolated from B (none leak)
    # -----------------------------------------------------------------------

    @given(
        count=st.integers(min_value=2, max_value=10),
        price=price_strategy,
    )
    @settings(max_examples=50)
    def test_property_18d_multiple_records_under_branch_a_none_visible_in_branch_b(
        self, count, price
    ):
        """
        **Validates: Requirements 8.2, 8.3**

        When multiple MenuItems and InventoryItems are created under Branch A,
        not a single one of them should be visible when filtering by Branch B.
        The result set for Branch B must always be empty.
        """
        from apps.menus.models import MenuItem
        from apps.inventory.models import InventoryItem

        branch_a = _make_branch("-A")
        branch_b = _make_branch("-B")

        # Create `count` MenuItems under Branch A
        for i in range(count):
            _make_menu_item(branch_a, price, f"Item{i}")

        # Create `count` InventoryItems under Branch A
        for i in range(count):
            _make_inventory_item(
                branch_a,
                decimal.Decimal("10.0000"),
                decimal.Decimal("2.0000"),
                f"Ingredient{i}",
            )

        # Query Branch B — both querysets must be empty
        menu_in_b = MenuItem.objects.filter(branch=branch_b)
        inv_in_b = InventoryItem.objects.filter(branch=branch_b)

        self.assertEqual(
            menu_in_b.count(),
            0,
            f"Branch scope isolation violated: {menu_in_b.count()} of {count} "
            f"MenuItems from Branch A leaked into Branch B's queryset.",
        )
        self.assertEqual(
            inv_in_b.count(),
            0,
            f"Branch scope isolation violated: {inv_in_b.count()} of {count} "
            f"InventoryItems from Branch A leaked into Branch B's queryset.",
        )

    # -----------------------------------------------------------------------
    # Property 18e — Records under both branches coexist without cross-contamination
    # -----------------------------------------------------------------------

    @given(
        count_a=st.integers(min_value=1, max_value=8),
        count_b=st.integers(min_value=1, max_value=8),
        price=price_strategy,
        quantity=quantity_strategy,
        threshold=threshold_strategy,
    )
    @settings(max_examples=50)
    def test_property_18e_coexisting_branch_data_remains_isolated(
        self, count_a, count_b, price, quantity, threshold
    ):
        """
        **Validates: Requirements 8.2, 8.3**

        When both Branch A and Branch B each have their own set of operational
        records, filtering by a given branch returns exactly that branch's
        records and nothing from the other branch.

        Asserts:
        - filter(branch=A).count() == count_a  (own records visible)
        - filter(branch=B).count() == count_b  (own records visible)
        - No record from A appears in B's filtered result
        - No record from B appears in A's filtered result
        """
        from apps.menus.models import MenuItem
        from apps.inventory.models import InventoryItem

        branch_a = _make_branch("-A")
        branch_b = _make_branch("-B")

        # Create records under each branch
        items_a = [_make_menu_item(branch_a, price, f"MenuA{i}") for i in range(count_a)]
        items_b = [_make_menu_item(branch_b, price, f"MenuB{i}") for i in range(count_b)]
        inv_a = [
            _make_inventory_item(branch_a, quantity, threshold, f"InvA{i}")
            for i in range(count_a)
        ]
        inv_b = [
            _make_inventory_item(branch_b, quantity, threshold, f"InvB{i}")
            for i in range(count_b)
        ]

        # --- Branch A filter ---
        menu_via_a = MenuItem.objects.filter(branch=branch_a)
        inv_via_a = InventoryItem.objects.filter(branch=branch_a)

        self.assertEqual(
            menu_via_a.count(),
            count_a,
            f"Expected {count_a} MenuItems for Branch A, got {menu_via_a.count()}.",
        )
        self.assertEqual(
            inv_via_a.count(),
            count_a,
            f"Expected {count_a} InventoryItems for Branch A, got {inv_via_a.count()}.",
        )

        # No record from B must appear in A's view
        ids_a_menu = set(menu_via_a.values_list("id", flat=True))
        ids_b_menu = {item.id for item in items_b}
        leaked_menu = ids_a_menu & ids_b_menu
        self.assertEqual(
            len(leaked_menu),
            0,
            f"Cross-branch leakage detected in MenuItem: Branch A's view contains "
            f"{len(leaked_menu)} record(s) belonging to Branch B: {leaked_menu}.",
        )

        ids_a_inv = set(inv_via_a.values_list("id", flat=True))
        ids_b_inv = {item.id for item in inv_b}
        leaked_inv = ids_a_inv & ids_b_inv
        self.assertEqual(
            len(leaked_inv),
            0,
            f"Cross-branch leakage detected in InventoryItem: Branch A's view "
            f"contains {len(leaked_inv)} record(s) belonging to Branch B: {leaked_inv}.",
        )

        # --- Branch B filter ---
        menu_via_b = MenuItem.objects.filter(branch=branch_b)
        inv_via_b = InventoryItem.objects.filter(branch=branch_b)

        self.assertEqual(
            menu_via_b.count(),
            count_b,
            f"Expected {count_b} MenuItems for Branch B, got {menu_via_b.count()}.",
        )
        self.assertEqual(
            inv_via_b.count(),
            count_b,
            f"Expected {count_b} InventoryItems for Branch B, got {inv_via_b.count()}.",
        )

        # No record from A must appear in B's view
        ids_b_menu_actual = set(menu_via_b.values_list("id", flat=True))
        ids_a_menu_orig = {item.id for item in items_a}
        leaked_menu_b = ids_b_menu_actual & ids_a_menu_orig
        self.assertEqual(
            len(leaked_menu_b),
            0,
            f"Cross-branch leakage detected in MenuItem: Branch B's view contains "
            f"{len(leaked_menu_b)} record(s) belonging to Branch A: {leaked_menu_b}.",
        )

        ids_b_inv_actual = set(inv_via_b.values_list("id", flat=True))
        ids_a_inv_orig = {item.id for item in inv_a}
        leaked_inv_b = ids_b_inv_actual & ids_a_inv_orig
        self.assertEqual(
            len(leaked_inv_b),
            0,
            f"Cross-branch leakage detected in InventoryItem: Branch B's view "
            f"contains {len(leaked_inv_b)} record(s) belonging to Branch A: {leaked_inv_b}.",
        )
