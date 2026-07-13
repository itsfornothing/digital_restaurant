"""
Root URL configuration for the Restaurant Platform.

All API endpoints are versioned under /api/v1/.
The /health endpoint is public and used by load balancers / CI smoke tests.
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

try:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
    _spectacular_available = True
except ImportError:
    _spectacular_available = False

from apps.authentication.staff_views import (
    staff_branch_comparison, staff_dashboard, staff_expenses,
    staff_financials, staff_inventory, staff_kds, staff_login,
    staff_logout, staff_menu, staff_orders, staff_profile,
    staff_qr_codes, staff_reception, staff_register, staff_tables,
)

urlpatterns = [
    # Django i18n language switching endpoint (/i18n/setlang/)
    path("i18n/", include("django.conf.urls.i18n")),

    path("admin/", admin.site.urls),
    # Health check (public) — accessible as GET /health/ (with or without trailing slash)
    path("health/", include("apps.observability.urls")),
    path("health", include("apps.observability.urls")),
    # Prometheus metrics endpoint
    path("", include("django_prometheus.urls")),
    # OpenAPI schema and Swagger UI (only if drf-spectacular is installed)
    *([
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ] if _spectacular_available else []),
    # -----------------------------------------------------------------------
    # Staff portal (HTML pages)
    # -----------------------------------------------------------------------
    path("staff/register/",   staff_register),
    path("staff/login/",      staff_login),
    path("staff/logout/",     staff_logout),
    path("staff/",            staff_dashboard),
    path("staff/kds/",        staff_kds),
    path("staff/reception/",  staff_reception),
    path("staff/orders/",     staff_orders),
    path("staff/menu/",       staff_menu),
    path("staff/inventory/",  staff_inventory),
    path("staff/expenses/",   staff_expenses),
    path("staff/financials/", staff_financials),
    path("staff/profile/",     staff_profile),
    path("staff/qr-codes/", staff_qr_codes),
    path("staff/tables/", staff_tables),
    path("staff/branch-comparison/", staff_branch_comparison),
    # -----------------------------------------------------------------------
    # REST API
    # -----------------------------------------------------------------------
    path("api/v1/", include("apps.tenants.urls")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/", include("apps.authentication.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.branches.urls")),
    path("api/v1/", include("apps.menus.urls")),
    path("api/v1/", include("apps.kitchen.urls")),
    path("api/v1/", include("apps.inventory.urls")),
    path("api/v1/", include("apps.expenses.urls")),
    path("api/v1/", include("apps.financials.urls")),
    path("api/v1/", include("apps.qr.urls")),
    path("api/v1/", include("apps.orders.urls")),
    path("api/v1/", include("apps.whitelabel.urls")),
    path("api/v1/", include("apps.webhooks.urls")),
    # Customer-facing API (anonymous, session-based QR auth)
    path("api/v1/customer/", include("apps.qr.customer_urls")),
    # Customer-facing browser routes (QR scan redirect, digital menu HTML page)
    path("", include("apps.qr.customer_urls_browser")),
]

# Serve media files in development (production serves via Nginx)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
