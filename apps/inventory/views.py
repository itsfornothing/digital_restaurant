"""
inventory/views.py

ViewSets and views for InventoryItem, Supplier, and Inventory Reports.

Endpoints:
  GET    /api/v1/branches/{branch_pk}/inventory/              — list items
  POST   /api/v1/branches/{branch_pk}/inventory/              — create item
  GET    /api/v1/inventory/{pk}/                              — retrieve item
  PATCH  /api/v1/inventory/{pk}/                              — partial update
  GET    /api/v1/branches/{branch_pk}/inventory/report/       — inventory report
  GET    /api/v1/branches/{branch_pk}/suppliers/              — list suppliers
  POST   /api/v1/branches/{branch_pk}/suppliers/              — create supplier

Permission matrix (Requirement 4.2):
  InventoryViewSet:
    list / retrieve          → IsBranchStaff (Branch_Manager + Kitchen_Staff)
    create / partial_update  → IsBranchManager
  SupplierViewSet:
    all actions              → IsBranchManager
  InventoryReportView:
    GET                      → IsBranchManager

Requirements: 4.1, 4.2, 4.3, 11.1, 11.6
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.utils.timezone import now
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.branches.models import Branch
from apps.inventory.models import InventoryItem, Supplier
from apps.notifications.utils import push_staff_events
from apps.shared.csv_export import csv_response
from apps.inventory.serializers import (
    InventoryItemListSerializer,
    InventoryItemSerializer,
    SupplierSerializer,
)
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsBranchStaff,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _snapshot_inventory_item(instance: InventoryItem) -> dict:
    """Return a JSON-safe snapshot of auditable InventoryItem fields."""
    return {
        "id": str(instance.id),
        "name": instance.name,
        "quantity": str(instance.quantity),
        "unit": instance.unit,
        "purchase_price": str(instance.purchase_price),
        "reorder_threshold": str(instance.reorder_threshold),
        "expiration_date": str(instance.expiration_date) if instance.expiration_date else None,
        "category": instance.category,
        "supplier_id": str(instance.supplier_id) if instance.supplier_id else None,
    }


def _write_inventory_audit(
    request,
    action_code: str,
    resource_id,
    old_value: dict | None,
    new_value: dict | None,
    branch_id=None,
) -> None:
    """
    Write an AuditLog entry for an InventoryItem change.

    Silently swallows errors so audit logging never blocks the HTTP response.
    Requirements: 11.1
    """
    try:
        from apps.audit.models import AuditLog

        user = getattr(request, "user", None)
        user_id = str(user.pk) if (user and getattr(user, "is_authenticated", False)) else None
        user_role = getattr(user, "role", "") if user else ""

        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = (
            x_forwarded_for.split(",")[0].strip()
            if x_forwarded_for
            else request.META.get("REMOTE_ADDR", "0.0.0.0")
        )

        AuditLog.objects.create(
            branch_id=branch_id,
            user_id=user_id,
            user_role=user_role,
            ip_address=ip or "0.0.0.0",
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            action=action_code,
            resource_type="InventoryItem",
            resource_id=resource_id,
            old_value=old_value,
            new_value=new_value,
            status="success",
            failure_reason="",
        )
    except Exception as exc:
        logger.warning(
            "Failed to write AuditLog for InventoryItem action %s: %s",
            action_code,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# InventoryViewSet
# ---------------------------------------------------------------------------


class InventoryViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/inventory/   — list
    POST   /api/v1/branches/{branch_pk}/inventory/   — create
    GET    /api/v1/inventory/{pk}/                   — retrieve
    PATCH  /api/v1/inventory/{pk}/                   — partial update

    Permission:
      list / retrieve       → IsBranchStaff
      create / partial_update → IsBranchManager
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [IsBranchManager]

    def get_serializer_class(self):
        if self.action == "list":
            return InventoryItemListSerializer
        return InventoryItemSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsBranchStaff()]
        return [IsBranchManager()]

    def get_queryset(self):
        """
        Scope queryset:
          - list/create: filter by ``branch_pk`` URL kwarg, and additionally
            enforce that branch-scoped roles (Branch_Manager, Kitchen_Staff,
            Receptionist) can only access their own assigned branch.
          - detail (retrieve/partial_update): scope to user's assigned branch
            for branch-scoped roles.

        Cross-branch access for branch-scoped roles returns an empty queryset,
        which results in a 404 on detail or an empty list on list — effectively
        denying access to data from a different branch (Requirement 4.3).
        """
        from apps.authentication.models import UserRole

        user = self.request.user
        branch_pk = self.kwargs.get("branch_pk")

        qs = InventoryItem.objects.select_related("supplier", "branch")

        # Super_Admin and Tenant_Owner have cross-branch access
        elevated_roles = (UserRole.SUPER_ADMIN, UserRole.TENANT_OWNER)

        if branch_pk:
            # For branch-scoped roles, enforce that the requested branch_pk
            # matches the user's assigned branch.
            if hasattr(user, "role") and user.role not in elevated_roles and user.role in (
                UserRole.BRANCH_MANAGER,
                UserRole.RECEPTIONIST,
                UserRole.KITCHEN_STAFF,
            ):
                if user.branch_id and str(user.branch_id) != str(branch_pk):
                    return qs.none()
            qs = qs.filter(branch_id=branch_pk)
        else:
            # Detail view — scope to user's branch for branch-scoped roles
            if hasattr(user, "role") and user.role not in elevated_roles and user.role in (
                UserRole.BRANCH_MANAGER,
                UserRole.RECEPTIONIST,
                UserRole.KITCHEN_STAFF,
            ):
                if user.branch_id:
                    qs = qs.filter(branch_id=user.branch_id)
                else:
                    qs = qs.none()

        return qs.order_by("name")

    def perform_create(self, serializer):
        """
        1. Verify branch exists.
        2. Save the InventoryItem attached to the branch.
        3. Write AuditLog for the creation.
        """
        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        instance = serializer.save(branch=branch)

        _write_inventory_audit(
            request=self.request,
            action_code="INVENTORY_CREATE",
            resource_id=instance.id,
            old_value=None,
            new_value=_snapshot_inventory_item(instance),
            branch_id=branch.id,
        )

        push_staff_events(str(branch.id), "inventory.item_updated", {
            "item_id": str(instance.id), "name": instance.name, "action": "created",
        })

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH — partial update with audit logging.

        Captures old_value BEFORE the update and new_value AFTER.
        """
        instance = self.get_object()
        old_snapshot = _snapshot_inventory_item(instance)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_instance = serializer.save()

        new_snapshot = _snapshot_inventory_item(updated_instance)

        _write_inventory_audit(
            request=request,
            action_code="INVENTORY_UPDATE",
            resource_id=updated_instance.id,
            old_value=old_snapshot,
            new_value=new_snapshot,
            branch_id=updated_instance.branch_id,
        )

        push_staff_events(str(updated_instance.branch_id), "inventory.item_updated", {
            "item_id": str(updated_instance.id), "name": updated_instance.name, "action": "updated",
        })

        return Response(
            self.get_serializer(updated_instance).data,
            status=status.HTTP_200_OK,
        )

    # -- CSV export --------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request, branch_pk=None, **kwargs):
        qs = self.get_queryset()
        rows = []
        for item in qs:
            rows.append({
                "Name": item.name,
                "Category": item.category or "",
                "Quantity": str(item.quantity),
                "Unit": item.unit,
                "Purchase Price": str(item.purchase_price),
                "Supplier": item.supplier.name if item.supplier else "",
                "Expiration Date": str(item.expiration_date or ""),
                "Reorder Threshold": str(item.reorder_threshold),
            })
        return csv_response(rows, f"inventory_{branch_pk}.csv")


