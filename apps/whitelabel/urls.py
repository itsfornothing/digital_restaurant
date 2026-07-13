"""
whitelabel/urls.py

URL routing for the white-label configuration API.

Endpoints:
    GET   /api/v1/tenant/config/  → TenantConfigViewSet.retrieve
    PATCH /api/v1/tenant/config/  → TenantConfigViewSet.partial_update

Requirements: 7.1, 7.2
"""

from django.urls import path

from apps.whitelabel.views import TenantConfigViewSet

urlpatterns = [
    path(
        "tenant/config/",
        TenantConfigViewSet.as_view(
            {
                "get": "retrieve",
                "patch": "partial_update",
            }
        ),
        name="tenant-config",
    ),
]
