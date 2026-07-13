"""
Customer-facing QR / order URL routes.

API routes (under /api/v1/customer/):
    POST /api/v1/customer/session/      — create anonymous session from QR scan
    GET  /api/v1/customer/menu/         — get branch menu (requires active session)
    POST /api/v1/customer/cart/add/     — add item to server-side session cart
    GET  /api/v1/customer/cart/         — get current server-side session cart
    POST /api/v1/customer/cart/clear/   — clear session cart

Browser routes (wired into config/urls.py under /):
    GET  /qr/scan/<token>/              — browser QR scan entry point
    GET  /customer/menu/                — customer digital menu HTML page
    GET  /customer/cart/                — cart review / confirmation page
    GET  /customer/order/<id>/          — live order tracker page
"""

from django.urls import path

from apps.qr.customer_views import (
    CartPageView,
    CustomerCartAPIView,
    CustomerMenuPageView,
    CustomerMenuView,
    CustomerOrderCreateView,
    CustomerOrderViewSet,
    CustomerSessionView,
    OrderTrackerPageView,
    QRScanView,
)

# ---------------------------------------------------------------------------
# API routes — included under /api/v1/customer/ in config/urls.py
# ---------------------------------------------------------------------------
urlpatterns = [
    # POST /api/v1/customer/session/ — create anonymous session from QR scan
    path("session/", CustomerSessionView.as_view(), name="customer-session"),
    # GET /api/v1/customer/menu/ — get branch menu (requires active customer session)
    path("menu/", CustomerMenuView.as_view(), name="customer-menu"),
    # POST /api/v1/customer/orders/ — place an order (Task 17.1)
    path("orders/", CustomerOrderCreateView.as_view(), name="customer-order-create"),
    # GET /api/v1/customer/orders/my/ — list active orders for this session
    path(
        "orders/my/",
        CustomerOrderViewSet.as_view({"get": "list"}),
        name="customer-orders-my",
    ),
    # GET /api/v1/customer/orders/{id}/status/ — HTTP polling fallback for tracker
    path(
        "orders/<uuid:pk>/status/",
        CustomerOrderViewSet.as_view({"get": "status"}),
        name="customer-order-status",
    ),
    # POST /api/v1/customer/orders/{id}/cancel/ — cancel own order
    path(
        "orders/<uuid:pk>/cancel/",
        CustomerOrderViewSet.as_view({"post": "cancel"}),
        name="customer-order-cancel",
    ),
    # GET/POST /api/v1/customer/cart/ — server-side session cart
    path("cart/", CustomerCartAPIView.as_view(), name="customer-cart-api"),
]

# ---------------------------------------------------------------------------
# Browser routes — included at root level in config/urls.py
# ---------------------------------------------------------------------------
browser_urlpatterns = [
    # GET /qr/scan/<token>/ — browser entry point when customer scans QR code
    path(
        "qr/scan/<str:token>/",
        QRScanView.as_view(),
        name="qr-scan",
    ),
    # GET /customer/menu/ — digital menu HTML page
    path(
        "customer/menu/",
        CustomerMenuPageView.as_view(),
        name="customer-menu-page",
    ),
    # GET /customer/cart/ — cart review and order confirmation page (Task 17.5)
    path(
        "customer/cart/",
        CartPageView.as_view(),
        name="customer-cart-page",
    ),
    # GET /customer/order/<order_id>/ — live order tracker page (Task 17.5)
    path(
        "customer/order/<str:order_id>/",
        OrderTrackerPageView.as_view(),
        name="customer-order-tracker",
    ),
]
