"""
Property-Based Tests: Archived Items Hidden from Customers (Property 21)

Property 21: Archived Items Hidden from Customers

  For any archived MenuItem, the customer-facing menu API for the containing
  branch shall NOT include that item in its response, while historical order
  records referencing that item shall retain the association intact.

Sub-properties tested:
  21a — An archived item never appears in the customer menu queryset,
        regardless of what status it had before archiving
  21b — A non-archived available item IS visible, then disappears immediately
        after being archived
  21c — Archiving a MenuItem does NOT delete its DB record; all field values
        (price, name, description) are preserved for historical order lookups
  21d — When a branch has a mix of archived and non-archived items, only the
        non-archived available ones appear in the customer menu
  21e — Archiving is idempotent: archiving an already-archived item still hides it

Strategy:
  The customer-facing menu queryset is:
      MenuItem.objects.filter(branch=branch, status='available', is_archived=False)
  This mirrors the filter that CustomerMenuView (Task 16) applies
  (Requirement 14.11 — only 'available', non-archived items are shown).

  Because Order/OrderItem models are not yet implemented (Task 11), the
  "historical order association remains intact" guarantee is validated at the
  data-model level: after archiving, the MenuItem record must still exist in
  the DB with all its original field values intact.  A simulated order
  association — stored as an in-memory dict mapping order_id → menu_item_id —
  is resolved against the DB to confirm the association can always be
  reconstructed from the persisted record.

  No external dependencies (Celery, WebSocket, R2) are exercised; tests run
  against Django's in-memory database and LocMemCache.

Validates: Requirements 9.3
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.branches.models import Branch
from apps.menus.models import DIETARY_TAGS, MenuItem
from apps.menus.views import _invalidate_branch_menu_cache

# ---------------------------------------------------------------------------
# Hypothesis strategies (aligned with Property 19 / 20 tests)
# ---------------------------------------------------------------------------

_price_st = st.decimals(
    min_value="0.01",
    max_value="9999.99",
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

_prep_time_st = st.integers(min_value=1, max_value=32767)

_name_st = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters=" -",
    ),
).map(str.strip).filter(bool)

_description_st = st.text(
    min_size=0,
    max_size=300,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po", "Pd"),
        whitelist_characters=" -.,!?",
    ),
)

# Statuses that are valid before an item is archived
_pre_archive_status_st = st.sampled_from(["available", "unavailable", "seasonal"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q2(value) -> Decimal:
    """Quantize a Decimal (or numeric string) to 2 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _customer_menu_qs(branch: Branch):
    """
    Return the canonical customer-facing menu queryset for *branch*.

    Mirrors the filter applied by CustomerMenuView (Task 16):
        status='available' AND is_archived=False

    Requirement 14.11: only 'available' items with is_archived=False are shown
    to customers.
    """
    return MenuItem.objects.filter(
        branch=branch,
        status="available",
        is_archived=False,
    )


def _archive_item(item: MenuItem) -> None:
    """
    Archive a MenuItem: set is_archived=True and status='archived'.

    Mirrors the behaviour of MenuItemViewSet.archive() (Task 10.7) and the
    PATCH partial_update path when is_archived is set to True.
    """
    item.is_archived = True
    item.status = "archived"
    item.save(update_fields=["is_archived", "status", "updated_at"])


def _reset_branch_state(branch: Branch) -> None:
    """
    Delete all MenuItems for *branch* and flush the cache.

    Called at the start of each @given example so each Hypothesis iteration
    starts from a clean slate.
    """
    MenuItem.objects.filter(branch=branch).delete()
    cache.clear()


# ---------------------------------------------------------------------------
# Property 21 Test Class
# ---------------------------------------------------------------------------


