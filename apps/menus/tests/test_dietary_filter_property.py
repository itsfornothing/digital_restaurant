"""
Property-Based Tests: Dietary Filter Correctness (Property 28)

# Feature: restaurant-platform, Property 28: Dietary Filter Correctness

Property 28: Dietary Filter Correctness

  For any combination of dietary filters, every item in the filtered result
  possesses ALL selected tags; no item lacking any selected tag appears in
  the results.

Sub-properties tested:
  28a — Every item returned by a single-tag filter possesses that tag
  28b — Every item returned by a multi-tag filter possesses ALL requested tags
  28c — No item lacking even one required tag appears in the filtered results
  28d — Empty filter (no ?dietary_tags param) returns all available items
        regardless of their dietary_tags
  28e — Items with status != 'available' or is_archived=True never appear,
        regardless of matching dietary tags
  28f — The filtered result is the complete set: no qualifying item is
        incorrectly excluded (no false negatives)

Strategy:
  Tests exercise the real ``CustomerMenuView`` (GET /api/v1/customer/menu/)
  through the DRF test client with a live customer session, mirroring how
  the production endpoint is called.

  For each Hypothesis example:
    1. Delete all MenuItems for the test branch (clean slate per iteration).
    2. Generate N MenuItems with random dietary_tags drawn from the valid
       DIETARY_TAGS list.
    3. Generate a random non-empty subset of DIETARY_TAGS as the filter.
    4. Call GET /api/v1/customer/menu/?dietary_tags=<comma-joined filter>.
    5. Assert that:
       - Every returned item has ALL filter tags in its dietary_tags list.
       - No item lacking any filter tag is present in the response.
       - All qualifying DB items (status=available, not archived, with all
         filter tags) ARE present in the response (completeness).

  Session setup mirrors the real QR scan flow: a Branch + Table + active
  QRCode are created in setUp; each iteration calls POST /api/v1/customer/session/
  to establish the session cookie, then calls GET /api/v1/customer/menu/.

  No mocking — tests run against the real Django test database (SQLite in-memory),
  the real in-process session backend, and the real CustomerMenuView filter logic.

Validates: Requirements 14.6
"""

import uuid

import pytest
from django.contrib.sessions.backends.db import SessionStore
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.menus.models import DIETARY_TAGS, MenuItem
from apps.qr.models import QRCode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_URL = "/api/v1/customer/session/"
MENU_URL    = "/api/v1/customer/menu/"

# All valid dietary tag keys (lowercase internal values)
_ALL_TAGS = DIETARY_TAGS  # e.g. ['vegetarian', 'vegan', 'gluten_free', ...]


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# A random non-empty subset of the 13 valid dietary tags (filter selection)
_filter_tags_st = st.lists(
    st.sampled_from(_ALL_TAGS),
    min_size=1,
    max_size=len(_ALL_TAGS),
    unique=True,
)

# A random subset of tags to assign to a single MenuItem (can be empty)
_item_tags_st = st.lists(
    st.sampled_from(_ALL_TAGS),
    min_size=0,
    max_size=len(_ALL_TAGS),
    unique=True,
)

# Random item status: only 'available' items may appear in customer menu
_item_status_st = st.sampled_from(["available", "unavailable", "seasonal"])

# Random number of menu items to generate per iteration
_n_items_st = st.integers(min_value=1, max_value=10)

# ---------------------------------------------------------------------------
# Helper strategy: list of (dietary_tags, status, is_archived) tuples
# ---------------------------------------------------------------------------

_item_spec_st = st.tuples(
    _item_tags_st,       # dietary_tags
    _item_status_st,     # status
    st.booleans(),       # is_archived
)