# ---------------------------------------------------------------------------
# SupplierViewSet
# ---------------------------------------------------------------------------


class SupplierViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/suppliers/   — list
    POST   /api/v1/branches/{branch_pk}/suppliers/   — create

    Permission: IsBranchManager for all actions.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = SupplierSerializer
    permission_classes = [IsBranchManager]

    def get_queryset(self):
        branch_pk = self.kwargs.get("branch_pk")
        qs = Supplier.objects.all()
        if branch_pk:
            qs = qs.filter(branch_id=branch_pk)
        else:
            user = self.request.user
            from apps.authentication.models import UserRole
            if hasattr(user, "role") and user.role == UserRole.BRANCH_MANAGER:
                if user.branch_id:
                    qs = qs.filter(branch_id=user.branch_id)
                else:
                    qs = qs.none()
        return qs.order_by("name")

    def perform_create(self, serializer):
        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")
        serializer.save(branch=branch)


# ---------------------------------------------------------------------------
# InventoryReportView — GET /api/v1/branches/{branch_pk}/inventory/report/
# ---------------------------------------------------------------------------


class InventoryReportView(APIView):
    """
    GET /api/v1/branches/{branch_pk}/inventory/report/

    Returns a structured inventory report for the branch:
      - current_stock: all items
      - below_threshold: items where quantity <= reorder_threshold
      - expiring_soon: items where expiration_date <= today + expiry_days
      - out_of_stock: items where quantity <= 0
      - total_inventory_value: sum of quantity * purchase_price (Decimal arithmetic)

    Query params:
      ?expiry_days=N  (default 7)

    Permission: IsBranchManager
    Requirements: 11.6
    """

    permission_classes = [IsBranchManager]

    def get(self, request, branch_pk=None):
        # Verify the branch exists
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        # Parse query param
        try:
            expiry_days = int(request.query_params.get("expiry_days", 7))
        except (ValueError, TypeError):
            expiry_days = 7

        items = InventoryItem.objects.filter(branch=branch).select_related("supplier")
        today = date.today()
        expiry_cutoff = today + timedelta(days=expiry_days)

        # Build serialized representations
        def item_to_dict(item):
            return {
                "id": str(item.id),
                "name": item.name,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "purchase_price": str(item.purchase_price),
                "reorder_threshold": str(item.reorder_threshold),
                "expiration_date": str(item.expiration_date) if item.expiration_date else None,
                "category": item.category,
                "supplier_id": str(item.supplier_id) if item.supplier_id else None,
            }

        current_stock = [item_to_dict(i) for i in items]
        below_threshold = [item_to_dict(i) for i in items if i.quantity <= i.reorder_threshold]
        out_of_stock = [item_to_dict(i) for i in items if i.quantity <= 0]
        expiring_soon = [
            item_to_dict(i)
            for i in items
            if i.expiration_date is not None and i.expiration_date <= expiry_cutoff
        ]

        # Compute total inventory value using Decimal arithmetic
        total_value = Decimal("0.00")
        for item in items:
            try:
                total_value += Decimal(str(item.quantity)) * Decimal(str(item.purchase_price))
            except InvalidOperation:
                pass

        return Response(
            {
                "branch_id": str(branch.id),
                "generated_at": now().isoformat(),
                "total_items": items.count(),
                "total_inventory_value": str(total_value),
                "current_stock": current_stock,
                "below_threshold": below_threshold,
                "expiring_soon": expiring_soon,
                "out_of_stock": out_of_stock,
            },
            status=status.HTTP_200_OK,
        )
