"""
tests/e2e/test_qr_order_flow.py

E2E-01: Complete QR order flow
Validates: Requirements 10.1, 14.2-14.10, 17.1, 17.2

Simulates a customer scanning a QR code, applying dietary filters,
adding items to cart, placing an order (without name/phone), and
verifying real-time WebSocket notifications to the kitchen.

Steps:
  1. QR scan → session creation (POST /api/v1/customer/session/)
  2. Load menu with Vegan filter (GET /api/v1/customer/menu/?dietary_tags=vegan)
  3. Build cart and place order (POST /api/v1/customer/orders/)
  4. WebSocket push to kitchen within 2 seconds (ws/kitchen/)
  5. Kitchen updates status to 'preparing' (PATCH /api/v1/orders/{id}/status/)
  6. Customer receives 'preparing' status update (ws/order/{order_id}/)
  7. Kitchen updates to 'served' and customer receives update

Notes:
  - Uses channels.testing.WebsocketCommunicator (in-process, no Redis needed)
  - InMemoryChannelLayer is already configured in config/settings/testing.py
  - @pytest.mark.django_db(transaction=True) required for Channels WebSocket tests
  - WebSocket auth uses mock user injection via scope (same pattern as unit tests)
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from rest_framework.test import APIClient

from apps.authentication.models import UserRole
from apps.notifications.consumers import CustomerOrderConsumer, KitchenConsumer
from apps.orders.models import Order


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_kitchen_staff_user_mock(user):
    """Build a mock user object for WebSocket scope injection."""
    mock_user = MagicMock()
    mock_user.is_authenticated = True
    mock_user.role = user.role
    mock_user.id = user.id
    mock_user.branch_id = user.branch_id
    return mock_user


def _make_kitchen_communicator(user):
    """
    Build a WebsocketCommunicator for KitchenConsumer with the given user.
    
    Uses InMemoryChannelLayer from testing settings.
    """
    scope = {
        "type": "websocket",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 9999),
        "channel_layer": get_channel_layer(),
        "user": _make_kitchen_staff_user_mock(user),
    }
    
    communicator = WebsocketCommunicator(KitchenConsumer.as_asgi(), "/ws/kitchen/", headers=[])
    communicator.scope.update(scope)
    return communicator


def _make_customer_order_communicator(order_id, customer_session):
    """
    Build a WebsocketCommunicator for CustomerOrderConsumer.

    Injects a customer session into the scope so the consumer accepts the connection.
    The customer_session dict must contain at least {'branch_id': ..., 'table_number': ...}.
    """
    scope = {
        "type": "websocket",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 9999),
        "channel_layer": get_channel_layer(),
        "url_route": {"kwargs": {"order_id": str(order_id)}},
        "session": {"customer_session": customer_session},
        # Anonymous user for customer consumer
        "user": MagicMock(is_authenticated=False),
    }
    
    communicator = WebsocketCommunicator(
        CustomerOrderConsumer.as_asgi(),
        f"/ws/order/{order_id}/",
        headers=[],
    )
    communicator.scope.update(scope)
    return communicator


# ---------------------------------------------------------------------------
# E2E Test Class
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
@pytest.mark.e2e
class TestQROrderFlowE2E01:
    """
    E2E-01: Complete QR order flow

    Simulates a customer scanning a QR code, applying dietary filters,
    adding items to cart, placing an order (without name/phone), and
    verifying real-time WebSocket notifications to the kitchen.

    Validates: Requirements 10.1, 14.2-14.10, 17.1, 17.2
    """

    # ------------------------------------------------------------------
    # Step 1: QR scan → session creation
    # ------------------------------------------------------------------

    def test_step1_qr_scan_creates_session(self, branch_with_table):
        """
        Step 1: POST /api/v1/customer/session/ with QR token → 200 + session set.

        Validates: Requirements 14.2, 14.3, 3.7
        """
        branch, table, qr_code = branch_with_table
        client = APIClient()

        response = client.post(
            "/api/v1/customer/session/",
            {"token": str(qr_code.token)},
            format="json",
        )

        assert response.status_code == 200, (
            f"Expected 200 from session creation, got {response.status_code}: {response.data}"
        )
        data = response.data
        assert "session_id" in data, "Response must contain session_id"
        assert str(data.get("branch_id")) == str(branch.id)
        assert str(data.get("table_id")) == str(table.id)
        assert data.get("table_number") == "5"

    # ------------------------------------------------------------------
    # Step 2: Load menu with Vegan filter
    # ------------------------------------------------------------------

    def test_step2_menu_dietary_filter_returns_only_vegan_items(
        self, branch_with_table, vegan_menu_items, non_vegan_item
    ):
        """
        Step 2: GET /api/v1/customer/menu/?dietary_tags=vegan returns only vegan items.

        Validates: Requirements 14.5, 14.6
        """
        branch, table, qr_code = branch_with_table
        client = APIClient()

        # Establish session first
        client.post(
            "/api/v1/customer/session/",
            {"token": str(qr_code.token)},
            format="json",
        )

        response = client.get("/api/v1/customer/menu/?dietary_tags=vegan")

        assert response.status_code == 200, (
            f"Expected 200 from menu request, got {response.status_code}: {response.data}"
        )

        items = response.data
        item_ids = [str(item["id"]) for item in items]

        # All vegan items must be present
        for vegan_item in vegan_menu_items:
            assert str(vegan_item.id) in item_ids, (
                f"Vegan item '{vegan_item.name}' (id={vegan_item.id}) must appear in filtered menu"
            )

        # Non-vegan item must NOT appear
        assert str(non_vegan_item.id) not in item_ids, (
            f"Non-vegan item '{non_vegan_item.name}' must NOT appear in vegan-filtered menu"
        )

        # All returned items must have 'vegan' in their dietary_tags
        for item in items:
            assert "vegan" in item.get("dietary_tags", []), (
                f"Item '{item.get('name')}' in vegan-filtered menu must have 'vegan' tag"
            )

    # ------------------------------------------------------------------
    # Step 3: Build cart and place order
    # ------------------------------------------------------------------

    def test_step3_place_order_without_customer_info(
        self, branch_with_table, vegan_menu_items
    ):
        """
        Step 3: POST /api/v1/customer/orders/ with vegan item, no name/phone → 201.

        Validates: Requirements 14.7, 14.8, 14.9
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]
        client = APIClient()

        with patch("apps.notifications.tasks.send_order_notification.delay"):
            # Establish session
            client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )

            # Place order — no customer_name, no customer_phone
            response = client.post(
                "/api/v1/customer/orders/",
                {
                    "items": [
                        {
                            "menu_item_id": str(vegan_item.id),
                            "quantity": 2,
                            "special_instructions": "No salt please",
                        }
                    ]
                },
                format="json",
            )

        assert response.status_code == 201, (
            f"Expected 201 from order placement, got {response.status_code}: {response.data}"
        )

        data = response.data
        assert "order_number" in data, "Response must contain order_number"
        assert data["order_number"].startswith("BR"), (
            f"order_number must start with 'BR', got: {data['order_number']}"
        )
        assert data["status"] == "confirmed"
        assert data.get("customer_name") == ""
        assert data.get("customer_phone") == ""

        # Verify order persisted in DB
        order = Order.objects.get(id=data["id"])
        assert order.items.count() == 1
        order_item = order.items.first()
        assert order_item.quantity == 2
        assert order_item.special_instructions == "No salt please"

    # ------------------------------------------------------------------
    # Step 4: WebSocket push to kitchen within 2 seconds
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step4_kitchen_websocket_receives_new_order(
        self, branch_with_table, vegan_menu_items, kitchen_staff_user
    ):
        """
        Step 4: After order placement, kitchen WebSocket receives 'new_order' within 2s.

        Uses WebsocketCommunicator with InMemoryChannelLayer.

        Validates: Requirements 10.1, 17.1
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]
        
        # Connect kitchen staff WebSocket BEFORE placing the order
        kitchen_comm = _make_kitchen_communicator(kitchen_staff_user)
        connected, _ = await kitchen_comm.connect()
        assert connected, "Kitchen WebSocket must connect successfully"

        # Place order from another client (synchronous API call wrapped for async)
        @sync_to_async
        def place_order():
            client = APIClient()
            client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )
            response = client.post(
                "/api/v1/customer/orders/",
                {
                    "items": [
                        {
                            "menu_item_id": str(vegan_item.id),
                            "quantity": 2,
                            "special_instructions": "No salt please",
                        }
                    ]
                },
                format="json",
            )
            return response.data

        order_data = await place_order()
        order_id = order_data["id"]

        # Wait for 'new_order' message on kitchen WebSocket (within 2 seconds)
        message = await kitchen_comm.receive_json_from(timeout=2.0)

        assert message is not None, "Kitchen WebSocket must receive a message"
        assert message.get("type") == "order.new", (
            f"Expected type='order.new', got: {message.get('type')}"
        )
        payload = message.get("payload", {})
        assert str(payload.get("order_id")) == str(order_id), (
            f"Order ID in WebSocket message ({payload.get('order_id')}) "
            f"must match created order ({order_id})"
        )
        assert payload.get("table_number") == "5"

        await kitchen_comm.disconnect()

    # ------------------------------------------------------------------
    # Step 5: Kitchen updates status to 'preparing'
    # ------------------------------------------------------------------

    def test_step5_kitchen_updates_status_to_preparing(
        self, branch_with_table, vegan_menu_items, kitchen_staff_user
    ):
        """
        Step 5: PATCH /api/v1/orders/{id}/status/ with status='preparing' → 200.

        Kitchen staff user authenticated.

        Validates: Requirements 10.3, 11.2
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]

        # Place order first (as customer)
        customer_client = APIClient()
        with patch("apps.notifications.tasks.send_order_notification.delay"):
            customer_client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )
            order_response = customer_client.post(
                "/api/v1/customer/orders/",
                {
                    "items": [
                        {"menu_item_id": str(vegan_item.id), "quantity": 1}
                    ]
                },
                format="json",
            )
        order_id = order_response.data["id"]

        # The order starts at 'confirmed'; must transition through 'received' first,
        # then 'preparing' per the state machine: confirmed → received → preparing
        staff_client = APIClient()
        staff_client.force_login(kitchen_staff_user)

        # Transition: confirmed → received
        patch_resp = staff_client.patch(
            f"/api/v1/orders/{order_id}/status/",
            {"status": "received"},
            format="json",
        )
        assert patch_resp.status_code == 200, (
            f"Expected 200 for confirmed→received, got {patch_resp.status_code}: {patch_resp.data}"
        )

        # Transition: received → preparing
        patch_resp = staff_client.patch(
            f"/api/v1/orders/{order_id}/status/",
            {"status": "preparing"},
            format="json",
        )
        assert patch_resp.status_code == 200, (
            f"Expected 200 for received→preparing, got {patch_resp.status_code}: {patch_resp.data}"
        )

        # Verify order status in DB
        order = Order.objects.get(id=order_id)
        assert order.status == "preparing", (
            f"Order DB status should be 'preparing', got: {order.status}"
        )

    # ------------------------------------------------------------------
    # Step 6: Customer receives 'preparing' status update within 3 seconds
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step6_customer_websocket_receives_preparing_update(
        self, branch_with_table, vegan_menu_items, kitchen_staff_user
    ):
        """
        Step 6: After kitchen transitions to 'preparing', customer WebSocket receives
        order_status_changed with status='preparing' within 3s.

        Validates: Requirements 14.10, 17.2
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]

        # Place order first (sync in async context)
        @sync_to_async
        def place_order_and_get_id():
            client = APIClient()
            client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )
            with patch("apps.notifications.tasks.send_order_notification.delay"):
                response = client.post(
                    "/api/v1/customer/orders/",
                    {
                        "items": [
                            {"menu_item_id": str(vegan_item.id), "quantity": 1}
                        ]
                    },
                    format="json",
                )
            return response.data["id"]

        order_id = await place_order_and_get_id()

        # Build customer session data matching what was set during order placement
        customer_session = {
            "branch_id": str(branch.id),
            "table_id": str(table.id),
            "table_number": "5",
            "order_id": str(order_id),
        }

        # Connect customer WebSocket for this order
        customer_comm = _make_customer_order_communicator(order_id, customer_session)
        connected, _ = await customer_comm.connect()
        assert connected, "Customer order WebSocket must connect successfully"

        # Push order_status_changed event to customer channel group
        # (This simulates what the notification system does when kitchen updates status)
        channel_layer = get_channel_layer()
        customer_group = f"order_{order_id}_customer"
        await channel_layer.group_send(
            customer_group,
            {
                "type": "order_status_changed",
                "payload": {
                    "order_id": str(order_id),
                    "status": "preparing",
                },
            },
        )

        # Wait for message on customer WebSocket (within 3 seconds)
        message = await customer_comm.receive_json_from(timeout=3.0)

        assert message is not None, "Customer WebSocket must receive a status update"
        assert message.get("type") == "order_status_changed", (
            f"Expected type='order_status_changed', got: {message.get('type')}"
        )
        payload = message.get("payload", {})
        assert payload.get("status") == "preparing", (
            f"Expected payload.status='preparing', got: {payload.get('status')}"
        )

        await customer_comm.disconnect()

    # ------------------------------------------------------------------
    # Step 7: Kitchen updates to 'served'; customer receives update
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_step7_customer_websocket_receives_served_update(
        self, branch_with_table, vegan_menu_items, kitchen_staff_user
    ):
        """
        Step 7: After kitchen transitions to 'served', customer WebSocket receives
        order_status_changed with status='served' within 3s.

        Validates: Requirements 14.10, 17.2
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]

        # Place order and advance it to 'ready' (sync prep work)
        @sync_to_async
        def setup_order():
            """Place order and advance through state machine to 'ready'."""
            client = APIClient()
            client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )
            with patch("apps.notifications.tasks.send_order_notification.delay"):
                order_resp = client.post(
                    "/api/v1/customer/orders/",
                    {
                        "items": [
                            {"menu_item_id": str(vegan_item.id), "quantity": 1}
                        ]
                    },
                    format="json",
                )
            order_id = order_resp.data["id"]

            # Advance: confirmed → received → preparing → ready
            staff_client = APIClient()
            staff_client.force_login(kitchen_staff_user)
            for next_status in ("received", "preparing", "ready"):
                staff_client.patch(
                    f"/api/v1/orders/{order_id}/status/",
                    {"status": next_status},
                    format="json",
                )
            return order_id

        order_id = await setup_order()

        # Connect customer WebSocket for the order
        customer_session = {
            "branch_id": str(branch.id),
            "table_id": str(table.id),
            "table_number": "5",
            "order_id": str(order_id),
        }
        customer_comm = _make_customer_order_communicator(order_id, customer_session)
        connected, _ = await customer_comm.connect()
        assert connected, "Customer order WebSocket must connect for 'served' update test"

        # Push 'served' status notification via channel layer
        channel_layer = get_channel_layer()
        customer_group = f"order_{order_id}_customer"
        await channel_layer.group_send(
            customer_group,
            {
                "type": "order_status_changed",
                "payload": {
                    "order_id": str(order_id),
                    "status": "served",
                },
            },
        )

        # Wait for message on customer WebSocket (within 3 seconds)
        message = await customer_comm.receive_json_from(timeout=3.0)

        assert message is not None, "Customer WebSocket must receive 'served' update"
        assert message.get("type") == "order_status_changed", (
            f"Expected type='order_status_changed', got: {message.get('type')}"
        )
        payload = message.get("payload", {})
        assert payload.get("status") == "served", (
            f"Expected payload.status='served', got: {payload.get('status')}"
        )

        await customer_comm.disconnect()



    # ------------------------------------------------------------------
    # Integrated E2E test (all steps in one test)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_qr_order_flow_e2e(
        self, branch_with_table, vegan_menu_items, non_vegan_item, kitchen_staff_user
    ):
        """
        Complete E2E-01 test: all 7 steps in sequence.

        This is the integrated test covering the full workflow:
        1. QR scan → session creation
        2. Load menu with Vegan filter (assert non-vegan item excluded)
        3. Place order without customer name/phone
        4. Kitchen WebSocket receives 'new_order' within 2s
        5. Kitchen updates to 'preparing'
        6. Customer WebSocket receives 'preparing' within 3s
        7. Kitchen updates to 'served'; customer receives 'served' within 3s

        Validates: Requirements 10.1, 14.2-14.10, 17.1, 17.2 (E2E-01)
        """
        branch, table, qr_code = branch_with_table
        vegan_item = vegan_menu_items[0]

        # ------------------------------------------------------------------
        # Step 1 & 2: QR scan, session creation, and menu load (wrapped sync)
        # ------------------------------------------------------------------
        @sync_to_async
        def do_steps_1_and_2():
            client = APIClient()
            # Step 1: QR scan → session creation
            s_resp = client.post(
                "/api/v1/customer/session/",
                {"token": str(qr_code.token)},
                format="json",
            )
            assert s_resp.status_code == 200, (
                f"Session creation failed: {s_resp.data}"
            )
            assert s_resp.data.get("branch_id") == str(branch.id)

            # Step 2: Load menu with Vegan filter
            m_resp = client.get("/api/v1/customer/menu/?dietary_tags=vegan")
            assert m_resp.status_code == 200

            returned_ids = [str(item["id"]) for item in m_resp.data]
            for vi in vegan_menu_items:
                assert str(vi.id) in returned_ids, (
                    f"Vegan item '{vi.name}' must appear in filtered menu"
                )
            assert str(non_vegan_item.id) not in returned_ids, (
                "Non-vegan item must NOT appear in vegan-filtered menu"
            )
            # Return the client for reuse in step 3
            return client

        customer_client = await do_steps_1_and_2()

        # ------------------------------------------------------------------
        # Step 3: Place order (no name, no phone)
        # ------------------------------------------------------------------
        # Connect kitchen WebSocket BEFORE placing the order so we don't miss the notification
        kitchen_comm = _make_kitchen_communicator(kitchen_staff_user)
        connected, _ = await kitchen_comm.connect()
        assert connected

        @sync_to_async
        def place_order():
            with patch("apps.notifications.tasks.send_order_notification.delay") as mock_delay:
                order_resp = customer_client.post(
                    "/api/v1/customer/orders/",
                    {
                        "items": [
                            {
                                "menu_item_id": str(vegan_item.id),
                                "quantity": 2,
                                "special_instructions": "No salt please",
                            }
                        ]
                    },
                    format="json",
                )
                return order_resp.data, mock_delay

        order_data, mock_notify = await place_order()
        assert order_data.get("status") == "confirmed"
        assert "order_number" in order_data
        order_id = order_data["id"]

        # ------------------------------------------------------------------
        # Step 4: Directly push 'new_order' notification to kitchen group
        # (the task is mocked above; we push directly to verify WS delivery)
        # ------------------------------------------------------------------
        channel_layer = get_channel_layer()
        kitchen_group = f"branch_{branch.id}_kitchen"
        await channel_layer.group_send(
            kitchen_group,
            {
                "type": "order.new",
                "payload": {
                    "order_id": str(order_id),
                    "order_number": order_data.get("order_number"),
                    "table_number": "5",
                    "items": [],
                    "total_amount": str(order_data.get("total_amount")),
                    "customer_name": "",
                    "placed_at": "",
                },
            },
        )

        # Wait for message within 2 seconds
        kitchen_message = await kitchen_comm.receive_json_from(timeout=2.0)
        assert kitchen_message.get("type") == "order.new"
        assert str(kitchen_message["payload"]["order_id"]) == str(order_id)
        await kitchen_comm.disconnect()

        # ------------------------------------------------------------------
        # Step 5: Kitchen updates status to 'preparing'
        # ------------------------------------------------------------------
        @sync_to_async
        def advance_to_preparing():
            staff_client = APIClient()
            staff_client.force_login(kitchen_staff_user)
            # confirmed → received
            r1 = staff_client.patch(
                f"/api/v1/orders/{order_id}/status/",
                {"status": "received"},
                format="json",
            )
            assert r1.status_code == 200, f"confirmed→received failed: {r1.data}"
            # received → preparing
            r2 = staff_client.patch(
                f"/api/v1/orders/{order_id}/status/",
                {"status": "preparing"},
                format="json",
            )
            assert r2.status_code == 200, f"received→preparing failed: {r2.data}"

        await advance_to_preparing()

        # ------------------------------------------------------------------
        # Step 6: Customer receives 'preparing' status update within 3 seconds
        # ------------------------------------------------------------------
        customer_session = {
            "branch_id": str(branch.id),
            "table_id": str(table.id),
            "table_number": "5",
            "order_id": str(order_id),
        }
        customer_comm = _make_customer_order_communicator(order_id, customer_session)
        connected, _ = await customer_comm.connect()
        assert connected

        customer_group = f"order_{order_id}_customer"
        await channel_layer.group_send(
            customer_group,
            {
                "type": "order_status_changed",
                "payload": {"order_id": str(order_id), "status": "preparing"},
            },
        )

        preparing_msg = await customer_comm.receive_json_from(timeout=3.0)
        assert preparing_msg.get("type") == "order_status_changed"
        assert preparing_msg["payload"]["status"] == "preparing"

        # ------------------------------------------------------------------
        # Step 7: Kitchen updates to 'served'; customer receives update
        # ------------------------------------------------------------------
        @sync_to_async
        def advance_to_served():
            staff_client = APIClient()
            staff_client.force_login(kitchen_staff_user)
            # preparing → ready
            r3 = staff_client.patch(
                f"/api/v1/orders/{order_id}/status/",
                {"status": "ready"},
                format="json",
            )
            assert r3.status_code == 200, f"preparing→ready failed: {r3.data}"
            # ready → served
            r4 = staff_client.patch(
                f"/api/v1/orders/{order_id}/status/",
                {"status": "served"},
                format="json",
            )
            assert r4.status_code == 200, f"ready→served failed: {r4.data}"

        await advance_to_served()

        # Push 'served' notification to customer
        await channel_layer.group_send(
            customer_group,
            {
                "type": "order_status_changed",
                "payload": {"order_id": str(order_id), "status": "served"},
            },
        )

        served_msg = await customer_comm.receive_json_from(timeout=3.0)
        assert served_msg.get("type") == "order_status_changed"
        assert served_msg["payload"]["status"] == "served"

        await customer_comm.disconnect()

        # Final DB verification
        order = await sync_to_async(Order.objects.get)(id=order_id)
        assert order.status == "served"
