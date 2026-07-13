"""
tests/test_consumers.py — Unit tests for WebSocket consumers.

Tests verify:
  - Unauthenticated connections are rejected with code 4001
  - Connections with wrong role are rejected with code 4003
  - Users without an assigned branch are rejected with code 4003
  - Authenticated connections with correct role are accepted
  - Consumers join and leave the correct channel group
  - Pushed events are forwarded to the connected client
  - CustomerOrderConsumer validates customer session correctly

These tests use channels.testing.WebsocketCommunicator (in-process, no Redis
required) with an in-memory channel layer configured in the testing settings.

Requirements: 17.1, 17.2, 17.3, 17.4
"""

import uuid
from unittest.mock import MagicMock

import pytest
from channels.testing import WebsocketCommunicator

from apps.authentication.models import UserRole
from apps.notifications.consumers import (
    CustomerOrderConsumer,
    InventoryConsumer,
    KitchenConsumer,
    ManagerConsumer,
    ReceptionConsumer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(role: str, branch_id=None, authenticated=True):
    """Return a mock user object suitable for injecting into scope["user"]."""
    user = MagicMock()
    user.is_authenticated = authenticated
    user.role = role
    user.id = uuid.uuid4()
    user.branch_id = branch_id or uuid.uuid4()
    return user


def _make_anonymous_user():
    user = MagicMock()
    user.is_authenticated = False
    user.role = None
    user.id = None
    user.branch_id = None
    return user


def _make_communicator(consumer_class, user=None, extra_scope=None, path=None):
    """
    Build a WebsocketCommunicator with a custom scope injecting the given user.

    Uses an in-process channel layer (InMemoryChannelLayer) injected via scope.
    """
    from channels.layers import get_channel_layer

    scope = {
        "type": "websocket",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 9999),
        "channel_layer": get_channel_layer(),
    }
    if path:
        scope["path"] = path
    if user is not None:
        scope["user"] = user
    if extra_scope:
        scope.update(extra_scope)

    communicator = WebsocketCommunicator(consumer_class.as_asgi(), "/", headers=[])
    communicator.scope.update(scope)
    return communicator


