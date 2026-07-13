"""
notifications/utils.py

Shared helpers for pushing real-time WebSocket events to channel groups.
"""

import logging

logger = logging.getLogger(__name__)


def push_ws_event(channel_layer, group: str, event_type: str, payload: dict) -> None:
    """
    Push a WebSocket event to a channel group.

    Usage::

        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        from apps.notifications.utils import push_ws_event

        channel_layer = get_channel_layer()
        push_ws_event(channel_layer, f"branch_{branch_id}_kitchen", "order.new", {...})

    The ``event_type`` must match the handler method name on the consumer, with
    dots replaced by underscores (e.g. ``"order.new"`` → ``order_new``).
    """
    try:
        from asgiref.sync import async_to_sync

        async_to_sync(channel_layer.group_send)(
            group,
            {
                "type": event_type,
                "payload": payload,
            },
        )
    except Exception as exc:
        logger.warning("push_ws_event failed (group=%s, type=%s): %s", group, event_type, exc)


def push_staff_events(branch_id: str, event_type: str, payload: dict) -> None:
    """
    Push an event to all staff-related channel groups for a branch.

    Pushes to kitchen, reception, manager, and inventory groups so all
    connected staff dashboards receive the update in real time.
    """
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        for suffix in ("kitchen", "reception", "manager", "inventory"):
            push_ws_event(channel_layer, f"branch_{branch_id}_{suffix}", event_type, payload)
    except Exception as exc:
        logger.warning("push_staff_events failed: %s", exc)


def push_staff_roles_event(branch_id: str, event_type: str, payload: dict, roles: list[str]) -> None:
    """
    Push an event to specific staff role groups for a branch.

    ``roles`` may contain any of: "kitchen", "reception", "manager", "inventory".
    """
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        for suffix in roles:
            push_ws_event(channel_layer, f"branch_{branch_id}_{suffix}", event_type, payload)
    except Exception as exc:
        logger.warning("push_staff_roles_event failed: %s", exc)


def push_customer_event(order_id: str, event_type: str, payload: dict) -> None:
    """
    Push an event to a customer's order tracker group.
    """
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        push_ws_event(channel_layer, f"order_{order_id}_customer", event_type, payload)
    except Exception as exc:
        logger.warning("push_customer_event failed: %s", exc)


def push_customer_menu_event(branch_id: str, event_type: str, payload: dict) -> None:
    """
    Push an event to all customers viewing the menu for a branch.
    """
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        push_ws_event(channel_layer, f"branch_{branch_id}_menu", event_type, payload)
    except Exception as exc:
        logger.warning("push_customer_menu_event failed: %s", exc)
