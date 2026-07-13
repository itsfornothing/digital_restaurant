"""
whitelabel/views.py

TenantConfigViewSet — GET and PATCH for /api/v1/tenant/config/

Permission matrix (Requirement 4.2):
  - retrieve / partial_update → Tenant_Owner (or Super_Admin)

Cache invalidation:
  On every successful PATCH, the cached config for the current tenant is
  deleted from Redis so that the context processor picks up the fresh values
  on the next request (Requirement 7.2).

Requirements: 4.1, 4.2, 4.3, 7.1, 7.2
"""

from django.core.cache import cache
from django_tenants.utils import get_tenant_model

try:
    from django.db import connection
except ImportError:
    connection = None  # type: ignore[assignment]

from rest_framework import mixins, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from shared.permissions import AuditLogMixin, IsSuperAdminOrTenantOwner

from apps.whitelabel.models import TenantConfig
from apps.whitelabel.serializers import TenantConfigSerializer


def _tenant_cache_key() -> str:
    """Return the Redis cache key for the current tenant's config."""
    schema = getattr(connection, "schema_name", "public")
    return f"tenant_config:{schema}"


class TenantConfigViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    Singleton-style ViewSet for TenantConfig.

    Exposes two actions:
        GET  /api/v1/tenant/config/   → retrieve
        PATCH /api/v1/tenant/config/  → partial_update

    The config record is always singular — the first (and only) TenantConfig
    row in the tenant schema is returned.  If no record exists yet, an empty
    204 is returned on GET and one is created on PATCH.

    Cache invalidation:
        Each successful PATCH deletes ``tenant_config:{schema_name}`` from
        Redis so the whitelabel_context processor reloads from the DB.
    """

    permission_classes = [IsSuperAdminOrTenantOwner]
    serializer_class = TenantConfigSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_object_or_none(self):
        """Return the singleton TenantConfig or None if it doesn't exist yet."""
        return TenantConfig.objects.first()

    # Cache TTL for the tenant config — 5 minutes (300 seconds).
    # Requirements: 19.1, 19.2 (Task 20.2)
    _CONFIG_CACHE_TTL = 300

    # ------------------------------------------------------------------
    # GET /api/v1/tenant/config/
    # ------------------------------------------------------------------

    def retrieve(self, request, *args, **kwargs):
        """
        Return the current tenant's branding and localisation config.

        Caching (Task 20.2 — Requirements 19.1, 19.2):
            Serialized config is cached under ``tenant_config:{schema}``
            with a 5-minute (300 s) TTL.  The whitelabel_context processor
            uses the same key, so a single warm cache satisfies both.
            Cache is invalidated in partial_update() on every successful PATCH.
        """
        cache_key = _tenant_cache_key()
        cached_data = cache.get(cache_key)
        if cached_data is not None and isinstance(cached_data, dict):
            # Return the cached serialized representation directly.
            return Response(cached_data)

        instance = self._get_object_or_none()
        if instance is None:
            return Response(
                {"detail": "Tenant configuration has not been set up yet."},
                status=status.HTTP_204_NO_CONTENT,
            )

        serializer = self.get_serializer(instance)
        data = serializer.data

        # Store serialized data in cache (TTL: 5 minutes).
        try:
            cache.set(cache_key, dict(data), timeout=self._CONFIG_CACHE_TTL)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "TenantConfigViewSet: failed to cache config: %s", exc
            )

        return Response(data)

    # ------------------------------------------------------------------
    # PATCH /api/v1/tenant/config/
    # ------------------------------------------------------------------

    def partial_update(self, request, *args, **kwargs):
        """
        Partially update (or create) the tenant config.

        On success:
          1. Saves the updated instance.
          2. Invalidates the Redis cached config for this tenant.
          3. Returns the full serialized config.
        """
        instance = self._get_object_or_none()

        if instance is None:
            # First-time setup: create the record from the submitted data.
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        else:
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        # Invalidate Redis cache so whitelabel_context picks up new values.
        cache.delete(_tenant_cache_key())

        return Response(serializer.data, status=status.HTTP_200_OK)
