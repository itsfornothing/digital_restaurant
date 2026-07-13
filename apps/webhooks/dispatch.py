import logging

logger = logging.getLogger(__name__)


def dispatch_webhook_event(branch_id, event_type, payload, tenant_id=None):
    from apps.webhooks.models import WebhookConfig
    from apps.webhooks.tasks import deliver_webhook

    configs = WebhookConfig.objects.filter(
        branch_id=branch_id,
        is_active=True,
    )
    configs = [c for c in configs if event_type in c.events]
    for config in configs:
        deliver_webhook.delay(str(config.id), event_type, payload)
