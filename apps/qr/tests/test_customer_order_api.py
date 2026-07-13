"""
apps/qr/tests/test_customer_order_api.py

API tests for POST /api/v1/customer/orders/ — order placement endpoint (Task 17.1).

Test cases:
  TC-17-01: POST with valid session and available items → 201, Order + OrderItems created,
            total_amount = sum(unit_price × quantity), status=confirmed
  TC-17-02: unit_price snapshot matches MenuItem.price at placement time
  TC-17-03: POST with an unavailable item (status != available) → 422 ITEM_UNAVAILABLE
  TC-17-04: POST with an archived item (is_archived=True) → 422 ITEM_UNAVAILABLE
  TC-17-05: POST without active session → 403
  TC-17-06: customer_name and customer_phone are stored but never required
  TC-17-07: POST without items list → 400
  TC-17-08: POST with zero quantity → 400
  TC-17-09: POST with item belonging to a different branch → 422 ITEM_UNAVAILABLE
  TC-17-10: Multiple items → total_amount computed correctly
  TC-17-11: special_instructions stored per item

Requirements: 14.7, 14.8, 14.9
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.orders.models import Order, OrderItem
from apps.qr.models import QRCode

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

SESSION_URL = "/api/v1/customer/session/"
ORDERS_URL  = "/api/v1/customer/orders/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Order Test Branch",
        address="10 Order Street, Addis Ababa",
        phone="0911111111",
        email="orders@restaurant.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="99 Other Street",
        phone="0922222222",
        email="other@restaurant.com",
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
def available_item(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Tibs",
        description="Sautéed beef",
        price=Decimal("180.00"),
        prep_time_minutes=20,
        status="available",
        is_archived=False,
        dietary_tags=["halal"],
    )


@pytest.fixture
def available_item2(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Injera",
        description="Fermented flatbread",
        price=Decimal("30.00"),
        prep_time_minutes=5,
        status="available",
        is_archived=False,
        dietary_tags=["vegan", "vegetarian"],
    )


@pytest.fixture
def unavailable_item(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Seasonal Soup",
        description="Not available today",
        price=Decimal("100.00"),
        prep_time_minutes=30,
        status="unavailable",
        is_archived=False,
        dietary_tags=[],
    )


@pytest.fixture
def archived_item(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Old Special",
        description="No longer on menu",
        price=Decimal("120.00"),
        prep_time_minutes=25,
        status="available",
        is_archived=True,
        dietary_tags=[],
    )


@pytest.fixture
def other_branch_item(db, other_branch):
    return MenuItem.objects.create(
        branch=other_branch,
        name="Kitfo",
        description="Ethiopian beef tartare",
        price=Decimal("250.00"),
        prep_time_minutes=10,
        status="available",
        is_archived=False,
        dietary_tags=[],
    )


# ---------------------------------------------------------------------------
# Helper: establish a customer session
# ---------------------------------------------------------------------------

def _establish_session(api_client, qr_token):
    """POST /api/v1/customer/session/ and assert success."""
    resp = api_client.post(SESSION_URL, {"token": str(qr_token)}, format="json")
    assert resp.status_code == status.HTTP_200_OK, (
        f"Session creation failed: {resp.data}"
    )
    return resp


# ===========================================================================
# TC-17-01 through TC-17-11
# ===========================================================================

class TestCustomerOrderPlacement:
    """POST /api/v1/customer/orders/ — order placement endpoint tests."""

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_valid_order_returns_201_and_creates_order(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-17-01: POST with valid session and available item → 201.
        Order is persisted with status=confirmed; OrderItem is created.

        Requirements: 14.7, 14.8
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 2},
            ]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {response.status_code}: {response.data}"
        )

        data = response.data
        assert data["status"] == "confirmed"
        assert "order_number" in data
        assert data["order_number"].startswith("BR")

        # Verify order is persisted in the DB
        assert Order.objects.filter(id=data["id"]).exists(), (
            "Order must be persisted in the database"
        )

        # Verify OrderItem was created
        order = Order.objects.get(id=data["id"])
        assert order.items.count() == 1
        item = order.items.first()
        assert str(item.menu_item_id) == str(available_item.id)
        assert item.quantity == 2

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_unit_price_snapshotted_from_menu_item_price(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-17-02: unit_price on OrderItem must equal MenuItem.price at placement time.
        Subsequent price changes to MenuItem must NOT affect the stored snapshot.

        Requirement: 14.8
        """
        _establish_session(api_client, active_qr.token)

        original_price = available_item.price  # Decimal("180.00")

        payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=response.data["id"])
        order_item = order.items.first()

        # unit_price must equal the price at placement time
        assert order_item.unit_price == original_price, (
            f"unit_price {order_item.unit_price} must match MenuItem.price {original_price}"
        )

        # Changing the MenuItem price after placement must not affect order_item
        available_item.price = Decimal("999.00")
        available_item.save()
        order_item.refresh_from_db()
        assert order_item.unit_price == original_price, (
            "unit_price must remain unchanged after MenuItem price update (price immutability)"
        )

    @pytest.mark.django_db
    def test_unavailable_item_returns_422_item_unavailable(
        self, api_client, active_qr, unavailable_item
    ):
        """
        TC-17-03: Ordering an item with status='unavailable' → 422 ITEM_UNAVAILABLE.

        Requirement: 14.7, 14.11
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(unavailable_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")

        assert response.status_code == 422, (
            f"Expected 422, got {response.status_code}: {response.data}"
        )
        assert response.data.get("error") == "ITEM_UNAVAILABLE", (
            f"Expected error=ITEM_UNAVAILABLE, got: {response.data}"
        )

    @pytest.mark.django_db
    def test_archived_item_returns_422_item_unavailable(
        self, api_client, active_qr, archived_item
    ):
        """
        TC-17-04: Ordering an archived item (is_archived=True) → 422 ITEM_UNAVAILABLE.

        Requirement: 14.7, 14.11
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(archived_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")

        assert response.status_code == 422, (
            f"Expected 422 for archived item, got {response.status_code}: {response.data}"
        )
        assert response.data.get("error") == "ITEM_UNAVAILABLE"

    @pytest.mark.django_db
    def test_no_session_returns_403(self, api_client, available_item):
        """
        TC-17-05: POST without an active session → 403 Forbidden.

        Requirement: 4.2 (IsCustomerSession)
        """
        payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), f"Expected 401/403, got {response.status_code}: {response.data}"

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_optional_customer_info_stored_not_required(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-17-06: customer_name and customer_phone are optional.
        When provided, they are stored on the Order.
        When omitted, order still succeeds.

        Requirement: 14.9
        """
        _establish_session(api_client, active_qr.token)

        # With customer info
        payload_with_info = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}],
            "customer_name": "Abebe Girma",
            "customer_phone": "+251911000000",
        }
        resp = api_client.post(ORDERS_URL, payload_with_info, format="json")
        assert resp.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=resp.data["id"])
        assert order.customer_name == "Abebe Girma"
        assert order.customer_phone == "+251911000000"

        # Without customer info — must also succeed
        _establish_session(api_client, active_qr.token)  # Re-create session with same QR
        payload_without_info = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}],
        }
        resp2 = api_client.post(ORDERS_URL, payload_without_info, format="json")
        assert resp2.status_code == status.HTTP_201_CREATED

        order2 = Order.objects.get(id=resp2.data["id"])
        assert order2.customer_name == ""
        assert order2.customer_phone == ""

    @pytest.mark.django_db
    def test_missing_items_returns_400(self, api_client, active_qr):
        """
        TC-17-07: POST without the items list → 400 validation error.
        """
        _establish_session(api_client, active_qr.token)

        response = api_client.post(ORDERS_URL, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400 for missing items, got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_zero_quantity_returns_400(self, api_client, active_qr, available_item):
        """
        TC-17-08: POST with quantity=0 → 400 (min_value=1 on quantity field).
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 0}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400 for zero quantity, got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_item_from_different_branch_returns_422(
        self, api_client, active_qr, other_branch_item
    ):
        """
        TC-17-09: POST with a menu_item_id from a different branch → 422 ITEM_UNAVAILABLE.
        Items are scoped to the session's branch.

        Requirements: 14.7
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(other_branch_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")

        assert response.status_code == 422, (
            f"Expected 422 for cross-branch item, got {response.status_code}: {response.data}"
        )
        assert response.data.get("error") == "ITEM_UNAVAILABLE"

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_total_amount_computed_correctly_for_multiple_items(
        self, mock_notify, api_client, active_qr, available_item, available_item2
    ):
        """
        TC-17-10: total_amount = sum(unit_price × quantity) for all items.

        Requirements: 14.8
        """
        _establish_session(api_client, active_qr.token)

        # available_item: 180.00 × 2 = 360.00
        # available_item2: 30.00 × 3 = 90.00
        # total: 450.00
        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 2},
                {"menu_item_id": str(available_item2.id), "quantity": 3},
            ]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {response.status_code}: {response.data}"
        )

        data = response.data
        expected_total = Decimal("180.00") * 2 + Decimal("30.00") * 3  # 450.00
        assert Decimal(str(data["total_amount"])) == expected_total, (
            f"total_amount {data['total_amount']} != expected {expected_total}"
        )

        order = Order.objects.get(id=data["id"])
        assert order.total_amount == expected_total
        assert order.items.count() == 2

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_special_instructions_stored_per_item(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-17-11: special_instructions are stored on the OrderItem.

        Requirement: 14.7
        """
        _establish_session(api_client, active_qr.token)

        instructions = "No onions, extra spicy"
        payload = {
            "items": [
                {
                    "menu_item_id": str(available_item.id),
                    "quantity": 1,
                    "special_instructions": instructions,
                }
            ]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        order = Order.objects.get(id=response.data["id"])
        order_item = order.items.first()
        assert order_item.special_instructions == instructions, (
            f"special_instructions mismatch: {order_item.special_instructions!r}"
        )

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_send_order_notification_enqueued(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        Notification task is enqueued after successful order placement.

        Requirement: 17.1
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # Verify the Celery task was enqueued with the created order's ID
        mock_notify.assert_called_once_with(response.data["id"])

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_response_contains_table_number(
        self, mock_notify, api_client, active_qr, available_item, table
    ):
        """
        Response includes table_number from the session's table.
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
        }
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        assert response.data.get("table_number") == table.number, (
            f"Expected table_number={table.number!r}, got {response.data.get('table_number')!r}"
        )

    @pytest.mark.django_db
    def test_empty_items_list_returns_400(self, api_client, active_qr):
        """
        POST with an empty items list → 400 (min_length=1 validation).
        """
        _establish_session(api_client, active_qr.token)

        payload = {"items": []}
        response = api_client.post(ORDERS_URL, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400 for empty items list, got {response.status_code}: {response.data}"
        )