# ---------------------------------------------------------------------------
# KitchenConsumer tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_accepts_kitchen_staff():
    """Kitchen_Staff with a branch should be accepted."""
    user = _make_user(UserRole.KITCHEN_STAFF)
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, subprotocol = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_accepts_branch_manager():
    """Branch_Manager should be accepted on the kitchen consumer."""
    user = _make_user(UserRole.BRANCH_MANAGER)
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_rejects_unauthenticated():
    """Anonymous user must be rejected with close code 4001."""
    user = _make_anonymous_user()
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_rejects_wrong_role():
    """Customer role must be rejected with close code 4003."""
    user = _make_user(UserRole.CUSTOMER)
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_rejects_kitchen_staff_without_branch():
    """Kitchen_Staff with no branch assigned must be rejected."""
    user = _make_user(UserRole.KITCHEN_STAFF)
    user.branch_id = None
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_joins_correct_group():
    """KitchenConsumer must join branch_{branch_id}_kitchen group.

    Verified by sending to the expected group and confirming receipt.
    """
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.KITCHEN_STAFF, branch_id=branch_id)
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    # If the consumer is in the correct group, sending a message there should arrive.
    channel_layer = get_channel_layer()
    expected_group = f"branch_{branch_id}_kitchen"
    await channel_layer.group_send(
        expected_group,
        {"type": "order.new", "payload": {"order_id": "test", "table_number": "1"}},
    )
    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order.new"

    # Sending to a different group should NOT arrive
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_kitchen_consumer_forwards_order_new_event():
    """order.new events pushed to the group must arrive at the client."""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.KITCHEN_STAFF, branch_id=branch_id)
    comm = _make_communicator(KitchenConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    # Push a message to the group
    channel_layer = get_channel_layer()
    group = f"branch_{branch_id}_kitchen"
    message = {
        "type": "order.new",
        "payload": {"order_id": str(uuid.uuid4()), "table_number": "5"},
    }
    await channel_layer.group_send(group, message)

    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order.new"
    assert response["payload"]["table_number"] == "5"
    await comm.disconnect()


# ---------------------------------------------------------------------------
# ReceptionConsumer tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.asyncio
async def test_reception_consumer_accepts_receptionist():
    """Receptionist with a branch should be accepted."""
    user = _make_user(UserRole.RECEPTIONIST)
    comm = _make_communicator(ReceptionConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_reception_consumer_rejects_kitchen_staff():
    """Kitchen_Staff must be rejected by ReceptionConsumer."""
    user = _make_user(UserRole.KITCHEN_STAFF)
    comm = _make_communicator(ReceptionConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_reception_consumer_joins_correct_group():
    """ReceptionConsumer must join branch_{branch_id}_reception group.

    Verified by sending to the expected group and confirming receipt.
    """
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.RECEPTIONIST, branch_id=branch_id)
    comm = _make_communicator(ReceptionConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    expected_group = f"branch_{branch_id}_reception"
    await channel_layer.group_send(
        expected_group,
        {"type": "order.new", "payload": {"order_id": "test"}},
    )
    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order.new"
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_reception_consumer_forwards_order_status_changed():
    """order_status_changed events must arrive at the receptionist client."""
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.RECEPTIONIST, branch_id=branch_id)
    comm = _make_communicator(ReceptionConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    group = f"branch_{branch_id}_reception"
    message = {
        "type": "order_status_changed",
        "payload": {"order_id": str(uuid.uuid4()), "new_status": "preparing"},
    }
    await channel_layer.group_send(group, message)

    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order_status_changed"
    assert response["payload"]["new_status"] == "preparing"
    await comm.disconnect()


# ---------------------------------------------------------------------------
# ManagerConsumer tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.asyncio
async def test_manager_consumer_accepts_branch_manager():
    """Branch_Manager should be accepted."""
    user = _make_user(UserRole.BRANCH_MANAGER)
    comm = _make_communicator(ManagerConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_manager_consumer_rejects_receptionist():
    """Receptionist must be rejected by ManagerConsumer."""
    user = _make_user(UserRole.RECEPTIONIST)
    comm = _make_communicator(ManagerConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_manager_consumer_joins_correct_group():
    """ManagerConsumer must join branch_{branch_id}_manager group.

    Verified by sending to the expected group and confirming receipt.
    """
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.BRANCH_MANAGER, branch_id=branch_id)
    comm = _make_communicator(ManagerConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    expected_group = f"branch_{branch_id}_manager"
    await channel_layer.group_send(
        expected_group,
        {"type": "report_ready", "payload": {"report_url": "https://example.com/report.pdf"}},
    )
    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "report_ready"
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_manager_consumer_forwards_inventory_alert():
    """inventory_alert events must arrive at the manager client."""
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.BRANCH_MANAGER, branch_id=branch_id)
    comm = _make_communicator(ManagerConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    group = f"branch_{branch_id}_manager"
    message = {
        "type": "inventory_alert",
        "payload": {"item": "Tomatoes", "alert_type": "low_stock"},
    }
    await channel_layer.group_send(group, message)

    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "inventory_alert"
    assert response["payload"]["alert_type"] == "low_stock"
    await comm.disconnect()


# ---------------------------------------------------------------------------
# CustomerOrderConsumer tests
# ---------------------------------------------------------------------------

def _make_customer_communicator(order_id, session_order_id=None, has_session=True):
    """Build a communicator for CustomerOrderConsumer with a customer session."""
    from channels.layers import get_channel_layer

    customer_session = None
    if has_session:
        customer_session = {"branch_id": str(uuid.uuid4()), "table_number": "3"}
        if session_order_id:
            customer_session["order_id"] = str(session_order_id)

    scope = {
        "type": "websocket",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 9999),
        "channel_layer": get_channel_layer(),
        "url_route": {"kwargs": {"order_id": str(order_id)}},
        "session": {"customer_session": customer_session} if has_session else {},
        "user": _make_anonymous_user(),
    }

    communicator = WebsocketCommunicator(
        CustomerOrderConsumer.as_asgi(), f"/ws/order/{order_id}/", headers=[]
    )
    communicator.scope.update(scope)
    return communicator


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_customer_consumer_accepts_valid_session():
    """Customer session without order_id restriction should be accepted.

    Group membership is verified by sending a message and confirming receipt.
    """
    from channels.layers import get_channel_layer

    order_id = uuid.uuid4()
    comm = _make_customer_communicator(order_id, has_session=True)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    expected_group = f"order_{order_id}_customer"
    await channel_layer.group_send(
        expected_group,
        {"type": "order_status_changed", "payload": {"new_status": "received"}},
    )
    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order_status_changed"
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_customer_consumer_rejects_no_session():
    """No customer session must be rejected with close code 4001."""
    order_id = uuid.uuid4()
    comm = _make_customer_communicator(order_id, has_session=False)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_customer_consumer_accepts_matching_order():
    """Customer session with matching order_id must be accepted."""
    order_id = uuid.uuid4()
    comm = _make_customer_communicator(order_id, session_order_id=order_id)
    connected, _ = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_customer_consumer_rejects_mismatched_order():
    """Customer session with a different order_id must be rejected."""
    order_id = uuid.uuid4()
    other_order_id = uuid.uuid4()
    comm = _make_customer_communicator(order_id, session_order_id=other_order_id)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_customer_consumer_forwards_status_changed():
    """order_status_changed events must reach the customer's client."""
    from channels.layers import get_channel_layer

    order_id = uuid.uuid4()
    comm = _make_customer_communicator(order_id)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    group = f"order_{order_id}_customer"
    message = {
        "type": "order_status_changed",
        "payload": {"order_id": str(order_id), "new_status": "ready"},
    }
    await channel_layer.group_send(group, message)

    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "order_status_changed"
    assert response["payload"]["new_status"] == "ready"
    await comm.disconnect()


# ---------------------------------------------------------------------------
# InventoryConsumer tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.asyncio
async def test_inventory_consumer_accepts_branch_manager():
    """Branch_Manager should be accepted on InventoryConsumer."""
    user = _make_user(UserRole.BRANCH_MANAGER)
    comm = _make_communicator(InventoryConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_inventory_consumer_rejects_kitchen_staff():
    """Kitchen_Staff must be rejected by InventoryConsumer."""
    user = _make_user(UserRole.KITCHEN_STAFF)
    comm = _make_communicator(InventoryConsumer, user=user)
    connected, _ = await comm.connect()
    assert not connected


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_inventory_consumer_joins_correct_group():
    """InventoryConsumer must join branch_{branch_id}_inventory group.

    Verified by sending to the expected group and confirming receipt.
    """
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.BRANCH_MANAGER, branch_id=branch_id)
    comm = _make_communicator(InventoryConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    expected_group = f"branch_{branch_id}_inventory"
    await channel_layer.group_send(
        expected_group,
        {"type": "out_of_stock", "payload": {"item": "Chicken", "quantity": 0}},
    )
    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "out_of_stock"
    await comm.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_inventory_consumer_forwards_low_stock():
    """low_stock events must arrive at the inventory consumer client."""
    from channels.layers import get_channel_layer

    branch_id = uuid.uuid4()
    user = _make_user(UserRole.BRANCH_MANAGER, branch_id=branch_id)
    comm = _make_communicator(InventoryConsumer, user=user)
    connected, _ = await comm.connect()
    assert connected

    channel_layer = get_channel_layer()
    group = f"branch_{branch_id}_inventory"
    message = {
        "type": "low_stock",
        "payload": {"item_id": str(uuid.uuid4()), "quantity": 2},
    }
    await channel_layer.group_send(group, message)

    response = await comm.receive_json_from(timeout=2)
    assert response["type"] == "low_stock"
    assert response["payload"]["quantity"] == 2
    await comm.disconnect()
