"""
orders/urls.py

URL routing for the Order management API.

Registered routes:
    GET    /api/v1/orders/              — OrderViewSet.list
    GET    /api/v1/orders/{id}/         — OrderViewSet.retrieve
    PATCH  /api/v1/orders/{id}/status/  — OrderViewSet.update_status

Requirements: 10.3, 11.2
"""

from rest_framework.routers import DefaultRouter

from apps.orders.views import OrderViewSet

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="order")

urlpatterns = router.urls
