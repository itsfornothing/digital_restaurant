import uuid

from django.db import models


WEBHOOK_EVENT_CHOICES = [
    ("order.created", "Order Created"),
    ("order.status_changed", "Order Status Changed"),
    ("inventory.low_stock", "Inventory Low Stock"),
    ("menu.updated", "Menu Updated"),
    ("expense.created", "Expense Created"),
    ("tenant.suspended", "Tenant Suspended"),
]


class WebhookConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="webhook_configs",
    )
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=64)
    events = models.JSONField(
        default=list,
        help_text="List of subscribed event types, e.g. ['order.created', 'order.status_changed']",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "webhooks"
        verbose_name = "Webhook Configuration"
        verbose_name_plural = "Webhook Configurations"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Webhook({self.url}) — {len(self.events)} event(s)"
