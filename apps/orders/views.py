"""
orders/views.py

ViewSets for Order and OrderItem management.

Permission matrix (Requirement 4.2):
  - OrderViewSet (staff-facing):
      list / retrieve / cancel  → Receptionist OR Branch_Manager
      status update (PATCH)     → Kitchen_Staff OR Receptionist
  - CustomerOrderViewSet:
      create                    → IsCustomerSession
      retrieve (own status)     → IsCustomerSession

PATCH /api/v1/orders/{id}/status/ validates the state machine transition
and enqueues Celery tasks on key transitions:
  - → preparing : deduct_inventory task (Task 12)
  - → served    : record_income task (Task 13)

Requirements: 4.1, 4.2, 4.3, 10.3, 13.1, 14.7, 14.8, 14.10
"""

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.shared.csv_export import csv_response

from apps.authentication.models import UserRole
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer, OrderStatusUpdateSerializer
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsCustomerSession,
    IsKitchenStaff,
    IsReceptionist,
    _get_user,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

def _get_orders_counter():
    """Return the restaurant_orders_placed_total Counter, creating it once."""
    from prometheus_client import Counter, registry as _reg
    name = "restaurant_orders_placed_total"
    if not hasattr(_get_orders_counter, "_counter"):
        try:
            _get_orders_counter._counter = Counter(
                name, "Total number of orders placed", ["branch_id"],
            )
        except ValueError:
            # Already registered (Daphne reload) — grab the existing metric
            for c in _reg.REGISTRY._collector_to_names:
                if hasattr(c, "_name") and c._name == name:
                    _get_orders_counter._counter = c
                    break
            else:
                # Fallback: create a dummy that won't crash
                _get_orders_counter._counter = None
    return _get_orders_counter._counter

# ---------------------------------------------------------------------------
# Celery task imports — wrapped in try/except for forward-compatibility
# ---------------------------------------------------------------------------

try:
    from apps.inventory.tasks import deduct_inventory
except ImportError:
    # TODO: Task 12 — deduct_inventory is implemented in Task 12
    deduct_inventory = None  # type: ignore[assignment]

try:
    from apps.financials.tasks import record_income
except ImportError:
    # TODO: Task 13 — record_income is implemented in Task 13
    record_income = None  # type: ignore[assignment]

try:
    from apps.webhooks.dispatch import dispatch_webhook_event as _dispatch_webhook
except ImportError:
    _dispatch_webhook = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _OrderPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200

def _valid_next_states(current_status: str) -> list[str]:
    """Return the sorted list of valid next states for *current_status*."""
    from apps.orders.models import VALID_TRANSITIONS
    return sorted(VALID_TRANSITIONS.get(current_status, set()))


# ---------------------------------------------------------------------------
# Composite permission classes
# ---------------------------------------------------------------------------

class _OrderReadOrCancelPermission(BasePermission):
    """
    Allows Order list / retrieve / cancel for Receptionist, Branch_Manager,
    Kitchen_Staff, Tenant_Owner, and Super_Admin (Requirement 4.2).
    """

    message = "You must be a Receptionist, Branch Manager, or Kitchen Staff to access orders."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.RECEPTIONIST,
            UserRole.BRANCH_MANAGER,
            UserRole.KITCHEN_STAFF,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


class _OrderStatusUpdatePermission(BasePermission):
    """
    Allows Order status updates for Kitchen_Staff, Receptionist,
    Branch_Manager, Tenant_Owner, and Super_Admin (Requirement 4.2).
    """

    message = "You must be Kitchen Staff or a Receptionist to update order status."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.KITCHEN_STAFF,
            UserRole.RECEPTIONIST,
            UserRole.BRANCH_MANAGER,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


# ---------------------------------------------------------------------------
# OrderViewSet
# ---------------------------------------------------------------------------

class OrderViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    ViewSet for staff-facing Order management.

    Allowed roles:
        list / retrieve / cancel     → Receptionist, Branch_Manager
        status update (patch/status) → Kitchen_Staff, Receptionist

    Endpoints:
        GET    /api/v1/orders/           — list (with branch filter)
        GET    /api/v1/orders/{id}/      — retrieve
        PATCH  /api/v1/orders/{id}/status/ — advance order through state machine

    Requirements: 4.2, 10.3, 14.8
    """

    serializer_class = OrderSerializer
    queryset = Order.objects.all()
    pagination_class = _OrderPagination

    def get_permissions(self):
        if self.action in ("list", "retrieve", "cancel", "create"):
            return [_OrderReadOrCancelPermission()]
        if self.action in ("partial_update", "update", "update_status"):
            return [_OrderStatusUpdatePermission()]
        # Default — require at least receptionist access
        return [_OrderReadOrCancelPermission()]

    def get_queryset(self):
        """Filter orders to the requesting user's branch.

        Tenant_Owner and Super_Admin can see orders across all branches.
        Branch-scoped roles (Branch_Manager, Receptionist, Kitchen_Staff)
        are limited to their assigned branch.

        By default excludes terminal statuses (served, cancelled) unless
        an explicit ``?status=`` query parameter is provided.

        Supports ``?placed_date=today`` or ``?placed_date=YYYY-MM-DD``
        to filter by the order's placed date.
        """
        from apps.authentication.models import UserRole as _UserRole
        user = _get_user(self.request)
        qs = Order.objects.select_related("branch", "table").prefetch_related("items__menu_item")

        elevated_roles = (_UserRole.SUPER_ADMIN, _UserRole.TENANT_OWNER)
        branch_pk = self.kwargs.get("branch_pk")

        if branch_pk:
            if user and hasattr(user, "role") and user.role not in elevated_roles:
                if user.branch_id and str(user.branch_id) != str(branch_pk):
                    return qs.none()
            qs = qs.filter(branch_id=branch_pk)
        elif user and hasattr(user, "role") and user.role not in elevated_roles:
            if user.branch_id:
                qs = qs.filter(branch_id=user.branch_id)

        status_param = self.request.query_params.get("status")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            qs = qs.filter(status__in=statuses)
        else:
            # Default: hide terminal orders (served / cancelled)
            qs = qs.exclude(status__in=["served", "cancelled"])

        if self.action == "list":
            placed_date = self.request.query_params.get("placed_date", "today")
        else:
            placed_date = self.request.query_params.get("placed_date")
        if placed_date:
            from django.utils import timezone as _tz
            import datetime as _dt
            if placed_date == "today":
                _d = _tz.localdate()
            else:
                try:
                    _d = _dt.datetime.strptime(placed_date, "%Y-%m-%d").date()
                except ValueError:
                    _d = None
            if _d:
                _start = _dt.datetime(_d.year, _d.month, _d.day, tzinfo=_dt.timezone.utc)
                _end = _start + _dt.timedelta(days=1)
                qs = qs.filter(placed_at__gte=_start, placed_at__lt=_end)

        return qs.order_by("-placed_at")

    # -- CSV export --------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request, branch_pk=None, **kwargs):
        queryset = self.get_queryset().select_related("table").prefetch_related("items__menu_item")
        rows = []
        for o in queryset:
            items_summary = "; ".join(
                f"{item.quantity}x {item.menu_item.name}" for item in o.items.all()
            )
            rows.append({
                "Order Number": o.order_number,
                "Status": o.status,
                "Table": o.table.number if o.table else "",
                "Customer Name": o.customer_name or "",
                "Customer Phone": o.customer_phone or "",
                "Placed At": o.placed_at.isoformat() if o.placed_at else "",
                "Total Amount": str(o.total_amount),
                "Items": items_summary,
            })
        return csv_response(rows, f"orders_{branch_pk}.csv")

    # -- Standard actions --------------------------------------------------

    def list(self, request, branch_pk=None, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, branch_pk=None, **kwargs):
        order = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = self.get_serializer(order)
        return Response(serializer.data)

    # -- Custom status update action ---------------------------------------

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        """
        PATCH /api/v1/orders/{id}/status/

        Advance an order through the state machine.

        Request body:
            {"status": "<new_status>"}

        Returns:
            200 — serialized updated order
            422 — INVALID_TRANSITION if the transition is out-of-sequence
            400 — if the payload is malformed

        Side effects:
            → preparing : enqueues deduct_inventory(order_id) Celery task
            → served    : enqueues record_income(order_id) Celery task

        Requirements: 10.3, 11.2, 13.1
        """
        order = get_object_or_404(self.get_queryset(), pk=pk)

        serializer = OrderStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data["status"]

        # Validate state machine transition
        if not order.is_valid_transition(new_status):
            return Response(
                {
                    "error": "INVALID_TRANSITION",
                    "detail": (
                        f"Cannot transition order from '{order.status}' to '{new_status}'. "
                        f"Valid transitions from '{order.status}' are: "
                        f"{sorted(order.__class__.__dict__) and _valid_next_states(order.status)}."
                    ),
                },
                status=422,
            )

        previous_status = order.status
        order.status = new_status
        order.save(update_fields=["status"])

        logger.info(
            "Order %s transitioned: %s → %s (user=%s)",
            order.order_number,
            previous_status,
            new_status,
            getattr(request.user, "id", "anonymous"),
        )

        from apps.notifications.utils import push_customer_event, push_staff_roles_event

        # Staff push — use full Order shape + dot-notation type for KDS compat
        staff_payload = {
            "id": str(order.id),
            "order_id": str(order.id),
            "order_number": order.order_number,
            "status": new_status,
            "previous_status": previous_status,
            "branch_id": str(order.branch_id),
            "table_number": str(order.table.number) if order.table else None,
            "placed_at": order.placed_at.isoformat(),
            "total_amount": str(order.total_amount),
            "customer_name": order.customer_name or "",
            "items": [
                {
                    "id": str(item.id),
                    "menu_item": str(item.menu_item_id),
                    "menu_item_name": item.menu_item.name,
                    "quantity": item.quantity,
                    "unit_price": str(item.unit_price),
                    "special_instructions": item.special_instructions or "",
                }
                for item in order.items.select_related("menu_item").all()
            ],
        }
        staff_roles = {
            "received": ["kitchen"],
            "preparing": ["kitchen"],
            "ready": ["reception"],
            "served": [],
            "cancelled": ["kitchen", "reception"],
        }.get(new_status, ["kitchen", "reception"])
        push_staff_roles_event(str(order.branch_id), "order.status_changed", staff_payload, staff_roles)

        # Customer push — original format (new_status / timestamp keys)
        customer_payload = {
            "id": str(order.id),
            "order_id": str(order.id),
            "order_number": order.order_number,
            "new_status": new_status,
            "previous_status": previous_status,
            "branch_id": str(order.branch_id),
            "table_number": str(order.table.number) if order.table else None,
            "timestamp": order.placed_at.isoformat(),
        }
        push_customer_event(str(order.id), "order_status_changed", customer_payload)

        # Enqueue Celery side-effect tasks on key transitions
        if new_status == "preparing":
            if deduct_inventory is not None:
                deduct_inventory.delay(str(order.id))
            else:
                logger.warning(
                    "deduct_inventory task not available — skipping for order %s",
                    order.id,
                )

        if new_status == "served":
            if record_income is not None:
                record_income.delay(str(order.id))
            else:
                logger.warning(
                    "record_income task not available — skipping for order %s",
                    order.id,
                )

        # Dispatch webhooks on order.status_changed
        if _dispatch_webhook is not None:
            _dispatch_webhook(
                branch_id=str(order.branch_id),
                event_type="order.status_changed",
                payload={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "previous_status": previous_status,
                    "new_status": new_status,
                    "branch_id": str(order.branch_id),
                    "table_number": str(order.table.number) if order.table else None,
                    "timestamp": str(order.placed_at.isoformat()),
                },
            )

        out_serializer = OrderSerializer(order)
        return Response(out_serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# CustomerOrderViewSet
# ---------------------------------------------------------------------------

class CustomerOrderViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    ViewSet for customer-facing order placement and status polling.

    Allowed roles: IsCustomerSession (anonymous QR-scan session)

    Full implementation: Task 17 / Task 11
    Requirements: 4.2, 14.7, 14.8, 14.10
    """

    permission_classes = [IsCustomerSession]

    def create(self, request, *args, **kwargs):
        """
        POST /api/v1/customer/orders/

        Place a new order for the customer session.
        Increments the orders_placed_total Prometheus counter on success.

        Requirements: 4.1, 14.7
        """
        from apps.orders.serializers import OrderSerializer as _OrderSerializer

        serializer = _OrderSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        order = serializer.save()

        # Increment the Prometheus counter labelled by branch_id
        _c = _get_orders_counter()
        if _c:
            _c.labels(branch_id=str(order.branch_id)).inc()

        logger.info(
            "Customer order placed: order_id=%s branch_id=%s",
            order.id,
            order.branch_id,
        )

        if _dispatch_webhook is not None:
            _dispatch_webhook(
                branch_id=str(order.branch_id),
                event_type="order.created",
                payload={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "branch_id": str(order.branch_id),
                    "table_number": str(order.table.number) if order.table else None,
                    "total_amount": str(order.total_amount),
                    "timestamp": str(order.placed_at.isoformat()),
                },
            )

        # Push real-time notifications to WebSocket groups
        try:
            from apps.notifications.utils import push_customer_event, push_staff_roles_event

            push_staff_roles_event(
                str(order.branch_id),
                "order.new",
                {
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "table_number": order.table.number if order.table else None,
                    "items": [
                        {
                            "menu_item_id": str(item.menu_item_id),
                            "menu_item_name": item.menu_item.name,
                            "quantity": item.quantity,
                            "unit_price": str(item.unit_price),
                            "special_instructions": item.special_instructions,
                        }
                        for item in order.items.all()
                    ],
                    "total_amount": str(order.total_amount),
                    "customer_name": getattr(order, "customer_name", None),
                    "placed_at": order.placed_at.isoformat(),
                },
                ["kitchen", "reception"],
            )
            push_customer_event(
                str(order.id),
                "order_status_changed",
                {
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "previous_status": None,
                    "new_status": order.status,
                    "branch_id": str(order.branch_id),
                    "table_number": order.table.number if order.table else None,
                    "timestamp": order.placed_at.isoformat(),
                },
            )
        except Exception:
            logger.warning(
                "Failed to push WebSocket events for new order %s", order.id, exc_info=True
            )

        out_serializer = _OrderSerializer(order)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)

