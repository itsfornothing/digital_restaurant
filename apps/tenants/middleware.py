"""
tenants/middleware.py — Custom TenantMiddleware extending django-tenants'
TenantMainMiddleware to return structured JSON error responses.

Extends TenantMainMiddleware to handle two error conditions explicitly:

  - 404 + TENANT_NOT_FOUND  : no Domain record matches the incoming hostname
  - 403 + TENANT_SUSPENDED  : the matched tenant has is_active=False

When TENANT_NOT_FOUND is raised, if the deployment has exactly one active
tenant, we fall back to it automatically.  This "single-tenant fallback"
makes ngrok / local dev / Render single-URL deployments work without
manually registering each domain.

All other behavior (schema switching, public tenant fallback) is delegated
to the parent class.
"""

from django.http import JsonResponse
from django_tenants.middleware.main import TenantMainMiddleware
from django_tenants.utils import get_tenant_domain_model


class TenantMiddleware(TenantMainMiddleware):
    """
    Extends django-tenants TenantMainMiddleware to return structured JSON
    error responses for missing or suspended tenants.

    Must remain the FIRST middleware in the MIDDLEWARE list so that the
    correct PostgreSQL schema is set before any other middleware or view
    code executes.
    """

    TENANT_NOT_FOUND_ERROR = "TENANT_NOT_FOUND"
    TENANT_SUSPENDED_ERROR = "TENANT_SUSPENDED"

    def process_request(self, request):
        """
        Resolve the incoming hostname to a tenant and validate it before
        delegating schema-switching to the parent class.

        Returns:
            JsonResponse(404) if no Domain record matches the hostname.
            JsonResponse(403) if the matched tenant is not active.
            None             if the tenant is valid (parent handles schema switch).
        """
        # Health check and metrics endpoints bypass tenant domain lookup
        path = request.path_info.rstrip("/")
        if path in ("/health", "/metrics") or path.startswith("/health/"):
            from django.db import connection
            connection.set_schema_to_public()
            return None

        hostname = self.hostname_from_request(request)
        DomainModel = get_tenant_domain_model()

        try:
            domain = DomainModel.objects.select_related("tenant").get(domain=hostname)
        except DomainModel.DoesNotExist:
            domain = self._fallback_domain(request)
            if domain is None:
                return JsonResponse(
                    {
                        "error": self.TENANT_NOT_FOUND_ERROR,
                        "detail": f"No tenant found for domain '{hostname}'.",
                    },
                    status=404,
                )
            # Fallback: set tenant directly to bypass parent's domain lookup
            return self._activate_tenant(request, domain.tenant)

        tenant = domain.tenant
        if not tenant.is_active:
            return JsonResponse(
                {
                    "error": self.TENANT_SUSPENDED_ERROR,
                    "detail": (
                        f"The tenant '{tenant.name}' is currently suspended. "
                        "Please contact platform support."
                    ),
                },
                status=403,
            )

        # Delegate schema switching (and public-tenant handling) to parent
        return super().process_request(request)

    @staticmethod
    def _activate_tenant(request, tenant):
        """Set the tenant on the connection and request, bypassing domain lookup."""
        from django.db import connection
        request.tenant = tenant
        connection.set_tenant(request.tenant)
        return None

    @staticmethod
    def _fallback_domain(request):
        """
        Return the primary Domain of the first active tenant, or None.

        This allows the app to respond to any arbitrary hostname (e.g. a
        freshly-generated ngrok URL, or a Render .onrender.com URL) without
        requiring the operator to manually create a Domain record for each
        one.  It only activates when ENABLE_TENANT_FALLBACK is True and there is
        exactly one active tenant (the common case for ngrok / single-tenant deployments).
        """
        from django.conf import settings
        if not getattr(settings, "ENABLE_TENANT_FALLBACK", True):
            return None

        try:
            from apps.tenants.models import Domain as DomainModel
            from apps.tenants.models import Tenant

            active_tenants = list(Tenant.objects.filter(is_active=True)[:2])
            if len(active_tenants) != 1:
                return None
            tenant = active_tenants[0]
            domain = DomainModel.objects.filter(tenant=tenant, is_primary=True).first()
            return domain
        except Exception:
            return None
