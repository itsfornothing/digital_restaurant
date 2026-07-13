"""
apps/orders/tests/test_order_api.py

Order placement and WebSocket notification tests (Task 17.6).

Test cases:
  TC-O01: POST with qty=1  → order total = unit_price × 1              (Req 14.7, 14.8)
  TC-O02: POST with qty=3  → order total = unit_price × 3              (Req 14.8)
  TC-O03: POST with multiple items, some qty=1, some qty=2             (Req 14.8)
  TC-O04: special_instructions saved exactly as typed                  (Req 14.7)
  TC-O05: POST without customer_name/phone → 201, not blocked          (Req 14.9)
  TC-O06: POST order → send_order_notification.delay called w/ order_id (Req 17.1)
  TC-O07: Order response includes correct table number                  (Req 14.8)
  TC-O08: PATCH /api/v1/orders/{id}/status/ to preparing → task/WS triggered (Req 10.3, 17.2)
  TC-API06: GET /api/v1/branches/{id}/orders/ as Kitchen_Staff → 200, own branch only (Req 10.3)
  TC-API07: PATCH /api/v1/orders/{id}/status/ by Kitchen Staff → 200   (Req 10.3)
  TC-API08: DELETE /api/v1/orders/{id}/ → 405                          (Req 10.3)

Requirements: 10.3, 14.7, 14.8, 14.9, 17.1, 17.2
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import User, UserRole
from apps.branches.models import Branch, Table
from apps.menus.models import MenuItem
from apps.orders.models import Order, OrderItem
from apps.qr.models import QRCode

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

SESSION_URL = "/api/v1/customer/session/"
CUSTOMER_ORDERS_URL = "/api/v1/customer/orders/"
ORDERS_URL = "/api/v1/orders/"


def order_status_url(order_id):
    return f"/api/v1/orders/{order_id}/status/"


def branch_orders_url(branch_id):
    return f"/api/v1/branches/{branch_id}/orders/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Main Test Branch",
        address="1 Test Street, Addis Ababa",
        phone="0911000001",
        email="main@testbranch.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="99 Other Street, Addis Ababa",
        phone="0911000002",
        email="other@testbranch.com",
    )


@pytest.fixture
def table(db, branch):
    return Table.objects.create(branch=branch, number="5", seat_count=4)


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
    """A single available menu item priced at 150.00 ETB."""
    return MenuItem.objects.create(
        branch=branch,
        name="Tibs",
        description="Sautéed beef",
        price=Decimal("150.00"),
        prep_time_minutes=20,
        status="available",
        is_archived=False,
        dietary_tags=["halal"],
    )


@pytest.fixture
def available_item2(db, branch):
    """A second available menu item priced at 50.00 ETB."""
    return MenuItem.objects.create(
        branch=branch,
        name="Injera",
        description="Fermented flatbread",
        price=Decimal("50.00"),
        prep_time_minutes=5,
        status="available",
        is_archived=False,
        dietary_tags=["vegan", "vegetarian"],
    )


@pytest.fixture
def kitchen_staff(db, branch):
    """A Kitchen_Staff user assigned to *branch*."""
    return User.objects.create_user(
        email="kitchen@testbranch.com",
        password="Passw0rd!",
        role=UserRole.KITCHEN_STAFF,
        branch=branch,
    )


@pytest.fixture
def other_kitchen_staff(db, other_branch):
    """A Kitchen_Staff user assigned to *other_branch*."""
    return User.objects.create_user(
        email="kitchen2@otherbranch.com",
        password="Passw0rd!",
        role=UserRole.KITCHEN_STAFF,
        branch=other_branch,
    )


@pytest.fixture
def receptionist(db, branch):
    """A Receptionist user assigned to *branch*."""
    return User.objects.create_user(
        email="reception@testbranch.com",
        password="Passw0rd!",
        role=UserRole.RECEPTIONIST,
        branch=branch,
    )


@pytest.fixture
def placed_order(db, branch, table, available_item):
    """
    A pre-existing confirmed Order for use in status-update tests.

    The order has one OrderItem with quantity=2.
    """
    order = Order.objects.create(
        branch=branch,
        table=table,
        status="confirmed",
        total_amount=available_item.price * 2,
    )
    OrderItem.objects.create(
        order=order,
        menu_item=available_item,
        quantity=2,
        unit_price=available_item.price,
    )
    return order


# ---------------------------------------------------------------------------
# Helper: establish a customer session via QR scan
# ---------------------------------------------------------------------------

def _establish_session(api_client, qr_token):
    """POST /api/v1/customer/session/ and assert success. Returns response."""
    resp = api_client.post(SESSION_URL, {"token": str(qr_token)}, format="json")
    assert resp.status_code == status.HTTP_200_OK, (
        f"Session creation failed ({resp.status_code}): {resp.data}"
    )
    return resp


# ===========================================================================
# TC-O01 – TC-O08: Order placement and notification tests
# ===========================================================================


class TestOrderPlacement:
    """
    TC-O01 – TC-O07: Order creation via customer API.

    All tests in this class place orders via POST /api/v1/customer/orders/
    after establishing a customer session via the QR scan endpoint.
    """

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o01_single_item_qty1_total_equals_unit_price(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-O01: POST a single item with qty=1 → total_amount = unit_price × 1.

        Requirements: 14.7, 14.8
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 1}
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {response.status_code}: {response.data}"
        )

        expected_total = available_item.price * 1  # 150.00 × 1 = 150.00
        actual_total = Decimal(str(response.data["total_amount"]))
        assert actual_total == expected_total, (
            f"TC-O01: total_amount {actual_total} != unit_price × 1 ({expected_total})"
        )

        # Verify DB record
        order = Order.objects.get(id=response.data["id"])
        assert order.total_amount == expected_total
        item = order.items.first()
        assert item.quantity == 1
        assert item.unit_price == available_item.price

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o02_single_item_qty3_total_equals_price_times_3(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-O02: POST a single item with qty=3 → total_amount = unit_price × 3.

        Requirement: 14.8
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 3}
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {response.status_code}: {response.data}"
        )

        expected_total = available_item.price * 3  # 150.00 × 3 = 450.00
        actual_total = Decimal(str(response.data["total_amount"]))
        assert actual_total == expected_total, (
            f"TC-O02: total_amount {actual_total} != unit_price × 3 ({expected_total})"
        )

        order = Order.objects.get(id=response.data["id"])
        assert order.total_amount == expected_total
        assert order.items.first().quantity == 3

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o03_multiple_items_totals_correct_per_item(
        self, mock_notify, api_client, active_qr, available_item, available_item2
    ):
        """
        TC-O03: POST with multiple items (some qty=1, some qty=2) →
        totals are correct per item; order total = sum of all line totals.

        item1 (Tibs, 150.00) × 1 = 150.00
        item2 (Injera, 50.00) × 2 = 100.00
        total = 250.00

        Requirement: 14.8
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 1},
                {"menu_item_id": str(available_item2.id), "quantity": 2},
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {response.status_code}: {response.data}"
        )

        expected_total = (available_item.price * 1) + (available_item2.price * 2)
        actual_total = Decimal(str(response.data["total_amount"]))
        assert actual_total == expected_total, (
            f"TC-O03: total_amount {actual_total} != expected {expected_total}"
        )

        order = Order.objects.get(id=response.data["id"])
        assert order.total_amount == expected_total
        assert order.items.count() == 2

        # Verify per-item unit prices are correct snapshots
        items_by_menu_id = {
            str(oi.menu_item_id): oi for oi in order.items.all()
        }
        assert items_by_menu_id[str(available_item.id)].unit_price == available_item.price
        assert items_by_menu_id[str(available_item2.id)].unit_price == available_item2.price

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o04_special_instructions_saved_exactly(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-O04: special_instructions are saved exactly as typed and appear
        in the order item.

        Requirement: 14.7
        """
        _establish_session(api_client, active_qr.token)

        instructions = "No onions, extra spicy, allergen: nuts"
        payload = {
            "items": [
                {
                    "menu_item_id": str(available_item.id),
                    "quantity": 1,
                    "special_instructions": instructions,
                }
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"TC-O04: Expected 201, got {response.status_code}: {response.data}"
        )

        order = Order.objects.get(id=response.data["id"])
        order_item = order.items.first()

        assert order_item.special_instructions == instructions, (
            f"TC-O04: special_instructions stored as {order_item.special_instructions!r}, "
            f"expected {instructions!r}"
        )

        # Also verify the value is echoed in the API response items list
        resp_items = response.data.get("items", [])
        assert any(
            item.get("special_instructions") == instructions
            for item in resp_items
        ), "TC-O04: special_instructions not present in response items"

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o05_order_without_customer_name_or_phone_returns_201(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-O05: POST order without customer_name / customer_phone → 201.
        The endpoint must NOT block orders without contact info.

        Requirement: 14.9
        """
        _establish_session(api_client, active_qr.token)

        # Omit both optional fields entirely
        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 1}
            ]
            # no customer_name, no customer_phone
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"TC-O05: Expected 201 without customer info, "
            f"got {response.status_code}: {response.data}"
        )

        order = Order.objects.get(id=response.data["id"])
        # Both fields default to empty string (never None)
        assert order.customer_name == ""
        assert order.customer_phone == ""

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o06_send_order_notification_called_with_order_id(
        self, mock_notify, api_client, active_qr, available_item
    ):
        """
        TC-O06: After a successful order placement, send_order_notification.delay
        must be called exactly once with the new order's ID (WebSocket push to kitchen).

        Requirement: 17.1
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 1}
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"TC-O06: Expected 201, got {response.status_code}: {response.data}"
        )

        mock_notify.assert_called_once_with(response.data["id"]), (
            f"TC-O06: send_order_notification.delay not called with order id. "
            f"Calls: {mock_notify.call_args_list}"
        )

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_o07_response_contains_correct_table_number(
        self, mock_notify, api_client, active_qr, available_item, table
    ):
        """
        TC-O07: The order placement response must include the correct
        table number from the customer's session.

        Requirement: 14.8
        """
        _establish_session(api_client, active_qr.token)

        payload = {
            "items": [
                {"menu_item_id": str(available_item.id), "quantity": 1}
            ]
        }
        response = api_client.post(CUSTOMER_ORDERS_URL, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED, (
            f"TC-O07: Expected 201, got {response.status_code}: {response.data}"
        )

        assert response.data.get("table_number") == table.number, (
            f"TC-O07: Expected table_number={table.number!r}, "
            f"got {response.data.get('table_number')!r}"
        )

        # Also verify from DB
        order = Order.objects.get(id=response.data["id"])
        assert str(order.table_id) == str(table.id)
        assert order.table.number == table.number


# ===========================================================================
# TC-O08: Status update triggers task/WebSocket notification
# ===========================================================================


class TestOrderStatusUpdate:
    """
    TC-O08: PATCH /api/v1/orders/{id}/status/ to 'preparing' triggers
    the deduct_inventory Celery task (and by extension the WebSocket
    notification chain).

    TC-API07: Kitchen Staff can PATCH order status → 200.

    Requirements: 10.3, 17.2
    """

    @pytest.mark.django_db
    @patch("apps.inventory.tasks.deduct_inventory.delay")
    def test_tc_o08_status_update_to_preparing_triggers_task(
        self, mock_deduct, api_client, placed_order, receptionist
    ):
        """
        TC-O08: PATCH /api/v1/orders/{id}/status/ with {"status": "preparing"}
        after transitioning through received → deduct_inventory.delay is called
        with the order's ID.

        The deduct_inventory task is the mechanism that triggers inventory
        deduction and downstream WebSocket notifications.

        Requirements: 10.3, 17.2
        """
        # Receptionist has update_status permission
        api_client.force_authenticate(user=receptionist)

        # First move: confirmed → received (required by state machine)
        url = order_status_url(placed_order.id)
        resp1 = api_client.patch(url, {"status": "received"}, format="json")
        assert resp1.status_code == status.HTTP_200_OK, (
            f"TC-O08: Step 1 (confirmed→received) failed: {resp1.data}"
        )

        # Second move: received → preparing
        resp2 = api_client.patch(url, {"status": "preparing"}, format="json")
        assert resp2.status_code == status.HTTP_200_OK, (
            f"TC-O08: Step 2 (received→preparing) failed: {resp2.data}"
        )
        assert resp2.data["status"] == "preparing"

        # Verify the Celery task was enqueued
        mock_deduct.assert_called_once_with(str(placed_order.id))

    @pytest.mark.django_db
    @patch("apps.inventory.tasks.deduct_inventory.delay")
    def test_tc_api07_kitchen_staff_can_patch_order_status(
        self, mock_deduct, api_client, placed_order, kitchen_staff
    ):
        """
        TC-API07: PATCH /api/v1/orders/{id}/status/ by Kitchen Staff → 200.

        Kitchen_Staff must be allowed to advance the order state machine
        (e.g. confirmed → received).

        Requirement: 10.3
        """
        api_client.force_authenticate(user=kitchen_staff)

        url = order_status_url(placed_order.id)
        response = api_client.patch(url, {"status": "received"}, format="json")

        assert response.status_code == status.HTTP_200_OK, (
            f"TC-API07: Kitchen Staff PATCH status failed "
            f"({response.status_code}): {response.data}"
        )
        assert response.data["status"] == "received"


# ===========================================================================
# TC-API06: GET /api/v1/branches/{id}/orders/ as Kitchen_Staff
# ===========================================================================


class TestBranchOrdersEndpoint:
    """
    TC-API06: GET /api/v1/branches/{id}/orders/ as Kitchen_Staff
    → 200, own branch orders only.

    Requirement: 10.3
    """

    @pytest.mark.django_db
    @patch("apps.notifications.tasks.send_order_notification.delay")
    def test_tc_api06_kitchen_staff_can_list_own_branch_orders(
        self, mock_notify, api_client, branch, table, available_item,
        active_qr, kitchen_staff
    ):
        """
        TC-API06: Kitchen_Staff GET /api/v1/branches/{branch_id}/orders/ → 200.
        Response contains only orders for the kitchen staff's own branch.

        Requirement: 10.3
        """
        # Place an order for this branch via customer session
        _establish_session(api_client, active_qr.token)
        order_payload = {
            "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
        }
        order_resp = api_client.post(CUSTOMER_ORDERS_URL, order_payload, format="json")
        assert order_resp.status_code == status.HTTP_201_CREATED

        created_order_id = order_resp.data["id"]

        # Now list orders as Kitchen_Staff
        api_client.force_authenticate(user=kitchen_staff)
        url = branch_orders_url(branch.id)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK, (
            f"TC-API06: Expected 200, got {response.status_code}: {response.data}"
        )

        # The created order must appear in the results
        order_ids = [str(o["id"]) for o in response.data]
        assert str(created_order_id) in order_ids, (
            f"TC-API06: Created order {created_order_id} not in Kitchen Staff order list"
        )

        # All returned orders must belong to the kitchen staff's branch
        for order_data in response.data:
            assert str(order_data["branch"]) == str(branch.id), (
                f"TC-API06: Order {order_data['id']} belongs to branch "
                f"{order_data['branch']!r}, expected {branch.id!r}"
            )

    @pytest.mark.django_db
    def test_tc_api06_kitchen_staff_cannot_see_other_branch_orders(
        self, api_client, other_branch, other_kitchen_staff, branch,
        table, available_item, active_qr
    ):
        """
        TC-API06 (scope): Kitchen_Staff from other_branch trying to access
        main branch orders gets an empty list (branch scope enforced).

        Requirement: 10.3
        """
        # Create an order in the main branch
        _establish_session(api_client, active_qr.token)
        with patch("apps.notifications.tasks.send_order_notification.delay"):
            order_payload = {
                "items": [{"menu_item_id": str(available_item.id), "quantity": 1}]
            }
            api_client.post(CUSTOMER_ORDERS_URL, order_payload, format="json")

        # Kitchen Staff from other_branch requests main branch orders
        api_client.force_authenticate(user=other_kitchen_staff)
        url = branch_orders_url(branch.id)
        response = api_client.get(url)

        # Should still return 200 but with an empty list (scope enforcement)
        assert response.status_code == status.HTTP_200_OK, (
            f"TC-API06 scope: Expected 200 (empty), got {response.status_code}: {response.data}"
        )
        assert response.data == [], (
            f"TC-API06 scope: Other-branch Kitchen_Staff should see no orders, "
            f"got: {response.data}"
        )


# ===========================================================================
# TC-API08: DELETE /api/v1/orders/{id}/ → 405
# ===========================================================================


class TestOrderDeleteForbidden:
    """
    TC-API08: DELETE /api/v1/orders/{id}/ → 405 Method Not Allowed.
    Orders are immutable records; deletion is never permitted.

    Requirement: 10.3
    """

    @pytest.mark.django_db
    def test_tc_api08_delete_order_returns_405(
        self, api_client, placed_order, receptionist
    ):
        """
        TC-API08: DELETE /api/v1/orders/{id}/ → 405 Method Not Allowed.

        The OrderViewSet does not register the delete action so attempting
        DELETE must be rejected with 405 regardless of role.

        Requirement: 10.3
        """
        api_client.force_authenticate(user=receptionist)
        url = f"{ORDERS_URL}{placed_order.id}/"
        response = api_client.delete(url)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-API08: Expected 405 for DELETE order, "
            f"got {response.status_code}: {response.data}"
        )

    @pytest.mark.django_db
    def test_tc_api08_delete_unauthenticated_returns_405_or_403(
        self, api_client, placed_order
    ):
        """
        TC-API08 (unauthenticated): DELETE without auth should also fail.
        Router does not expose DELETE so either 405 (route not found) or
        403/401 (auth first) are acceptable, but 200/204 must never occur.

        Requirement: 10.3
        """
        url = f"{ORDERS_URL}{placed_order.id}/"
        response = api_client.delete(url)

        assert response.status_code not in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        ), (
            f"TC-API08: DELETE must never succeed, got {response.status_code}"
        )
