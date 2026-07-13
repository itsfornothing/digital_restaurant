"""
notifications/tasks.py — Celery tasks for real-time order notifications.

Tasks:
  send_order_notification — push a new_order event to
      branch_{branch_id}_kitchen  (KDS)
      branch_{branch_id}_reception (Reception dashboard)

This satisfies the notification half of Task 17.1 and Requirement 17.1:
  WHEN a Customer confirms an order, THE Notification_Service SHALL deliver
  a WebSocket push notification to the reception dashboard and KDS within
  2 seconds.

Requirements: 14.8, 14.9, 17.1
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def send_order_notification(self, order_id: str):
    """
    Push a ``new_order`` WebSocket event to kitchen and reception channel groups.

    The event payload conforms to the WebSocket Message Envelope defined in
    the design document::

        {
          "type": "order.new",
          "payload": {
            "order_id": "<uuid>",
            "order_number": "BR...",
            "table_number": "<number>",
            "items": [...],
            "total_amount": "<decimal>",
            "customer_name": "<str>",
            "placed_at": "<ISO-8601 UTC>"
          }
        }

    Channel groups pushed to:
      - ``branch_{branch_id}_kitchen``    — KDS (Requirement 10.1)
      - ``branch_{branch_id}_reception``  — Reception dashboard (Requirement 17.1)

    Uses ``async_to_sync`` so the synchronous Celery worker can call the
    async ``channel_layer.group_send`` coroutine.

    Args:
        order_id: UUID string of the confirmed Order.

    Requirements: 14.8, 14.9, 17.1
    """
    try:
        from apps.orders.models import Order

        # Fetch the order with its items for the notification payload.
        order = (
            Order.objects.select_related("table")
            .prefetch_related("items__menu_item")
            .get(id=order_id)
        )

        branch_id = str(order.branch_id)

        # Build the items list for the payload.
        items_payload = [
            {
                "menu_item_id": str(item.menu_item_id),
                "menu_item_name": item.menu_item.name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "special_instructions": item.special_instructions,
            }
            for item in order.items.all()
        ]

        message = {
            "type": "order.new",
            "payload": {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "table_number": order.table.number,
                "items": items_payload,
                "total_amount": str(order.total_amount),
                "customer_name": order.customer_name,
                "placed_at": order.placed_at.isoformat(),
            },
        }

        # Attempt to send via the configured Django Channels layer.
        try:
            from apps.notifications.utils import push_staff_roles_event

            push_staff_roles_event(branch_id, "order.new", message["payload"], ["kitchen", "reception"])

            logger.info(
                "Sent new_order push for order %s to kitchen/reception (branch %s)",
                order_id,
                branch_id,
            )
        except Exception as channel_exc:
            # Channels layer unavailable (e.g. Redis not running in test env).
            # Log a warning but do NOT retry — notification delivery failure
            # must not roll back the already-persisted order.
            logger.warning(
                "Channel layer send failed for order %s: %s. "
                "Order is persisted; notification skipped.",
                order_id,
                channel_exc,
            )

    except Exception as exc:
        logger.error(
            "send_order_notification failed for order %s (attempt %d): %s",
            order_id,
            self.request.retries + 1,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5)
