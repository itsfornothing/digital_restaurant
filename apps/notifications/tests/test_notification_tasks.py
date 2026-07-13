"""
tests/test_notification_tasks.py — Unit tests for send_order_notification Celery task.

Tests verify:
  - Task serializes order data into the correct WebSocket Message Envelope format
  - Task calls channel_layer.group_send() to both kitchen and reception groups
  - Task uses async_to_sync wrapper for the async channel_layer call
  - Task has retry logic (max_retries=3)
  - Task gracefully handles a missing channel layer (no crash, logs warning)
  - Task fetches order with select_related/prefetch_related (table, items__menu_item)
  - Task retries on Order.DoesNotExist / general exceptions

Requirements: 17.1
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from apps.notifications.tasks import send_order_notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_item(menu_item_name="Tibs", quantity=2, unit_price=Decimal("150.00"),
                     special_instructions="no onions"):
    """Return a mock OrderItem with a nested mock MenuItem."""
    menu_item = MagicMock()
    menu_item.id = uuid.uuid4()
    menu_item.name = menu_item_name

    item = MagicMock()
    item.menu_item_id = menu_item.id
    item.menu_item = menu_item
    item.quantity = quantity
    item.unit_price = unit_price
    item.special_instructions = special_instructions
    return item


def _make_order(branch_id=None, table_number="7", num_items=1):
    """Return a fully-mock Order with items and table."""
    branch_id = branch_id or uuid.uuid4()
    order_id = uuid.uuid4()

    table = MagicMock()
    table.number = table_number

    items = [_make_order_item() for _ in range(num_items)]
    # Mimic QuerySet .all() on the prefetch manager
    items_qs = MagicMock()
    items_qs.all.return_value = items

    order = MagicMock()
    order.id = order_id
    order.branch_id = branch_id
    order.order_number = f"BR{str(branch_id).replace('-','')[:8].upper()}-20260101-0001"
    order.table = table
    order.total_amount = Decimal("300.00")
    order.customer_name = "Alice"
    order.placed_at = MagicMock()
    order.placed_at.isoformat.return_value = "2026-01-01T12:00:00+00:00"
    order.items = items_qs
    return order, branch_id, order_id


# ---------------------------------------------------------------------------
# Tests: payload structure (Message Envelope)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_task_sends_correct_message_envelope():
    """
    send_order_notification must produce a message matching the design doc's
    WebSocket Message Envelope:
        {"type": "order.new", "payload": {"order_id": ..., "table_number": ...,
         "items": [...], ...}}
    """
    order, branch_id, order_id = _make_order(table_number="7", num_items=1)

    captured_messages = []

    def fake_group_send(group, message):
        captured_messages.append((group, message))

    mock_channel_layer = MagicMock()
    # async_to_sync wraps the coroutine; simulate sync call through to our capture
    mock_channel_layer.group_send = AsyncMock(side_effect=lambda g, m: captured_messages.append((g, m)))

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("asgiref.sync.async_to_sync", side_effect=lambda coro_fn: lambda *a, **kw: coro_fn(*a, **kw)):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        # Run eagerly (CELERY_TASK_ALWAYS_EAGER=True in test settings)
        send_order_notification(str(order_id))

    assert len(captured_messages) == 2
    groups = {msg[0] for msg in captured_messages}
    assert f"branch_{branch_id}_kitchen" in groups
    assert f"branch_{branch_id}_reception" in groups

    # Check envelope structure on one message
    _, message = captured_messages[0]
    assert message["type"] == "order.new"
    payload = message["payload"]
    assert payload["order_id"] == str(order_id)
    assert payload["table_number"] == "7"
    assert isinstance(payload["items"], list)
    assert "placed_at" in payload


@pytest.mark.django_db
def test_task_sends_to_both_kitchen_and_reception_groups():
    """Task must call group_send for both kitchen and reception channel groups."""
    order, branch_id, order_id = _make_order()
    calls_made = []

    mock_channel_layer = MagicMock()
    mock_channel_layer.group_send = AsyncMock(
        side_effect=lambda g, m: calls_made.append(g)
    )

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("asgiref.sync.async_to_sync", side_effect=lambda fn: lambda *a, **kw: fn(*a, **kw)):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        send_order_notification(str(order_id))

    assert f"branch_{branch_id}_kitchen" in calls_made
    assert f"branch_{branch_id}_reception" in calls_made
    assert len(calls_made) == 2


@pytest.mark.django_db
def test_task_items_payload_contains_correct_fields():
    """Each item in the payload must include menu_item_id, name, quantity, unit_price, special_instructions."""
    order, _, order_id = _make_order(num_items=2)

    captured = []

    mock_channel_layer = MagicMock()
    mock_channel_layer.group_send = AsyncMock(
        side_effect=lambda g, m: captured.append(m)
    )

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("asgiref.sync.async_to_sync", side_effect=lambda fn: lambda *a, **kw: fn(*a, **kw)):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        send_order_notification(str(order_id))

    assert captured, "Expected at least one group_send call"
    items = captured[0]["payload"]["items"]
    assert len(items) == 2
    for item in items:
        assert "menu_item_id" in item
        assert "menu_item_name" in item
        assert "quantity" in item
        assert "unit_price" in item
        assert "special_instructions" in item


@pytest.mark.django_db
def test_task_uses_select_related_and_prefetch_related():
    """Task must use select_related('table') and prefetch_related('items__menu_item')."""
    order, _, order_id = _make_order()

    mock_channel_layer = MagicMock()
    mock_channel_layer.group_send = AsyncMock()

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("asgiref.sync.async_to_sync", side_effect=lambda fn: lambda *a, **kw: fn(*a, **kw)):
        chain_mock = mock_qs.select_related.return_value.prefetch_related.return_value
        chain_mock.get.return_value = order

        send_order_notification(str(order_id))

        mock_qs.select_related.assert_called_once_with("table")
        mock_qs.select_related.return_value.prefetch_related.assert_called_once_with("items__menu_item")


# ---------------------------------------------------------------------------
# Tests: retry logic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_task_has_max_retries_3():
    """send_order_notification Celery task must declare max_retries=3."""
    assert send_order_notification.max_retries == 3


@pytest.mark.django_db
def test_task_retries_on_order_not_found():
    """
    If the Order does not exist, the task should retry (up to max_retries).
    In eager mode, retry raises the exc immediately after max_retries reached.
    """
    from apps.orders.models import Order

    with patch("apps.orders.models.Order.objects") as mock_qs:
        mock_qs.select_related.return_value.prefetch_related.return_value.get.side_effect = (
            Order.DoesNotExist("not found")
        )
        # In eager mode with propagation, the exception eventually surfaces
        with pytest.raises(Exception):
            send_order_notification.apply(args=[str(uuid.uuid4())], retries=3)


# ---------------------------------------------------------------------------
# Tests: graceful handling when channel layer is None
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_task_handles_none_channel_layer_gracefully(caplog):
    """
    When get_channel_layer() returns None, the task must log a warning and
    return without raising an exception (order is already persisted).
    """
    import logging

    order, _, order_id = _make_order()

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=None):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        with caplog.at_level(logging.WARNING, logger="apps.notifications.tasks"):
            # Should not raise
            send_order_notification(str(order_id))

    assert any("No channel layer" in r.message or "channel layer" in r.message.lower()
                for r in caplog.records)


@pytest.mark.django_db
def test_task_does_not_raise_when_channel_layer_send_fails(caplog):
    """
    If channel_layer.group_send raises (e.g., Redis unavailable), the task
    must log a warning but NOT retry or propagate — the order is already saved.
    """
    import logging

    order, _, order_id = _make_order()

    mock_channel_layer = MagicMock()

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("asgiref.sync.async_to_sync", side_effect=lambda fn: lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError("Redis down"))):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        with caplog.at_level(logging.WARNING, logger="apps.notifications.tasks"):
            # Should not raise
            send_order_notification(str(order_id))

    assert any("failed" in r.message.lower() or "skip" in r.message.lower()
                for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: async_to_sync usage
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_task_uses_async_to_sync_for_channel_send():
    """Task must wrap channel_layer.group_send with async_to_sync."""
    order, _, order_id = _make_order()

    mock_channel_layer = MagicMock()
    async_to_sync_calls = []

    def fake_async_to_sync(coro_fn):
        async_to_sync_calls.append(coro_fn)
        # Return a sync callable that invokes the async function synchronously
        def wrapper(*args, **kwargs):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro_fn(*args, **kwargs))
            finally:
                loop.close()
        return wrapper

    mock_channel_layer.group_send = AsyncMock()

    with patch("apps.orders.models.Order.objects") as mock_qs, \
         patch("channels.layers.get_channel_layer", return_value=mock_channel_layer), \
         patch("apps.notifications.tasks.async_to_sync", side_effect=fake_async_to_sync):
        mock_qs.select_related.return_value.prefetch_related.return_value.get.return_value = order
        send_order_notification(str(order_id))

    # async_to_sync should have been called (once per group_send = 2 times)
    assert len(async_to_sync_calls) >= 2
