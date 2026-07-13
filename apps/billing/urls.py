"""
billing/urls.py — URL routing for billing / subscription plan endpoints.

Routes registered:
    GET    /api/v1/plans/                      → SubscriptionPlanViewSet.list
    POST   /api/v1/plans/                      → SubscriptionPlanViewSet.create
    PATCH  /api/v1/plans/{pk}/                 → SubscriptionPlanViewSet.partial_update

    POST   /api/v1/tenants/{pk}/subscription/  → AssignSubscriptionView
    GET    /api/v1/tenants/{pk}/usage/         → TenantUsageView

Requirements: 2.1, 2.2, 2.5, 2.6
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AssignSubscriptionView, SubscriptionPlanViewSet, TenantUsageView

# Router for SubscriptionPlanViewSet (list, create, partial_update)
router = SimpleRouter(trailing_slash=True)
router.register(r"plans", SubscriptionPlanViewSet, basename="plan")

urlpatterns = router.urls + [
    # POST /api/v1/tenants/{pk}/subscription/
    path(
        "tenants/<int:pk>/subscription/",
        AssignSubscriptionView.as_view(),
        name="tenant-subscription",
    ),
    # GET /api/v1/tenants/{pk}/usage/
    path(
        "tenants/<int:pk>/usage/",
        TenantUsageView.as_view(),
        name="tenant-usage",
    ),
]
