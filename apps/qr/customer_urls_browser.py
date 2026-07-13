"""
apps/qr/customer_urls_browser.py

Browser-facing URL routes for customer QR scanning and digital menu.

These routes are served to end-users via their mobile browser (not the JS API).

Routes:
    GET /qr/scan/<token>/          — validates QR token; redirects to menu or shows error page
    GET /customer/menu/            — renders the digital menu HTML template
    GET /customer/cart/            — renders the cart review / order confirmation page
    GET /customer/order/<id>/      — renders the live order tracker page
"""

from django.urls import path

from apps.qr.customer_views import (
    CartPageView,
    CustomerMenuPageView,
    CustomerTestEntryView,
    OrderTrackerPageView,
    QRScanView,
)

urlpatterns = [
    # Browser entry point when customer physically scans a QR code
    path(
        "qr/scan/<str:token>/",
        QRScanView.as_view(),
        name="qr-scan-browser",
    ),
    # Customer-facing digital menu HTML page
    path(
        "customer/menu/",
        CustomerMenuPageView.as_view(),
        name="customer-menu-browser",
    ),
    # Cart review + order confirmation page
    path(
        "customer/cart/",
        CartPageView.as_view(),
        name="customer-cart-browser",
    ),
    # Live order tracker page (after order is placed)
    path(
        "customer/order/<str:order_id>/",
        OrderTrackerPageView.as_view(),
        name="customer-order-tracker-browser",
    ),
    # Test entry — simulate QR scan (dev/demo only)
    path(
        "customer/test/",
        CustomerTestEntryView.as_view(),
        name="customer-test-entry",
    ),
]