_items_list_st = st.lists(_item_spec_st, min_size=1, max_size=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_branch(branch: Branch) -> None:
    """Delete all MenuItems for *branch* to start each iteration clean."""
    MenuItem.objects.filter(branch=branch).delete()


def _create_menu_item(branch: Branch, dietary_tags: list, status: str, is_archived: bool, idx: int) -> MenuItem:
    """Create a MenuItem with the given dietary_tags, status and archived flag."""
    return MenuItem.objects.create(
        branch=branch,
        name=f"Item-{idx}-{uuid.uuid4().hex[:8]}",
        price="100.00",
        prep_time_minutes=15,
        status=status,
        is_archived=is_archived,
        dietary_tags=dietary_tags,
    )


def _establish_customer_session(client: APIClient, qr_token: uuid.UUID) -> bool:
    """
    POST /api/v1/customer/session/ to establish a customer session.
    Returns True on success (200), False otherwise.
    """
    resp = client.post(SESSION_URL, {"token": str(qr_token)}, format="json")
    return resp.status_code == 200


def _get_menu(client: APIClient, filter_tags: list | None = None) -> list:
    """
    GET /api/v1/customer/menu/ with an optional dietary_tags filter.
    Returns the parsed list of menu item dicts.
    """
    url = MENU_URL
    if filter_tags:
        url = MENU_URL + "?dietary_tags=" + ",".join(filter_tags)
    resp = client.get(url)
    assert resp.status_code == 200, (
        f"GET {url} returned {resp.status_code}: {getattr(resp, 'data', resp.content)}"
    )
    return list(resp.data)


def _items_that_should_appear(items: list[MenuItem], filter_tags: list[str]) -> set:
    """
    Return the set of item IDs that satisfy ALL filter constraints AND the
    base visibility constraints (status=available, is_archived=False).
    """
    return {
        str(item.id)
        for item in items
        if (
            item.status == "available"
            and not item.is_archived
            and all(tag in item.dietary_tags for tag in filter_tags)
        )
    }


def _items_that_must_not_appear(items: list[MenuItem], filter_tags: list[str]) -> set:
    """
    Return IDs of items that must NOT appear: either they fail the base
    visibility constraints OR they are missing at least one filter tag.
    """
    return {
        str(item.id)
        for item in items
        if (
            item.status != "available"
            or item.is_archived
            or not all(tag in item.dietary_tags for tag in filter_tags)
        )
    }


# ---------------------------------------------------------------------------
# Property 28 Test Class
# ---------------------------------------------------------------------------


class TestPropertyDietaryFilterCorrectness(TestCase):
    """
    Property 28: Dietary Filter Correctness

    For any combination of dietary filters, every item in the filtered result
    possesses ALL selected tags; no item lacking any selected tag appears.

    Validates: Requirements 14.6
    """

    def setUp(self):
        """
        Create shared infrastructure:
          - Branch + Table + active QRCode for customer session.
          - APIClient instance reused across iterations (session cookie persists).
        """
        self.branch = Branch.objects.create(
            name="Dietary Filter Property Test Branch",
            address="28 Filter Street, Addis Ababa",
            phone="0922000028",
            email="prop28@restaurant.com",
        )
        self.table = Table.objects.create(
            branch=self.branch,
            number="T28",
            seat_count=4,
        )
        self.qr_code = QRCode.objects.create(
            table=self.table,
            token=uuid.uuid4(),
            is_active=True,
            image_url="",
        )

        # Establish customer session once; reuse across all Hypothesis iterations.
        self.client = APIClient()
        ok = _establish_customer_session(self.client, self.qr_code.token)
        self.assertTrue(ok, "setUp: customer session could not be established — check QR/session flow")

    # -----------------------------------------------------------------------
    # 28a — Every returned item possesses the single requested tag
    # -----------------------------------------------------------------------

    @given(
        filter_tag=st.sampled_from(_ALL_TAGS),
        item_specs=_items_list_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28a_single_tag_filter_all_results_have_tag(
        self,
        filter_tag,
        item_specs,
    ):
        """
        **Validates: Requirements 14.6**

        Sub-property 28a: For any single dietary tag filter, every item
        returned by the API possesses that tag in its dietary_tags list.

        For every generated (dietary_tags, status, is_archived) combination:
          - Create the MenuItem in the DB.
          - Call GET /api/v1/customer/menu/?dietary_tags=<tag>.
          - Assert: for all items in the response, filter_tag ∈ item.dietary_tags.
        """
        _reset_branch(self.branch)

        # Create menu items from Hypothesis-generated specs
        created_items = [
            _create_menu_item(self.branch, tags, status, archived, i)
            for i, (tags, status, archived) in enumerate(item_specs)
        ]

        # Call the API with a single tag filter
        returned_items = _get_menu(self.client, filter_tags=[filter_tag])

        for item_data in returned_items:
            item_tags = item_data.get("dietary_tags", [])
            self.assertIn(
                filter_tag,
                item_tags,
                msg=(
                    f"Property 28a FAILED: item id={item_data.get('id')} "
                    f"was returned by ?dietary_tags={filter_tag} but its "
                    f"dietary_tags={item_tags!r} does not contain the filter tag. "
                    f"Every returned item must possess the requested tag "
                    f"(Requirement 14.6)."
                ),
            )

    # -----------------------------------------------------------------------
    # 28b — Every returned item possesses ALL tags in a multi-tag filter
    # -----------------------------------------------------------------------

    @given(
        filter_tags=_filter_tags_st,
        item_specs=_items_list_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28b_multi_tag_filter_all_results_have_all_tags(
        self,
        filter_tags,
        item_specs,
    ):
        """
        **Validates: Requirements 14.6**

        Sub-property 28b: For any combination of N dietary tag filters,
        every item returned by the API possesses ALL N tags.

        This is the AND-logic guarantee: if ?dietary_tags=vegetarian,vegan
        is requested, no item missing either 'vegetarian' or 'vegan' may appear.
        """
        _reset_branch(self.branch)

        created_items = [
            _create_menu_item(self.branch, tags, status, archived, i)
            for i, (tags, status, archived) in enumerate(item_specs)
        ]

        returned_items = _get_menu(self.client, filter_tags=filter_tags)

        for item_data in returned_items:
            item_tags = item_data.get("dietary_tags", [])
            for required_tag in filter_tags:
                self.assertIn(
                    required_tag,
                    item_tags,
                    msg=(
                        f"Property 28b FAILED: item id={item_data.get('id')} "
                        f"was returned by ?dietary_tags={','.join(filter_tags)} "
                        f"but is missing required tag '{required_tag}'. "
                        f"dietary_tags={item_tags!r}. "
                        f"Filtering is AND logic — all selected tags must be "
                        f"present (Requirement 14.6)."
                    ),
                )

    # -----------------------------------------------------------------------
    # 28c — No item lacking any required tag appears in the results
    # -----------------------------------------------------------------------

    @given(
        filter_tags=_filter_tags_st,
        item_specs=_items_list_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28c_items_missing_any_tag_are_excluded(
        self,
        filter_tags,
        item_specs,
    ):
        """
        **Validates: Requirements 14.6**

        Sub-property 28c: For any combination of dietary filters, any item
        that is missing at least one of the selected tags must NOT appear in
        the results.

        This is the complementary direction to 28b: we build the set of
        items that fail the filter (either via base visibility or missing a tag)
        and assert none of their IDs appear in the API response.
        """
        _reset_branch(self.branch)

        created_items = [
            _create_menu_item(self.branch, tags, status, archived, i)
            for i, (tags, status, archived) in enumerate(item_specs)
        ]

        returned_items = _get_menu(self.client, filter_tags=filter_tags)
        returned_ids = {item_data.get("id") for item_data in returned_items}

        excluded_ids = _items_that_must_not_appear(created_items, filter_tags)

        for excluded_id in excluded_ids:
            self.assertNotIn(
                excluded_id,
                returned_ids,
                msg=(
                    f"Property 28c FAILED: item id={excluded_id} appeared "
                    f"in the response for ?dietary_tags={','.join(filter_tags)} "
                    f"but it should have been excluded (missing a required tag, "
                    f"unavailable, or archived). "
                    f"Items not matching ALL selected filters SHALL be hidden "
                    f"(Requirement 14.6)."
                ),
            )

    # -----------------------------------------------------------------------
    # 28d — Empty filter returns all available, non-archived items
    # -----------------------------------------------------------------------

    @given(item_specs=_items_list_st)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28d_no_filter_returns_all_available_items(
        self,
        item_specs,
    ):
        """
        **Validates: Requirements 14.6**

        Sub-property 28d: When no dietary_tags filter is specified, ALL
        available non-archived items are returned, regardless of their
        dietary_tags (including items with no tags at all).

        The filter is purely additive — omitting it must never hide items.
        """
        _reset_branch(self.branch)

        created_items = [
            _create_menu_item(self.branch, tags, status, archived, i)
            for i, (tags, status, archived) in enumerate(item_specs)
        ]

        returned_items = _get_menu(self.client, filter_tags=None)
        returned_ids = {item_data.get("id") for item_data in returned_items}

        # All available, non-archived items must appear
        for item in created_items:
            if item.status == "available" and not item.is_archived:
                self.assertIn(
                    str(item.id),
                    returned_ids,
                    msg=(
                        f"Property 28d FAILED: available non-archived item "
                        f"id={item.id} (dietary_tags={item.dietary_tags!r}) "
                        f"was missing from the response when no filter was applied. "
                        f"Without a filter all available items must be returned."
                    ),
                )

        # No unavailable or archived item may appear
        for item in created_items:
            if item.status != "available" or item.is_archived:
                self.assertNotIn(
                    str(item.id),
                    returned_ids,
                    msg=(
                        f"Property 28d FAILED: item id={item.id} "
                        f"(status={item.status!r}, is_archived={item.is_archived}) "
                        f"appeared in the response without a filter. "
                        f"Only available, non-archived items should appear "
                        f"(Requirement 14.11)."
                    ),
                )

    # -----------------------------------------------------------------------
    # 28e — Non-available / archived items never appear even if they match
    # -----------------------------------------------------------------------

    @given(
        filter_tags=_filter_tags_st,
        n_hidden=st.integers(min_value=1, max_value=5),
        n_visible=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28e_non_available_items_never_appear(
        self,
        filter_tags,
        n_hidden,
        n_visible,
    ):
        """
        **Validates: Requirements 14.6, 14.11**

        Sub-property 28e: Items with status != 'available' or is_archived=True
        must NEVER appear in the customer menu response, even when they have
        ALL the requested dietary tags.

        This validates the base visibility constraint is not overridden by
        the dietary tag match.
        """
        _reset_branch(self.branch)

        # Create items that have ALL filter tags but are hidden (unavailable/archived)
        hidden_items = []
        for i in range(n_hidden):
            # Alternate between unavailable and archived (both must be hidden)
            if i % 2 == 0:
                item = _create_menu_item(
                    self.branch,
                    dietary_tags=list(filter_tags),  # has ALL required tags
                    status="unavailable",
                    is_archived=False,
                    idx=i,
                )
            else:
                item = _create_menu_item(
                    self.branch,
                    dietary_tags=list(filter_tags),  # has ALL required tags
                    status="available",
                    is_archived=True,
                    idx=i,
                )
            hidden_items.append(item)

        # Create visible items that also have ALL filter tags
        visible_items = []
        for i in range(n_visible):
            item = _create_menu_item(
                self.branch,
                dietary_tags=list(filter_tags),  # has ALL required tags
                status="available",
                is_archived=False,
                idx=n_hidden + i,
            )
            visible_items.append(item)

        returned_items = _get_menu(self.client, filter_tags=filter_tags)
        returned_ids = {item_data.get("id") for item_data in returned_items}

        # Hidden items must NOT appear
        for item in hidden_items:
            self.assertNotIn(
                str(item.id),
                returned_ids,
                msg=(
                    f"Property 28e FAILED: item id={item.id} "
                    f"(status={item.status!r}, is_archived={item.is_archived}) "
                    f"appeared in filtered response despite being non-available "
                    f"or archived. Even tag-matching items must be excluded if "
                    f"they are unavailable or archived "
                    f"(Requirements 14.6, 14.11)."
                ),
            )

        # Visible items MUST appear (they qualify on all dimensions)
        for item in visible_items:
            self.assertIn(
                str(item.id),
                returned_ids,
                msg=(
                    f"Property 28e: visible item id={item.id} with all required "
                    f"tags must appear in the filtered response."
                ),
            )

    # -----------------------------------------------------------------------
    # 28f — No qualifying item is incorrectly excluded (completeness)
    # -----------------------------------------------------------------------

    @given(
        filter_tags=_filter_tags_st,
        item_specs=_items_list_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_property_28f_all_qualifying_items_are_returned(
        self,
        filter_tags,
        item_specs,
    ):
        """
        **Validates: Requirements 14.6**

        Sub-property 28f: For any dietary filter, every item that IS
        available, non-archived, and possesses ALL selected tags MUST
        appear in the response — no qualifying item may be silently dropped
        (no false negatives).

        This validates the completeness direction: not just that wrong items
        are excluded, but that all correct items are included.
        """
        _reset_branch(self.branch)

        created_items = [
            _create_menu_item(self.branch, tags, status, archived, i)
            for i, (tags, status, archived) in enumerate(item_specs)
        ]

        returned_items = _get_menu(self.client, filter_tags=filter_tags)
        returned_ids = {item_data.get("id") for item_data in returned_items}

        qualifying_ids = _items_that_should_appear(created_items, filter_tags)

        for qualifying_id in qualifying_ids:
            self.assertIn(
                qualifying_id,
                returned_ids,
                msg=(
                    f"Property 28f FAILED: item id={qualifying_id} qualifies "
                    f"for filter ?dietary_tags={','.join(filter_tags)} "
                    f"(status=available, not archived, has all required tags) "
                    f"but was NOT returned by the API. "
                    f"All qualifying items must appear in the filtered result "
                    f"(Requirement 14.6)."
                ),
            )
