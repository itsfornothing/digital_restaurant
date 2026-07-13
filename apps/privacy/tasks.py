"""
privacy/tasks.py

Celery Beat tasks for customer data anonymization.

Task: anonymize_old_orders
  Runs nightly at 02:00 UTC.
  Finds all Order records where placed_at < now() - 30 days
  and is_anonymized=False, then sets:
    customer_name  = ''
    customer_phone = ''
    is_anonymized  = True

  Financial and operational fields (total_amount, items, table_number,
  order_number, branch) are NOT modified — retained indefinitely for
  accounting purposes (Requirements 15.3, 15.4).

Requirements: 15.3, 15.4
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="anonymize-old-orders",
    max_retries=3,
    default_retry_delay=300,  # 5-minute retry back-off
)
def anonymize_old_orders(self) -> dict:
    """
    Anonymize PII on orders older than 30 days.

    Processing:
      1. Compute the cutoff timestamp: now() - 30 days.
      2. Query all Order records where:
           placed_at < cutoff  AND  is_anonymized=False
      3. Bulk-update those records:
           customer_name  → ''
           customer_phone → ''
           is_anonymized  → True
      4. Log the count of anonymized records.

    Financial fields (total_amount), order items, table_number, and
    branch are deliberately left unchanged (Requirement 15.4).

    Returns:
        {"anonymized": <int>} — count of records updated.

    Requirements: 15.3, 15.4
    """
    from apps.orders.models import Order  # imported here to avoid circular imports at module load

    cutoff = timezone.now() - timedelta(days=30)

    try:
        updated_count = Order.objects.filter(
            placed_at__lt=cutoff,
            is_anonymized=False,
        ).update(
            customer_name="",
            customer_phone="",
            is_anonymized=True,
        )

        logger.info(
            "anonymize_old_orders: anonymized %d order(s) placed before %s",
            updated_count,
            cutoff.isoformat(),
        )
        return {"anonymized": updated_count}

    except Exception as exc:
        logger.error(
            "anonymize_old_orders: unexpected error: %s",
            exc,
            exc_info=True,
        )
        # Retry with exponential back-off
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 300)
