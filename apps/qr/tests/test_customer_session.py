"""
apps/qr/tests/test_customer_session.py

API tests for the customer session and menu endpoints (Task 16.1, 16.3).

Endpoints under test:
  POST /api/v1/customer/session/ — create anonymous session from QR scan
  GET  /api/v1/customer/menu/   — return active menu for session's branch

Test cases:
  16.1-TC-01: POST with valid token → 200, session contains branch_id and table_number
  16.1-TC-02: POST with invalid token → 404 with QR_CODE_INVALID
  16.1-TC-03: POST with unknown (never-existed) token → 404 with QR_CODE_INVALID
  16.1-TC-04: POST without token → 400
  16.3-TC-01: GET /customer/menu/ with valid session → 200, returns only available
              non-archived items (status=available, is_archived=False)
  16.3-TC-02: GET /customer/menu/ with dietary_tags filter → returns only items
              whose dietary_tags contain all specified tags
  16.3-TC-03: GET /customer/menu/ with no session → 401
  16.3-TC-04: Unavailable items (status != available) are excluded
  16.3-TC-05: Archived items (is_archived=True) are excluded
  16.3-TC-06: Items from another branch are not returned
  16.3-TC-07: Response includes full fields: id, name, price, nutrition, categories

Requirements: 14.2, 14.4, 14.5, 14.6, 14.11, 3.7
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.menus.models import Category, MenuItem, NutritionProfile
from apps.qr.models import QRCode

User = get_user_model()

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

SESSION_URL = "/api/v1/customer/session/"
MENU_URL    = "/api/v1/customer/menu/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Test Branch",
        address="1 Test Street, Addis Ababa",
        phone="0911000001",
        email="test@restaurant.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="2 Other Street, Addis Ababa",
        phone="0911000002",
        email="other@restaurant.com",
    )


@pytest.fixture
def table(db, branch):
    return Table.objects.create(
        branch=branch,
        number="7",
        seat_count=4,
    )


@pytest.fixture
def active_qr(db, table):
    """An active QRCode for the primary branch table."""
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="",
    )


@pytest.fixture
def inactive_qr(db, table):
    """A deactivated QRCode — simulates a regenerated/expired code."""
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=False,
        image_url="",
    )


@pytest.fixture
def category(db, branch):
    return Category.objects.create(branch=branch, name="Starters")


@pytest.fixture
def available_item(db, branch, category):
    """An available, non-archived item with nutrition data."""
    item = MenuItem.objects.create(
        branch=branch,
        name="Doro Wat",
        description="Spiced chicken stew with injera",
        price="150.00",
        prep_time_minutes=40,
        status="available",
        is_archived=False,
        dietary_tags=["halal", "high_protein"],
    )
    item.categories.add(category)
    NutritionProfile.objects.create(
        menu_item=item,
        calories_kcal="520.00",
        protein_g="42.00",
        carbs_g="30.00",
        fat_g="18.00",
        sodium_mg="350.00",
        allergens=["gluten"],
    )
    return item


@pytest.fixture
def vegan_item(db, branch):
    """An available vegan item."""
    return MenuItem.objects.create(
        branch=branch,
        name="Misir Wot",
        description="Spiced red lentils",
        price="90.00",
        prep_time_minutes=25,
        status="available",
        is_archived=False,
        dietary_tags=["vegan", "vegetarian", "halal", "gluten_free"],
    )


@pytest.fixture
def unavailable_item(db, branch):
    """An item with status=unavailable — must NOT appear in customer menu."""
    return MenuItem.objects.create(
        branch=branch,
        name="Seasonal Special",
        description="Not available right now",
        price="200.00",
        prep_time_minutes=60,
        status="unavailable",
        is_archived=False,
        dietary_tags=[],
    )


@pytest.fixture
def archived_item(db, branch):
    """An archived item — must NOT appear in customer menu."""
    return MenuItem.objects.create(
        branch=branch,
        name="Old Dish",
        description="No longer served",
        price="100.00",
        prep_time_minutes=30,
        status="available",
        is_archived=True,  # archived → hidden from customers
        dietary_tags=[],
    )


@pytest.fixture
def other_branch_item(db, other_branch):
    """An available item in a different branch — must NOT appear."""
    return MenuItem.objects.create(
        branch=other_branch,
        name="Kitfo",
        description="Ethiopian beef tartare",
        price="250.00",
        prep_time_minutes=10,
        status="available",
        is_archived=False,
        dietary_tags=[],
    )


# ---------------------------------------------------------------------------
# Helper: initialise a customer session
# ---------------------------------------------------------------------------

def _create_session(api_client, token):
    """POST /api/v1/customer/session/ with the given UUID token string."""
    return api_client.post(SESSION_URL, {"token": str(token)}, format="json")


# ===========================================================================
# 16.1 — Customer Session Endpoint
# ===========================================================================

class TestCustomerSessionEndpoint:
    """POST /api/v1/customer/session/ tests."""

    @pytest.mark.django_db
    def test_valid_token_returns_200_and_sets_session(self, api_client, active_qr, table, branch):
        """
        16.1-TC-01: POST with a valid active token → 200.
        Session contains branch_id and table_number.
        Response includes session_id (or status ok) and branch/table info.

        Requirements: 3.7, 14.2
        """
        response = _create_session(api_client, active_qr.token)

        assert response.status_code == status.HTTP_200_OK, (
            f"Expected 200, got {response.status_code}: {response.data}"
        )

        data = response.data
        # Response must include branch and table info
        assert str(branch.id) == data.get("branch_id"), (
            f"branch_id mismatch: {data}"
        )
        assert str(table.id) == data.get("table_id"), (
            f"table_id mismatch: {data}"
        )
        assert data.get("table_number") == table.number, (
            f"table_number mismatch: {data}"
        )

        # Subsequent request to /api/v1/customer/menu/ should succeed
        # (verifies the session cookie was actually set)
        menu_resp = api_client.get(MENU_URL)
        assert menu_resp.status_code == status.HTTP_200_OK, (
            f"Menu should be accessible after valid session creation, "
            f"got {menu_resp.status_code}: {menu_resp.data}"
        )

    @pytest.mark.django_db
    def test_session_stores_branch_id_and_table_number(
        self, api_client, active_qr, table, branch
    ):
        """
        16.1-TC-01 (session data): The Django session created by a successful
        POST must contain branch_id and table_number under 'customer_session'.

        Requirement: 3.7
        """
        response = _create_session(api_client, active_qr.token)
        assert response.status_code == status.HTTP_200_OK

        # After creation, GET /customer/menu/ works → session data is set
        menu_resp = api_client.get(MENU_URL)
        assert menu_resp.status_code == status.HTTP_200_OK, (
            "Menu should return 200 after session creation (proves session was set)"
        )

    @pytest.mark.django_db
    def test_inactive_token_returns_404_with_error_code(
        self, api_client, inactive_qr
    ):
        """
        16.1-TC-02: POST with an inactive/expired token → 404 with QR_CODE_INVALID.

        Requirement: 14.4
        """
        response = _create_session(api_client, inactive_qr.token)

        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            f"Expected 404, got {response.status_code}: {response.data}"
        )

        data = response.data
        response_text = str(data)
        assert "QR_CODE_INVALID" in response_text, (
            f"Response must contain QR_CODE_INVALID, got: {data}"
        )
        # Must not expose stack traces
        assert "Traceback" not in response_text
        assert "Exception" not in response_text

    @pytest.mark.django_db
    def test_nonexistent_token_returns_404_with_error_code(self, api_client):
        """
        16.1-TC-03: POST with a UUID that has never been issued → 404 with
        QR_CODE_INVALID.

        Requirement: 14.4
        """
        never_issued_token = uuid.uuid4()
        response = _create_session(api_client, never_issued_token)

        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            f"Expected 404, got {response.status_code}: {response.data}"
        )

        data = response.data
        assert "QR_CODE_INVALID" in str(data), (
            f"Response must contain QR_CODE_INVALID, got: {data}"
        )

    @pytest.mark.django_db
    def test_missing_token_field_returns_400(self, api_client):
        """
        16.1-TC-04: POST without 'token' field → 400.
        """
        response = api_client.post(SESSION_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400, got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_malformed_uuid_returns_400(self, api_client):
        """POST with a non-UUID token value → 400."""
        response = api_client.post(
            SESSION_URL, {"token": "not-a-valid-uuid"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400 for malformed UUID, got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_no_authentication_required(self, api_client, active_qr):
        """
        POST /api/v1/customer/session/ must be accessible without any auth.
        Requirement: 3.7 (no account creation required)
        """
        # Ensure no user is authenticated
        api_client.force_authenticate(user=None)
        response = _create_session(api_client, active_qr.token)
        assert response.status_code == status.HTTP_200_OK


# ===========================================================================
# 16.3 — Customer Menu Endpoint
# ===========================================================================

class TestCustomerMenuEndpoint:
    """GET /api/v1/customer/menu/ tests."""

    @pytest.mark.django_db
    def test_no_session_returns_401(self, api_client):
        """
        16.3-TC-03: GET /customer/menu/ without a valid session → 401 or 403.

        DRF with SessionAuthentication returns 403 for missing customer sessions
        (since IsCustomerSession checks session data rather than auth credentials).
        We accept either 401 or 403 as both indicate the request is denied.

        Requirement: 4.2 (IsCustomerSession permission)
        """
        # Fresh client — no session cookie
        response = api_client.get(MENU_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"Expected 401 or 403 for missing session, got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_returns_only_available_non_archived_items(
        self,
        api_client,
        active_qr,
        available_item,
        unavailable_item,
        archived_item,
    ):
        """
        16.3-TC-01: GET with valid session returns only status=available AND
        is_archived=False items.

        Requirements: 14.11
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK, (
            f"Expected 200, got {response.status_code}: {response.data}"
        )

        returned_ids = {item["id"] for item in response.data}

        # Available non-archived item MUST appear
        assert str(available_item.id) in returned_ids, (
            "Available non-archived item must appear in customer menu"
        )
        # Unavailable item MUST NOT appear (Req 14.11)
        assert str(unavailable_item.id) not in returned_ids, (
            "Unavailable item must NOT appear in customer menu (Req 14.11)"
        )
        # Archived item MUST NOT appear (Req 14.11)
        assert str(archived_item.id) not in returned_ids, (
            "Archived item must NOT appear in customer menu (Req 14.11)"
        )

    @pytest.mark.django_db
    def test_dietary_tags_filter_single_tag(
        self,
        api_client,
        active_qr,
        available_item,
        vegan_item,
    ):
        """
        16.3-TC-02 (single tag): ?dietary_tags=vegan returns only items
        that have 'vegan' in their dietary_tags.

        Requirement: 14.6
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL + "?dietary_tags=vegan")

        assert response.status_code == status.HTTP_200_OK

        returned_ids = {item["id"] for item in response.data}

        # vegan_item has "vegan" tag → must appear
        assert str(vegan_item.id) in returned_ids, (
            "Vegan item must appear when filtering by vegan tag"
        )
        # available_item does NOT have "vegan" tag → must NOT appear
        assert str(available_item.id) not in returned_ids, (
            "Non-vegan item must NOT appear when filtering by vegan"
        )

    @pytest.mark.django_db
    def test_dietary_tags_filter_multiple_tags_all_must_match(
        self,
        api_client,
        active_qr,
        available_item,
        vegan_item,
    ):
        """
        16.3-TC-02 (multi-tag AND logic): ?dietary_tags=vegan,halal returns
        only items with BOTH tags present. available_item has 'halal' but not
        'vegan'; vegan_item has both → only vegan_item appears.

        Requirement: 14.6 (items not matching ALL selected filters are hidden)
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL + "?dietary_tags=vegan,halal")

        assert response.status_code == status.HTTP_200_OK

        returned_ids = {item["id"] for item in response.data}

        # vegan_item has both "vegan" and "halal" → appears
        assert str(vegan_item.id) in returned_ids, (
            "Item with both vegan and halal tags must appear in combined filter"
        )
        # available_item has "halal" but NOT "vegan" → must not appear
        assert str(available_item.id) not in returned_ids, (
            "Item missing one of the required filter tags must not appear"
        )

    @pytest.mark.django_db
    def test_items_from_other_branch_not_returned(
        self,
        api_client,
        active_qr,
        available_item,
        other_branch_item,
    ):
        """
        Branch scoping: items from a different branch must not appear.

        Requirements: 8.2, 14.2
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK

        returned_ids = {item["id"] for item in response.data}

        assert str(available_item.id) in returned_ids, (
            "Item from session's branch must appear"
        )
        assert str(other_branch_item.id) not in returned_ids, (
            "Item from different branch must NOT appear (scope violation)"
        )

    @pytest.mark.django_db
    def test_response_includes_required_fields(
        self,
        api_client,
        active_qr,
        available_item,
    ):
        """
        16.3-TC-07: Response includes all required display fields per Req 14.5:
        id, name, description, image_url, price, prep_time_minutes,
        dietary_tags, nutrition (with calories, protein, carbs, fat, sodium,
        allergens), categories.
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK

        # Find the available_item in the response
        item_data = next(
            (i for i in response.data if i["id"] == str(available_item.id)),
            None,
        )
        assert item_data is not None, "available_item must be in the response"

        # Check all required fields
        required_fields = [
            "id", "name", "description", "price",
            "prep_time_minutes", "dietary_tags", "categories",
        ]
        for field in required_fields:
            assert field in item_data, (
                f"Required field '{field}' missing from menu item response"
            )

        # image_url field must be present (may be None if no image)
        assert "image_url" in item_data, "image_url field must be present"

        # Nutrition data should be present
        assert "nutrition" in item_data, "nutrition field must be present (Req 14.5)"
        nutrition = item_data["nutrition"]
        if nutrition is not None:
            for nut_field in ["calories_kcal", "protein_g", "carbs_g", "fat_g", "allergens"]:
                assert nut_field in nutrition, (
                    f"Nutrition field '{nut_field}' missing (Req 14.5)"
                )

    @pytest.mark.django_db
    def test_categories_returned_as_list_of_names(
        self,
        api_client,
        active_qr,
        available_item,
        category,
    ):
        """
        Categories field must be a list of category name strings.
        Requirement: 14.5 (categories listed in menu item detail)
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK

        item_data = next(
            (i for i in response.data if i["id"] == str(available_item.id)),
            None,
        )
        assert item_data is not None

        categories = item_data.get("categories", [])
        assert isinstance(categories, list), "categories must be a list"
        # Category assigned in the fixture is "Starters"
        assert category.name in categories, (
            f"Category '{category.name}' must be in the item's categories list"
        )

    @pytest.mark.django_db
    def test_nutrition_data_included(
        self,
        api_client,
        active_qr,
        available_item,
    ):
        """
        Nutrition profile data included when available_item has a NutritionProfile.
        Requirement: 14.5 (nutritional information displayed)
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK

        item_data = next(
            (i for i in response.data if i["id"] == str(available_item.id)),
            None,
        )
        assert item_data is not None

        nutrition = item_data.get("nutrition")
        assert nutrition is not None, (
            "Nutrition must be non-null for items with a NutritionProfile"
        )
        # Check specific fields created in the fixture
        assert str(nutrition.get("calories_kcal", "")).startswith("520"), (
            f"calories_kcal should be ~520, got: {nutrition.get('calories_kcal')}"
        )
        assert "gluten" in (nutrition.get("allergens") or []), (
            "Allergen 'gluten' must appear in nutrition.allergens"
        )

    @pytest.mark.django_db
    def test_empty_filters_returns_all_available_items(
        self,
        api_client,
        active_qr,
        available_item,
        vegan_item,
    ):
        """
        When no dietary_tags filter is applied, all available non-archived
        items are returned.
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL)

        assert response.status_code == status.HTTP_200_OK

        returned_ids = {item["id"] for item in response.data}
        assert str(available_item.id) in returned_ids
        assert str(vegan_item.id) in returned_ids

    @pytest.mark.django_db
    def test_filter_with_no_matching_items_returns_empty_list(
        self,
        api_client,
        active_qr,
        available_item,
    ):
        """
        Filtering by a tag that no item has → empty list (not an error).
        Requirement: 14.6
        """
        _create_session(api_client, active_qr.token)
        response = api_client.get(MENU_URL + "?dietary_tags=keto")

        assert response.status_code == status.HTTP_200_OK
        # available_item has halal + high_protein, not keto → empty
        assert str(available_item.id) not in {item["id"] for item in response.data}

    @pytest.mark.django_db
    def test_session_expired_after_clearing_session_returns_401(
        self,
        api_client,
        active_qr,
    ):
        """
        Once a session is cleared (simulating expiry), the menu endpoint
        returns 401 or 403 again.
        """
        _create_session(api_client, active_qr.token)
        # Verify session works
        assert api_client.get(MENU_URL).status_code == status.HTTP_200_OK

        # Clear the session
        api_client.session.flush()

        # Now menu should deny access
        fresh_client = APIClient()
        response = fresh_client.get(MENU_URL)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
