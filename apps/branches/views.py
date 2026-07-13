"""
branches/views.py

ViewSets for Branch and Table management.

Permission matrix (Requirement 4.2, 8.3):
  BranchViewSet:
    POST   (create)         → IsTenantOwner
    PATCH  (partial_update) → IsTenantOwner
    GET    (list/retrieve)  → IsTenantOwner  OR  IsBranchManager (own branch)

  TableViewSet:
    POST   (create)         → IsTenantOwner
    PATCH  (partial_update) → IsTenantOwner
    GET    (list/retrieve)  → IsTenantOwner  OR  any branch staff

Billing enforcement:
    BillingService.check_resource_limit(tenant, 'branches') is called in
    BranchViewSet.perform_create before the Branch is saved (Requirement 2.3, 8.6).

Requirements: 4.1, 4.2, 4.3, 8.1, 8.3, 8.6, 2.3
"""

from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.decorators import action

from apps.billing.exceptions import ResourceLimitExceeded as BillingLimitExceeded
from apps.billing.services import BillingService
from apps.branches.models import Branch, Room, Table
from apps.branches.serializers import (
    BranchListSerializer,
    BranchSerializer,
    RoomSerializer,
    TableSerializer,
)
from shared.exceptions import ResourceLimitExceeded as APIResourceLimitExceeded
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsBranchStaff,
    IsSuperAdminOrTenantOwner,
    IsTenantOwner,
)


# ---------------------------------------------------------------------------
# BranchViewSet
# ---------------------------------------------------------------------------

class BranchViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/        — list all branches (Tenant_Owner sees all,
                                      Branch_Manager sees own branch only)
    POST   /api/v1/branches/        — create a branch (Tenant_Owner only)
    GET    /api/v1/branches/{id}/   — retrieve branch detail
    PATCH  /api/v1/branches/{id}/   — partial update (Tenant_Owner only)

    BillingService.check_resource_limit is called on create to enforce the
    tenant's plan branch count limit (Requirement 8.6, 2.3).
    """

    queryset = Branch.objects.all().order_by("name")
    http_method_names = ["get", "post", "patch", "head", "options"]

    # Default permission_classes — IsSuperAdminOrTenantOwner is the primary gate.
    # Per-action overrides are provided by get_permissions() below.
    permission_classes = [IsSuperAdminOrTenantOwner]

    def get_serializer_class(self):
        if self.action == "list":
            return BranchListSerializer
        return BranchSerializer

    def get_permissions(self):
        """
        Fine-grained permission dispatch:
          list / retrieve → Tenant_Owner OR Branch_Manager (own branch)
          create / partial_update → Tenant_Owner only
        """
        if self.action in ("list", "retrieve"):
            return [_BranchReadPermission()]
        # create, partial_update
        return [IsTenantOwner()]

    def get_queryset(self):
        """
        Scope the queryset based on the user's role:
          - Tenant_Owner / Super_Admin: all branches
          - Branch_Manager (and other branch staff): only their assigned branch
        """
        user = self.request.user
        qs = Branch.objects.all().order_by("name")

        from apps.authentication.models import UserRole
        if user.role in (UserRole.BRANCH_MANAGER, UserRole.RECEPTIONIST, UserRole.KITCHEN_STAFF):
            if user.branch_id:
                qs = qs.filter(id=user.branch_id)
            else:
                qs = qs.none()

        return qs

    def perform_create(self, serializer):
        """
        Enforce the subscription branch limit before saving.

        Raises APIResourceLimitExceeded (HTTP 402) if the tenant is at or over
        its plan limit for branches (Requirement 8.6, 2.3).
        """
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            try:
                BillingService.check_resource_limit(tenant, "branches")
            except BillingLimitExceeded as exc:
                raise APIResourceLimitExceeded(
                    detail=(
                        f"Branch limit reached: {exc.current_count}/{exc.limit}. "
                        f"Upgrade your subscription plan to add more branches."
                    )
                ) from exc
        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        """PATCH — partial update; full PUT is not supported."""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# TableViewSet
# ---------------------------------------------------------------------------

class TableViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/tables/        — list tables in a branch
    POST   /api/v1/branches/{branch_pk}/tables/        — create a table (Tenant_Owner)
    GET    /api/v1/branches/{branch_pk}/tables/{id}/   — retrieve table detail
    PATCH  /api/v1/branches/{branch_pk}/tables/{id}/   — update table (Tenant_Owner)
    DELETE /api/v1/branches/{branch_pk}/tables/{id}/   — delete table (Tenant_Owner)
    """

    serializer_class = TableSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [_BranchReadPermission()]
        return [IsBranchManager()]

    def get_queryset(self):
        branch_pk = self.kwargs.get("branch_pk")
        qs = Table.objects.filter(branch_id=branch_pk).select_related("branch")

        # Branch staff can only see tables for their own branch
        user = self.request.user
        from apps.authentication.models import UserRole
        if user.role in (UserRole.BRANCH_MANAGER, UserRole.RECEPTIONIST, UserRole.KITCHEN_STAFF):
            if str(user.branch_id) != str(branch_pk):
                return qs.none()

        return qs

    def perform_create(self, serializer):
        """Attach the branch from the URL kwargs on creation."""
        from django.db import IntegrityError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        branch_pk = self.kwargs.get("branch_pk")
        branch = Branch.objects.get(pk=branch_pk)
        try:
            serializer.save(branch=branch)
        except IntegrityError as exc:
            raise DRFValidationError(
                {"number": "A table with this number already exists in this branch."}
            ) from exc

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# RoomViewSet
# ---------------------------------------------------------------------------

class RoomViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/rooms/        — list rooms in a branch
    POST   /api/v1/branches/{branch_pk}/rooms/        — create a room (Tenant_Owner)
    GET    /api/v1/branches/{branch_pk}/rooms/{id}/   — retrieve room detail
    PATCH  /api/v1/branches/{branch_pk}/rooms/{id}/   — update room (Tenant_Owner)
    DELETE /api/v1/branches/{branch_pk}/rooms/{id}/   — delete room (Tenant_Owner)
    """

    serializer_class = RoomSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [_BranchReadPermission()]
        return [IsBranchManager()]

    def get_queryset(self):
        branch_pk = self.kwargs.get("branch_pk")
        qs = Room.objects.filter(branch_id=branch_pk).select_related("branch")

        user = self.request.user
        from apps.authentication.models import UserRole
        if user.role in (UserRole.BRANCH_MANAGER, UserRole.RECEPTIONIST, UserRole.KITCHEN_STAFF):
            if str(user.branch_id) != str(branch_pk):
                return qs.none()

        return qs

    def perform_create(self, serializer):
        from django.db import IntegrityError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        branch_pk = self.kwargs.get("branch_pk")
        branch = Branch.objects.get(pk=branch_pk)
        try:
            serializer.save(branch=branch)
        except IntegrityError as exc:
            raise DRFValidationError(
                {"name": "A room with this name already exists in this branch."}
            ) from exc

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Internal permission class
# ---------------------------------------------------------------------------

class _BranchReadPermission(IsSuperAdminOrTenantOwner):
    """
    Composite permission that allows read access for:
      - Super_Admin
      - Tenant_Owner
      - Branch_Manager (own branch — enforced via queryset scoping)
      - Receptionist / Kitchen_Staff (own branch — enforced via queryset scoping)
    """

    message = "You must be a Super Admin, Tenant Owner, or branch staff to view branches."

    def has_permission(self, request, view) -> bool:
        if super().has_permission(request, view):
            return True
        return IsBranchStaff().has_permission(request, view)
