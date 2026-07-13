from django.urls import path

from apps.webhooks.views import WebhookConfigViewSet

webhook_list = WebhookConfigViewSet.as_view({"get": "list", "post": "create"})
webhook_detail = WebhookConfigViewSet.as_view({
    "get": "retrieve", "patch": "partial_update", "delete": "destroy",
})

urlpatterns = [
    path(
        "branches/<uuid:branch_pk>/webhooks/",
        webhook_list,
        name="branch-webhook-list",
    ),
    path(
        "webhooks/<uuid:pk>/",
        webhook_detail,
        name="webhook-detail",
    ),
]
