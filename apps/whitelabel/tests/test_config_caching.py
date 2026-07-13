"""
apps/whitelabel/tests/test_config_caching.py

Tests for the Redis caching layer on GET /api/v1/tenant/config/.

Cache behaviour under test (Task 20.2 — Requirements 19.1, 19.2):
  - First GET populates the cache under ``tenant_config:{schema}``
    with a 5-minute (300 s) TTL.
  - A subsequent GET returns the cached value rather than querying the DB.
  - PATCH /api/v1/tenant/config/ invalidates the cache.
  - After a TenantConfig save (via post_save signal), the cache is invalidated
    and the next GET returns fresh data from the database.

Test settings use LocMemCache which behaves identically to Redis for
set/get/delete operations and doesn't require a running Redis server.

Requirements: 19.1, 19.2
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import UserRole
from apps.whitelabel.models import TenantConfig
from apps.whitelabel.views import TenantConfigViewSet, _tenant_cache_key

User = get_user_model()

CONFIG_URL = "/api/v1/tenant/config/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure LocMemCache is clean before and after every test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenant_owner(db):
    """A Tenant_Owner user who can GET and PATCH /api/v1/tenant/config/."""
    return User.objects.create_user(
        email="owner@restaurant.com",
        password="testpass123",
        role=UserRole.TENANT_OWNER,
        is_active=True,
    )


@pytest.fixture
def tenant_config(db):
    """A pre-existing TenantConfig record in the DB."""
    return TenantConfig.objects.create(
        restaurant_name="Cache Test Restaurant",
        primary_color="#3B82F6",
        secondary_color="#F59E0B",
    )


@pytest.fixture
def auth_client(tenant_owner):
    """Authenticated API client using the tenant owner credentials."""
    client = APIClient()
    client.force_authenticate(user=tenant_owner)
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_cache_key():
    """Return the cache key that TenantConfigViewSet uses for the current tenant."""
    return _tenant_cache_key()


# ---------------------------------------------------------------------------
# Tests: cache population on GET
# ---------------------------------------------------------------------------


class TestTenantConfigCachePopulation:
    """GET /api/v1/tenant/config/ populates the cache on first request."""

    @pytest.mark.django_db
    def test_first_get_returns_200(self, auth_client, tenant_config):
        """GET /api/v1/tenant/config/ with a tenant owner returns HTTP 200."""
        response = auth_client.get(CONFIG_URL)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_first_get_populates_cache(self, auth_client, tenant_config):
        """After the first GET the ``tenant_config:{schema}`` cache key is set."""
        key = _get_cache_key()
        assert cache.get(key) is None, "Cache must be empty before first GET"

        auth_client.get(CONFIG_URL)

        cached = cache.get(key)
        assert cached is not None, "Cache must be populated after first GET"

    @pytest.mark.django_db
    def test_cached_data_matches_response(self, auth_client, tenant_config):
        """The data cached by the view matches the API response."""
        response = auth_client.get(CONFIG_URL)
        assert response.status_code == status.HTTP_200_OK

        cached = cache.get(_get_cache_key())
        assert cached is not None
        assert cached["restaurant_name"] == tenant_config.restaurant_name

    @pytest.mark.django_db
    def test_cache_uses_five_minute_ttl(self, auth_client, tenant_config):
        """
        The view's _CONFIG_CACHE_TTL constant must be 300 seconds (5 minutes).

        Requirements: 19.1 — tenant config cached with 5-minute TTL
        """
        assert TenantConfigViewSet._CONFIG_CACHE_TTL == 300

    @pytest.mark.django_db
    def test_no_config_returns_204_and_no_cache(self, auth_client):
        """
        When no TenantConfig row exists, GET returns 204 and does NOT
        populate the cache (there is nothing to cache).
        """
        # Ensure no config exists
        TenantConfig.objects.all().delete()

        response = auth_client.get(CONFIG_URL)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Cache must not be set for an empty config
        assert cache.get(_get_cache_key()) is None


# ---------------------------------------------------------------------------
# Tests: cache hit on second request
# ---------------------------------------------------------------------------


class TestTenantConfigCacheHit:
    """Second GET returns cached value."""

    @pytest.mark.django_db
    def test_second_get_returns_cached_value(self, auth_client, tenant_config):
        """
        After the first GET populates the cache, a second GET returns data
        from the cache rather than the DB.

        We verify this by pre-loading sentinel data into the cache and
        confirming the view returns that sentinel on the next GET.
        """
        # First GET to establish session context and populate cache
        first_response = auth_client.get(CONFIG_URL)
        assert first_response.status_code == status.HTTP_200_OK

        # Overwrite cache with sentinel data
        sentinel = {
            "id": None,
            "restaurant_name": "Sentinel Restaurant",
            "primary_color": "#000000",
            "secondary_color": "#FFFFFF",
            "font_choice": "default",
            "custom_domain": "",
            "favicon": None,
            "logo": None,
            "qr_design_template": "default",
            "receipt_header": "",
            "receipt_footer": "",
            "default_language": "en",
            "currency": "ETB",
            "currency_format": "{symbol}{amount}",
            "timezone": "Africa/Addis_Ababa",
            "date_format": "%d/%m/%Y",
            "time_format": "%H:%M",
            "tax_rate": "15.00",
            "tax_label": "VAT",
            "service_charge_pct": "0.00",
            "table_number_prefix": "",
        }
        cache.set(_get_cache_key(), sentinel, timeout=300)

        # Second GET must return sentinel from cache
        second_response = auth_client.get(CONFIG_URL)
        assert second_response.status_code == status.HTTP_200_OK
        assert second_response.data["restaurant_name"] == "Sentinel Restaurant", (
            "Second GET must return cached sentinel — cache is not being used"
        )

    @pytest.mark.django_db
    def test_repeated_gets_return_consistent_data(self, auth_client, tenant_config):
        """Multiple GETs return the same data (cache is stable within TTL)."""
        r1 = auth_client.get(CONFIG_URL)
        r2 = auth_client.get(CONFIG_URL)
        assert r1.status_code == r2.status_code == status.HTTP_200_OK
        assert r1.data["restaurant_name"] == r2.data["restaurant_name"]
        assert r1.data["primary_color"] == r2.data["primary_color"]


# ---------------------------------------------------------------------------
# Tests: cache invalidation on PATCH
# ---------------------------------------------------------------------------


class TestTenantConfigCachePatchInvalidation:
    """PATCH /api/v1/tenant/config/ invalidates the cache."""

    @pytest.mark.django_db
    def test_patch_returns_200(self, auth_client, tenant_config):
        """PATCH /api/v1/tenant/config/ with valid data returns HTTP 200."""
        response = auth_client.patch(
            CONFIG_URL,
            {"restaurant_name": "Updated Restaurant"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_patch_invalidates_cache(self, auth_client, tenant_config):
        """
        A successful PATCH must delete the cached config so the next GET
        reads fresh data from the database.

        Requirements: 19.2
        """
        # Populate the cache
        auth_client.get(CONFIG_URL)
        assert cache.get(_get_cache_key()) is not None, "Cache must be set after GET"

        # PATCH should clear the cache
        auth_client.patch(
            CONFIG_URL,
            {"restaurant_name": "After Patch"},
            format="json",
        )

        assert cache.get(_get_cache_key()) is None, (
            "Cache must be invalidated after PATCH /api/v1/tenant/config/"
        )

    @pytest.mark.django_db
    def test_get_after_patch_returns_updated_data(self, auth_client, tenant_config):
        """
        After a PATCH invalidates the cache, the next GET fetches fresh data
        and returns the updated values.

        Requirements: 19.1, 19.2
        """
        # First GET — populates cache with original name
        auth_client.get(CONFIG_URL)

        # PATCH — updates name and invalidates cache
        auth_client.patch(
            CONFIG_URL,
            {"restaurant_name": "New Name After Patch"},
            format="json",
        )

        # Next GET — must re-fetch from DB and return updated name
        response = auth_client.get(CONFIG_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["restaurant_name"] == "New Name After Patch", (
            "GET after PATCH must return the updated restaurant name from DB"
        )


# ---------------------------------------------------------------------------
# Tests: cache invalidation via signal
# ---------------------------------------------------------------------------


class TestTenantConfigSignalInvalidation:
    """TenantConfig post-save signal invalidates the cache."""

    @pytest.mark.django_db
    def test_signal_invalidates_cache_on_direct_save(self, auth_client, tenant_config):
        """
        Saving a TenantConfig instance directly (bypassing the ViewSet) must
        still invalidate the cache via the post_save signal.

        This is the safety-net for non-ViewSet save paths (admin, management
        commands, migrations, tests).

        Requirements: 19.1, 19.2
        """
        # Populate the cache via GET
        auth_client.get(CONFIG_URL)
        assert cache.get(_get_cache_key()) is not None

        # Direct save (not via the ViewSet) — triggers on_tenant_config_saved
        tenant_config.restaurant_name = "Direct Save Update"
        tenant_config.save()

        # Cache must be cleared by the signal
        assert cache.get(_get_cache_key()) is None, (
            "post_save signal must invalidate cache on direct TenantConfig.save()"
        )

    @pytest.mark.django_db
    def test_get_after_signal_save_returns_fresh_data(self, auth_client, tenant_config):
        """
        After the signal clears the cache, the next GET returns the updated
        value from the database.

        Requirements: 19.1, 19.2
        """
        # Populate cache
        auth_client.get(CONFIG_URL)

        # Direct save — invalidates cache via signal
        tenant_config.restaurant_name = "Signal Refreshed"
        tenant_config.save()

        # Next GET must return the updated name from DB
        response = auth_client.get(CONFIG_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["restaurant_name"] == "Signal Refreshed", (
            "GET after signal-triggered invalidation must return fresh DB data"
        )

    @pytest.mark.django_db
    def test_signal_handler_uses_correct_cache_key(self, tenant_config):
        """
        The signal handler (on_tenant_config_saved) must delete the same key
        that TenantConfigViewSet.retrieve() writes — ``tenant_config:{schema}``.
        """
        view_key = _get_cache_key()

        # Manually populate the view's cache key
        cache.set(view_key, {"restaurant_name": "Original"}, timeout=300)
        assert cache.get(view_key) is not None

        # Trigger the signal by saving
        tenant_config.restaurant_name = "Key Consistency Check"
        tenant_config.save()

        # The signal must have deleted the view's key
        assert cache.get(view_key) is None, (
            "Signal handler must delete the same key that "
            f"TenantConfigViewSet writes: {view_key!r}"
        )
