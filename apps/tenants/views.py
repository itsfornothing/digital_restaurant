"""
tenants/views.py — TenantViewSet wiring ProvisioningService to REST API.

Endpoints implemented here:
    POST   /api/v1/tenants/              → create_tenant
    GET    /api/v1/tenants/{id}/         → retrieve_tenant (Super_Admin or own Tenant_Owner)
    POST   /api/v1/tenants/{id}/suspend/ → suspend_tenant
    DELETE /api/v1/tenants/{id}/         → delete_tenant (requires X-Confirm-Delete: true)

create/suspend/destroy require IsSuperAdmin.
retrieve allows IsSuperAdmin (any tenant) or IsTenantOwner (own tenant only).

Requirements: 1.2, 1.4, 1.5, 1.6
"""

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from shared.permissions import IsSuperAdmin, IsTenantOwner

from .models import Tenant
from .serializers import CreateTenantSerializer, TenantSerializer
from .services import (
    InvalidConfirmationToken,
    PlanNotFound,
    ProvisioningError,
    ProvisioningService,
    TenantAlreadyExists,
    TenantNotFound,
)

logger = logging.getLogger(__name__)


class TenantViewSet(viewsets.GenericViewSet):
    """
    ViewSet for tenant lifecycle management.

    - create / suspend / destroy: require IsSuperAdmin.
    - retrieve: accessible to IsSuperAdmin (any tenant) or IsTenantOwner
      (own tenant only — enforced via scope check in the action).

    The ViewSet does not inherit from ModelMixin classes — it exposes only
    the provisioning and retrieval actions defined in Tasks 4.2 and 8.6.
    """

    permission_classes = [IsSuperAdmin]
    # serializer_class is set per-action below

    def get_serializer_class(self):
        if self.action == "create":
            return CreateTenantSerializer
        return TenantSerializer

    # ------------------------------------------------------------------
    # GET /api/v1/tenants/{id}/  →  retrieve_tenant
    # ------------------------------------------------------------------

    def retrieve(self, request, pk=None):
        """
        Retrieve a single tenant record.

        Access rules (Requirements 1.2, 1.5):
          - Super_Admin: may retrieve any tenant.
          - Tenant_Owner: may retrieve only their own tenant (the one whose
            schema matches the current request context).  A request to any
            other tenant's record returns 403 TENANT_ACCESS_DENIED.
          - All other roles: 403 (enforced by IsSuperAdmin default; overridden
            here for Tenant_Owner only).

        Returns 200 with the serialized Tenant on success.

        Error codes:
            - 403 TENANT_ACCESS_DENIED — Tenant_Owner accessing a different tenant
            - 404 TENANT_NOT_FOUND     — no tenant with the given id
        """
        user = getattr(request, "user", None)

        # Allow Tenant_Owner through the IsSuperAdmin gate by checking here
        is_super_admin = user and user.is_active and user.role == "Super_Admin"
        is_tenant_owner = user and user.is_active and user.role == "Tenant_Owner"

        if not (is_super_admin or is_tenant_owner):
            raise PermissionDenied(
                "You must be a Super Admin or Tenant Owner to perform this action."
            )

        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            return Response(
                {
                    "error": {
                        "code": "TENANT_NOT_FOUND",
                        "message": f"No tenant found with id={pk!r}.",
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Tenant_Owner may only access their own tenant.
        if is_tenant_owner and not is_super_admin:
            # The current tenant context is attached by TenantMiddleware.
            # In tests the tenant can also be injected as request.tenant directly.
            request_tenant = getattr(request, "tenant", None)
            if request_tenant is None or str(request_tenant.pk) != str(tenant.pk):
                return Response(
                    {
                        "error": {
                            "code": "TENANT_ACCESS_DENIED",
                            "message": "You may only access your own tenant.",
                            "details": {},
                        }
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        out = TenantSerializer(tenant)
        return Response(out.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # POST /api/v1/tenants/  →  create_tenant
    # ------------------------------------------------------------------

    def create(self, request):
        """
        Provision a new tenant.

        Request body (JSON)::

            {
                "name": "Green Leaf",
                "slug": "greenleaf",
                "plan_id": 1,
                "owner_email": "owner@greenleaf.et"
            }

        Returns 201 with the created Tenant on success.

        Error codes:
            - 400 TENANT_ALREADY_EXISTS — slug already in use
            - 400 PLAN_NOT_FOUND        — plan_id does not match any plan
            - 400 VALIDATION_ERROR      — malformed request body
            - 500 PROVISIONING_ERROR    — schema migration or internal failure
        """
        serializer = CreateTenantSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid request data.",
                        "details": serializer.errors,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        service = ProvisioningService()

        try:
            tenant = service.create_tenant(
                name=data["name"],
                slug=data["slug"],
                plan_id=data["plan_id"],
                owner_email=data["owner_email"],
            )
        except TenantAlreadyExists as exc:
            return Response(
                {
                    "error": {
                        "code": "TENANT_ALREADY_EXISTS",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PlanNotFound as exc:
            return Response(
                {
                    "error": {
                        "code": "PLAN_NOT_FOUND",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ProvisioningError as exc:
            logger.error("Provisioning failed: %s", exc, exc_info=True)
            return Response(
                {
                    "error": {
                        "code": "PROVISIONING_ERROR",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        out = TenantSerializer(tenant)
        return Response(out.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # POST /api/v1/tenants/{id}/suspend/  →  suspend_tenant
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="suspend")
    def suspend(self, request, pk=None):
        """
        Suspend a tenant, revoking all active user sessions immediately.

        Returns 200 with a confirmation message on success.

        Error codes:
            - 404 TENANT_NOT_FOUND — no tenant with the given id
        """
        service = ProvisioningService()

        try:
            service.suspend_tenant(tenant_id=pk)
        except TenantNotFound as exc:
            return Response(
                {
                    "error": {
                        "code": "TENANT_NOT_FOUND",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {"message": "Tenant suspended successfully."},
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # DELETE /api/v1/tenants/{id}/  →  delete_tenant
    # ------------------------------------------------------------------

    def destroy(self, request, pk=None):
        """
        Permanently delete a tenant and all its data.

        Requires the ``X-Confirm-Delete: true`` HTTP header.  The confirmation
        token must be obtained by calling
        ``ProvisioningService.generate_delete_token(tenant_id)`` and is
        included in the response as ``delete_token`` when the header is absent
        but the tenant exists.

        Returns 204 No Content on success.

        Error codes:
            - 400 MISSING_CONFIRM_HEADER  — X-Confirm-Delete header not set to "true"
            - 400 INVALID_CONFIRM_TOKEN   — token does not match expected value
            - 404 TENANT_NOT_FOUND        — no tenant with the given id
        """
        # Require explicit confirmation header to prevent accidental deletions
        confirm_header = request.headers.get("X-Confirm-Delete", "").lower()
        if confirm_header != "true":
            return Response(
                {
                    "error": {
                        "code": "MISSING_CONFIRM_HEADER",
                        "message": (
                            "Tenant deletion requires the 'X-Confirm-Delete: true' "
                            "header to be present."
                        ),
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = ProvisioningService()

        # Generate the expected confirmation token so the caller knows what to pass
        # (The token is derived from the tenant_id; callers must supply it in the
        # request body or as a query param.  Here we read from the request body.)
        confirmation_token = (
            request.data.get("confirmation_token")
            or request.query_params.get("confirmation_token", "")
        )

        if not confirmation_token:
            # Inform the caller of the required token so they can confirm
            try:
                token = service.generate_delete_token(pk)
            except Exception:
                token = None

            return Response(
                {
                    "error": {
                        "code": "MISSING_CONFIRM_TOKEN",
                        "message": (
                            "A confirmation_token is required to delete a tenant. "
                            "Use the delete_token value below in your next request."
                        ),
                        "details": {},
                        "delete_token": token,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service.delete_tenant(tenant_id=pk, confirmation_token=confirmation_token)
        except TenantNotFound as exc:
            return Response(
                {
                    "error": {
                        "code": "TENANT_NOT_FOUND",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except InvalidConfirmationToken as exc:
            return Response(
                {
                    "error": {
                        "code": "INVALID_CONFIRM_TOKEN",
                        "message": str(exc),
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
