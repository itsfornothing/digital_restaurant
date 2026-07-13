"""
tenants/urls.py — URL routing for tenant provisioning API endpoints.

Routes registered:
    POST   /api/v1/tenants/              → TenantViewSet.create
    GET    /api/v1/tenants/{pk}/         → TenantViewSet.retrieve
    POST   /api/v1/tenants/{pk}/suspend/ → TenantViewSet.suspend
    DELETE /api/v1/tenants/{pk}/         → TenantViewSet.destroy

Requirements: 1.2, 1.4, 1.5, 1.6
"""

from rest_framework.routers import SimpleRouter

from .views import TenantViewSet

router = SimpleRouter(trailing_slash=True)
router.register(r"tenants", TenantViewSet, basename="tenant")

urlpatterns = router.urls