class TestPropertyArchivedItemsHiddenFromCustomers(TestCase):
    """
    Property 21: Archived Items Hidden from Customers

    For any archived MenuItem, the customer-facing menu queryset for the
    containing branch shall NOT include that item, while the DB record
    remains intact for historical order lookups.

    Validates: Requirements 9.3
    """

    def setUp(self):
        """Create a shared Branch for all property iterations."""
        self.branch = Branch.objects.create(
            name="Property 21 Test Branch",
            address="21 Archive Lane, Addis Ababa",
            phone="0911000021",
            email="prop21@restaurant.com",
        )

    # -----------------------------------------------------------------------
    # 21a — Archived item never appears in customer menu queryset
    # -----------------------------------------------------------------------

    @given(
        pre_archive_status=_pre_archive_status_st,
        price=_price_st,
        prep_time=_prep_time_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_property_21a_archived_item_never_in_customer_menu(
        self,
        pre_archive_status,
        price,
        prep_time,
    ):
        """
        **Validates: Requirements 9.3**

        Sub-property 21a: For any MenuItem that was previously available,
        unavailable, or seasonal, once it is archived it MUST NOT appear in
        the customer menu queryset.

        For each generated (pre_archive_status, price, prep_time) triple:
          1. Create a MenuItem with the given status (is_archived=False).
          2. Archive it.
          3. Invalidate the cache.
          4. Assert the item is absent from the customer menu queryset.
        """
        _reset_branch_state(self.branch)

        unique_name = f"Archived21a-{uuid.uuid4().hex[:8]}"
        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(price),
            prep_time_minutes=prep_time,
            status=pre_archive_status,
            is_archived=False,
        )

        # Archive the item (mirrors MenuItemViewSet.archive)
        _archive_item(item)
        _invalidate_branch_menu_cache(str(self.branch.id))

        customer_ids = set(
            _customer_menu_qs(self.branch).values_list("id", flat=True)
        )

        self.assertNotIn(
            item.id,
            customer_ids,
            msg=(
                f"Property 21a FAILED: archived MenuItem (id={item.id}, "
                f"pre_archive_status={pre_archive_status!r}) appeared in the "
                f"customer menu queryset. Archived items must NEVER be visible "
                f"to customers (Requirement 9.3)."
            ),
        )

    # -----------------------------------------------------------------------
    # 21b — Non-archived available item is visible, disappears after archiving
    # -----------------------------------------------------------------------

    @given(
        price=_price_st,
        prep_time=_prep_time_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_property_21b_available_item_visible_then_hidden_after_archive(
        self,
        price,
        prep_time,
    ):
        """
        **Validates: Requirements 9.3**

        Sub-property 21b: A non-archived, available item MUST appear in the
        customer menu queryset BEFORE archiving, and MUST NOT appear AFTER
        archiving.

        This validates the before/after contrast: the item's visibility change
        is caused exclusively by the archive operation, and the change takes
        effect immediately (no caching delay).
        """
        _reset_branch_state(self.branch)

        unique_name = f"BeforeAfter21b-{uuid.uuid4().hex[:8]}"
        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(price),
            prep_time_minutes=prep_time,
            status="available",
            is_archived=False,
        )

        # --- BEFORE archiving: item must be visible ---
        customer_ids_before = set(
            _customer_menu_qs(self.branch).values_list("id", flat=True)
        )
        self.assertIn(
            item.id,
            customer_ids_before,
            msg=(
                f"Property 21b setup: non-archived available item {item.id} "
                f"must appear in customer menu before archiving."
            ),
        )

        # --- Archive and invalidate cache ---
        _archive_item(item)
        _invalidate_branch_menu_cache(str(self.branch.id))

        # --- AFTER archiving: item must be hidden ---
        customer_ids_after = set(
            _customer_menu_qs(self.branch).values_list("id", flat=True)
        )
        self.assertNotIn(
            item.id,
            customer_ids_after,
            msg=(
                f"Property 21b FAILED: item {item.id} still appears in "
                f"customer menu after archiving. Archive must hide item "
                f"immediately (Requirement 9.3)."
            ),
        )

    # -----------------------------------------------------------------------
    # 21c — DB record persists after archiving (historical order integrity)
    # -----------------------------------------------------------------------

    @given(
        price=_price_st,
        prep_time=_prep_time_st,
        name=_name_st,
        description=_description_st,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_property_21c_archived_record_persists_for_historical_orders(
        self,
        price,
        prep_time,
        name,
        description,
    ):
        """
        **Validates: Requirements 9.3**

        Sub-property 21c: Archiving a MenuItem does NOT delete its database
        record. All field values (name, price, description) are preserved
        after archiving, so that historical order records referencing the
        item via its primary key retain a valid, readable association.

        Because Order/OrderItem models are not yet implemented (Task 11),
        the "historical order association" is simulated as an in-memory dict:
          simulated_order = {'order_id': uuid4(), 'menu_item_id': item.pk, 'unit_price': item.price}
        After archiving, the test resolves the stored menu_item_id against the
        DB and verifies the record is still there with its original field values.
        """
        _reset_branch_state(self.branch)

        unique_name = f"{name[:91]}-{uuid.uuid4().hex[:8]}"
        expected_price = _q2(price)

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            description=description,
            price=expected_price,
            prep_time_minutes=prep_time,
            status="available",
            is_archived=False,
        )

        # Simulate placing an order that references this item (pre-archive)
        simulated_order = {
            "order_id": str(uuid.uuid4()),
            "menu_item_id": item.pk,
            "unit_price": str(expected_price),  # price snapshot at placement
            "item_name": item.name,
        }

        # Archive the item
        _archive_item(item)
        _invalidate_branch_menu_cache(str(self.branch.id))

        # --- The item must still exist in the DB ---
        try:
            archived_item = MenuItem.objects.get(pk=simulated_order["menu_item_id"])
        except MenuItem.DoesNotExist:
            self.fail(
                f"Property 21c FAILED: MenuItem (id={item.pk}) was deleted "
                f"from the DB after archiving. Archiving must be a soft "
                f"operation — the record must be preserved for historical "
                f"order lookups (Requirement 9.3)."
            )

        # --- The archived record must retain original field values ---
        self.assertEqual(
            archived_item.name,
            unique_name,
            msg=(
                f"Property 21c FAILED: archived item name changed. "
                f"Expected {unique_name!r}, got {archived_item.name!r}. "
                f"Historical order records require stable field values."
            ),
        )
        self.assertEqual(
            archived_item.price,
            expected_price,
            msg=(
                f"Property 21c FAILED: archived item price changed. "
                f"Expected {expected_price}, got {archived_item.price}. "
                f"Historical order records require the original price snapshot."
            ),
        )
        self.assertEqual(
            archived_item.description,
            description,
            msg=(
                f"Property 21c FAILED: archived item description changed. "
                f"Expected {description!r}, got {archived_item.description!r}."
            ),
        )

        # --- The simulated order association is still resolvable ---
        self.assertEqual(
            archived_item.pk,
            simulated_order["menu_item_id"],
            msg=(
                f"Property 21c FAILED: simulated order's menu_item_id "
                f"{simulated_order['menu_item_id']} does not match the "
                f"archived record's pk {archived_item.pk}."
            ),
        )

        # --- The archived flag is set correctly ---
        self.assertTrue(
            archived_item.is_archived,
            msg=(
                f"Property 21c: is_archived must be True after archiving "
                f"(item id={item.pk})."
            ),
        )
        self.assertEqual(
            archived_item.status,
            "archived",
            msg=(
                f"Property 21c: status must be 'archived' after archiving "
                f"(item id={item.pk})."
            ),
        )

    # -----------------------------------------------------------------------
    # 21d — Mixed branch: only non-archived available items appear
    # -----------------------------------------------------------------------

    @given(
        n_available=st.integers(min_value=1, max_value=5),
        n_to_archive=st.integers(min_value=1, max_value=5),
        base_price=_price_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_21d_only_non_archived_items_in_customer_menu(
        self,
        n_available,
        n_to_archive,
        base_price,
    ):
        """
        **Validates: Requirements 9.3**

        Sub-property 21d: When a branch has a mix of archived and non-archived
        available MenuItems, the customer menu queryset returns EXACTLY the
        non-archived available ones — no archived item may appear.

        For any n_available (1–5) non-archived items and n_to_archive (1–5)
        archived items created on the same branch:
          - Customer menu count == n_available
          - No archived item's ID appears in the customer menu queryset
          - All non-archived item IDs appear in the customer menu queryset
        """
        _reset_branch_state(self.branch)

        q_price = _q2(base_price)

        # Create the non-archived available items
        available_ids = set()
        for i in range(n_available):
            item = MenuItem.objects.create(
                branch=self.branch,
                name=f"Available21d-{i}-{uuid.uuid4().hex[:8]}",
                price=q_price,
                prep_time_minutes=10,
                status="available",
                is_archived=False,
            )
            available_ids.add(item.id)

        # Create items that will be archived
        archived_ids = set()
        for i in range(n_to_archive):
            item = MenuItem.objects.create(
                branch=self.branch,
                name=f"ToArchive21d-{i}-{uuid.uuid4().hex[:8]}",
                price=q_price,
                prep_time_minutes=10,
                status="available",
                is_archived=False,
            )
            _archive_item(item)
            archived_ids.add(item.id)

        _invalidate_branch_menu_cache(str(self.branch.id))

        customer_ids = set(
            _customer_menu_qs(self.branch).values_list("id", flat=True)
        )

        # Exactly n_available items in customer menu
        self.assertEqual(
            len(customer_ids),
            n_available,
            msg=(
                f"Property 21d FAILED: expected {n_available} items in customer "
                f"menu, found {len(customer_ids)}. Only non-archived available "
                f"items should appear (Requirement 9.3)."
            ),
        )

        # All non-archived available items are present
        for aid in available_ids:
            self.assertIn(
                aid,
                customer_ids,
                msg=(
                    f"Property 21d FAILED: non-archived available item {aid} "
                    f"is missing from customer menu queryset."
                ),
            )

        # No archived item is present
        for aid in archived_ids:
            self.assertNotIn(
                aid,
                customer_ids,
                msg=(
                    f"Property 21d FAILED: archived item {aid} appeared in "
                    f"customer menu queryset (Requirement 9.3)."
                ),
            )

    # -----------------------------------------------------------------------
    # 21e — Archiving is idempotent
    # -----------------------------------------------------------------------

    @given(price=_price_st, prep_time=_prep_time_st)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_21e_archiving_is_idempotent(self, price, prep_time):
        """
        **Validates: Requirements 9.3**

        Sub-property 21e: Archiving an already-archived MenuItem is a
        no-op that produces the same result: the item remains hidden from
        the customer menu queryset and its DB record remains intact.

        This validates that the archive operation is safe to call multiple
        times (e.g. due to retries) without corrupting state.
        """
        _reset_branch_state(self.branch)

        unique_name = f"Idempotent21e-{uuid.uuid4().hex[:8]}"
        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(price),
            prep_time_minutes=prep_time,
            status="available",
            is_archived=False,
        )

        # First archive
        _archive_item(item)
        _invalidate_branch_menu_cache(str(self.branch.id))

        # Second archive (idempotent call)
        _archive_item(item)
        _invalidate_branch_menu_cache(str(self.branch.id))

        # Item must still not appear in customer menu
        customer_ids = set(
            _customer_menu_qs(self.branch).values_list("id", flat=True)
        )
        self.assertNotIn(
            item.id,
            customer_ids,
            msg=(
                f"Property 21e FAILED: item {item.id} appeared in customer "
                f"menu after being archived twice. Archive must be idempotent "
                f"(Requirement 9.3)."
            ),
        )

        # DB record must still be present
        try:
            persisted = MenuItem.objects.get(pk=item.pk)
        except MenuItem.DoesNotExist:
            self.fail(
                f"Property 21e FAILED: MenuItem {item.pk} was deleted after "
                f"double-archiving. DB record must always be preserved."
            )

        self.assertTrue(persisted.is_archived)
        self.assertEqual(persisted.status, "archived")
