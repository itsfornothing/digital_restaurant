"""
apps/qr/tests/test_menu_caching.py

Tests for the Redis caching layer on GET /api/v1/customer/menu/.

Cache behaviour under test (Task 20.2 — Requirements 19.1, 19.2):
  - First GET populates the cache under ``menu:branch:{branch_id}`` (30 s TTL)
  - Second GET returns the cached value (no additional DB hit)
  - After a MenuItem is saved (via signal), the cache is invalidated and the
    next GET fetches fresh data from the database

Test settings use LocMemCache (django.core.cache.backends.locmem.LocMemCache),
which behaves identically to Redis for set/get/delete operations.

Requirements: 19.1, 19.2
"""

import uuid

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.qr.customer_views import CustomerMenuView
from apps.qr.models import QRCode

SESSION_URL = "/api/v1/customer/session/"
MENU_URL = "/api/v1/customer/menu/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure cache is clean before and after every test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Cache Test Branch",
        address="1 Cache Street, Addis Ababa",
        phone="0911000099",
        email="cache@restaurant.com",
    )


@pytest.fixture
def table(db, branch):
    return Table.objects.create(branch=branch, number="3", seat_count=2)


@pytest.fixture
def active_qr(db, table):
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="",
    )


@pytest.fixture
def menu_item(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Injera Combo",
        description="Traditional platter",
        price="180.00",
        prep_time_minutes=20,
        status="available",
        is_archived=False,
        dietary_tags=["halal"],
    )


@pytest.fixture
def api_client(active_qr):
    """API client with an active customer session already established."""
    client = APIClient()
    client.post(SESSION_URL, {"token": str(active_qr.token)}, format="json")
    return client


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _cache_key(branch_id):
    return CustomerMenuView._menu_cache_key(str(branch_id))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMenuCachePopulation:
    """GET /api/v1/customer/menu/ populates the cache on first request."""

    @pytest.mark.django_db
    def test_first_get_returns_200(self, api_client, menu_item):
        """GET /api/v1/customer/menu/ with a valid session returns HTTP 200."""
        response = api_client.get(MENU_URL)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_first_get_populates_cache(self, api_client, branch, menu_item):
        """After the first GET the cache key is populated."""
        # Cache must be empty before the request
        key = _cache_key(branch.id)
        assert cache.get(key) is None

        api_client.get(MENU_URL)

        cached = cache.get(key)
        assert cached is not None, "Cache must be populated after first GET"

    @pytest.mark.django_db
    def test_cached_data_contains_menu_item(self, api_client, branch, menu_item):
        """The cached payload includes the menu item returned by the view."""
        api_client.get(MENU_URL)

        cached = cache.get(_cache_key(branch.id))
        assert cached is not None
        ids = [str(item["id"]) for item in cached]
        assert str(menu_item.id) in ids, "Cached data must contain the available menu item"

    @pytest.mark.django_db
    def test_filtered_request_does_not_populate_cache(self, api_client, branch, menu_item):
        """
        Requests with ?dietary_tags= bypass the cache (only full unfiltered
        responses are cached — Task 20.2 design decision).
        """
        api_client.get(MENU_URL + "?dietary_tags=halal")
        # Cache key must NOT be set for filtered requests
        assert cache.get(_cache_key(branch.id)) is None


class TestMenuCacheHit:
    """Second GET returns cached value."""

    @pytest.mark.django_db
    def test_second_get_returns_cached_value(self, api_client, branch, menu_item):
        """
        After the first GET populates the cache, a second GET returns data
        from the cache rather than hitting the DB.

        We verify this by pre-loading the cache with known sentinel data and
        confirming the view returns that sentinel data on the next GET.
        """
        # First GET to establish a valid session context (writes to cache)
        first_response = api_client.get(MENU_URL)
        assert first_response.status_code == status.HTTP_200_OK

        # Overwrite the cache with sentinel data
        sentinel = [{"id": "sentinel-id", "name": "Sentinel Item", "price": "0.01"}]
        cache.set(_cache_key(branch.id), sentinel, timeout=30)

        # Second GET must return the sentinel (proving it hit the cache)
        second_response = api_client.get(MENU_URL)
        assert second_response.status_code == status.HTTP_200_OK
        assert second_response.data == sentinel, (
            "Second GET must return cached sentinel data — cache is not being used"
        )

    @pytest.mark.django_db
    def test_repeated_gets_return_same_data(self, api_client, branch, menu_item):
        """Multiple GETs return consistent data (cache is stable within TTL)."""
        r1 = api_client.get(MENU_URL)
        r2 = api_client.get(MENU_URL)
        assert r1.status_code == r2.status_code == status.HTTP_200_OK
        # Both responses should contain the same item ids
        ids1 = {item["id"] for item in r1.data}
        ids2 = {item["id"] for item in r2.data}
        assert ids1 == ids2


