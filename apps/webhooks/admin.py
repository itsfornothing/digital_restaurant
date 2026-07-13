from django.contrib import admin

from apps.webhooks.models import WebhookConfig


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ["url", "branch", "is_active", "failure_count", "last_triggered_at"]
    list_filter = ["is_active", "branch"]
    search_fields = ["url"]
