import hashlib
import hmac
import json
import logging

import requests
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def deliver_webhook(self, webhook_id: str, event_type: str, payload: dict):
    from apps.webhooks.models import WebhookConfig

    try:
        config = WebhookConfig.objects.get(id=webhook_id, is_active=True)
    except WebhookConfig.DoesNotExist:
        logger.warning("WebhookConfig %s not found or inactive — skipping", webhook_id)
        return

    body = json.dumps(payload, default=str)
    signature = hmac.new(
        config.secret.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event": event_type,
    }

    try:
        resp = requests.post(config.url, data=body, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        config.failure_count += 1
        if config.failure_count >= 5:
            config.is_active = False
            logger.warning(
                "Webhook %s (%s) deactivated after %d failures",
                config.id, config.url, config.failure_count,
            )
        config.save(update_fields=["failure_count", "is_active", "last_triggered_at"])
        logger.error(
            "Webhook delivery failed for %s (event=%s, attempt=%d): %s",
            config.url, event_type, self.request.retries + 1, exc,
        )
        raise self.retry(exc=exc)

    config.last_triggered_at = __import__("django").utils.timezone.now()
    config.failure_count = 0
    config.save(update_fields=["last_triggered_at", "failure_count"])
    logger.info("Webhook %s delivered (%s) — %s", config.id, event_type, config.url)