class TestMenuCacheInvalidation:
    """Cache is invalidated after a MenuItem is saved."""

    @pytest.mark.django_db
    def test_menu_item_save_invalidates_cache(self, api_client, branch, menu_item):
        """
        After any MenuItem.save(), the ``menu:branch:{branch_id}`` cache key
        is deleted (via signal) so the next GET fetches from DB.

        Requirements: 19.1, 19.2
        """
        # Populate the cache
        api_client.get(MENU_URL)
        assert cache.get(_cache_key(branch.id)) is not None

        # Save the MenuItem (triggers on_menu_item_saved signal)
        menu_item.price = "999.00"
        menu_item.save()

        # Cache must be cleared now
        assert cache.get(_cache_key(branch.id)) is None, (
            "Cache must be invalidated after MenuItem.save() (signal handler)"
        )

    @pytest.mark.django_db
    def test_get_after_save_reflects_updated_data(self, api_client, branch, menu_item):
        """
        After a MenuItem save invalidates the cache, the next GET reflects the
        updated data from the database.

        Requirements: 19.1, 19.2
        """
        # First GET — populates cache with original price
        api_client.get(MENU_URL)

        # Update price and save (invalidates cache via signal)
        menu_item.price = "250.00"
        menu_item.save()

        # Next GET must read from DB, not cache
        response = api_client.get(MENU_URL)
        assert response.status_code == status.HTTP_200_OK

        item_data = next(
            (i for i in response.data if i["id"] == str(menu_item.id)),
            None,
        )
        assert item_data is not None
        assert str(item_data["price"]) == "250.00", (
            "GET after cache invalidation must return updated price from DB"
        )

    @pytest.mark.django_db
    def test_new_menu_item_appears_after_cache_invalidation(
        self, api_client, branch, menu_item
    ):
        """
        Creating a new MenuItem invalidates the cache (via signal) so it
        appears in the next GET.

        Requirements: 19.1, 19.2
        """
        # First GET — only one item in cache
        r1 = api_client.get(MENU_URL)
        assert len(r1.data) == 1

        # Create a new item (triggers signal → invalidates cache)
        new_item = MenuItem.objects.create(
            branch=branch,
            name="Tibs",
            description="Sautéed beef",
            price="220.00",
            prep_time_minutes=15,
            status="available",
            is_archived=False,
            dietary_tags=[],
        )

        # Next GET must show both items
        r2 = api_client.get(MENU_URL)
        assert r2.status_code == status.HTTP_200_OK
        ids = {item["id"] for item in r2.data}
        assert str(new_item.id) in ids, "New item must appear after cache invalidation"

    @pytest.mark.django_db
    def test_cache_key_format_matches_across_view_and_signal(self, branch, menu_item):
        """
        The cache key used by CustomerMenuView._menu_cache_key() must match
        the key deleted by the signal handler in menus/signals.py.

        Both must use ``menu:branch:{branch_id}``.

        This test validates key consistency — the core bug targeted in Task 20.2.
        """
        branch_id_str = str(branch.id)
        view_key = CustomerMenuView._menu_cache_key(branch_id_str)

        # Simulate what the signal handler does
        from apps.menus.signals import _invalidate_menu_cache
        cache.set(view_key, [{"id": "test"}], timeout=30)
        assert cache.get(view_key) is not None

        # Signal invalidation must delete the view's key
        _invalidate_menu_cache(branch_id_str)
        assert cache.get(view_key) is None, (
            "Signal handler must delete the same key that CustomerMenuView writes. "
            f"Expected key: {view_key!r}"
        )

    @pytest.mark.django_db
    def test_viewset_invalidation_uses_same_key(self, branch, menu_item):
        """
        The MenuItemViewSet._invalidate_branch_menu_cache() helper (called on
        create/update/archive) must delete the same key that CustomerMenuView
        writes — ``menu:branch:{branch_id}``.

        This is the second invalidation path targeted in Task 20.2.
        """
        branch_id_str = str(branch.id)
        view_key = CustomerMenuView._menu_cache_key(branch_id_str)

        # Populate the view's cache key
        cache.set(view_key, [{"id": "test"}], timeout=30)
        assert cache.get(view_key) is not None

        # ViewSet invalidation helper must delete the same key
        from apps.menus.views import _invalidate_branch_menu_cache
        _invalidate_branch_menu_cache(branch_id_str)
        assert cache.get(view_key) is None, (
            "MenuItemViewSet helper must delete the same key as CustomerMenuView. "
            f"Expected key: {view_key!r}"
        )
