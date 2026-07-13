"""
expenses/views.py

ViewSets for Expense management.

Endpoints:
  GET    /api/v1/branches/{branch_pk}/expenses/        — list (IsFinancialReader)
  POST   /api/v1/branches/{branch_pk}/expenses/        — create (IsBranchManager)
  GET    /api/v1/expenses/{pk}/                        — retrieve (IsFinancialReader)
  PATCH  /api/v1/expenses/{pk}/                        — partial update (IsBranchManager)
  DELETE /api/v1/expenses/{pk}/                        — destroy (IsBranchManager)
  GET    /api/v1/branches/{branch_pk}/expenses/report/ — summary report (IsFinancialReader)
  POST   /api/v1/branches/{branch_pk}/expenses/export/ — trigger export (IsBranchManager)

Permission matrix (Requirement 4.2):
  list / retrieve / report → IsFinancialReader (Branch_Manager, Tenant_Owner, Super_Admin)
  create / partial_update / destroy / export → IsBranchManager

Requirements: 4.2, 12.1, 12.2, 12.3, 12.4
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.branches.models import Branch
from apps.expenses.models import Expense
from apps.expenses.serializers import ExpenseSerializer
from apps.notifications.utils import push_staff_events
from apps.shared.csv_export import csv_response
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsFinancialReader,
)

logger = logging.getLogger(__name__)

try:
    from apps.webhooks.dispatch import dispatch_webhook_event as _dispatch_webhook
except ImportError:
    _dispatch_webhook = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _snapshot_expense(instance: Expense) -> dict:
    """Return a JSON-safe snapshot of an Expense record."""
    return {
        "id": str(instance.id),
        "branch_id": str(instance.branch_id),
        "description": instance.description,
        "category": instance.category,
        "amount": str(instance.amount),
        "date_incurred": str(instance.date_incurred),
        "notes": instance.notes,
        "reference_number": instance.reference_number,
    }


def _write_expense_audit(
    request,
    action_code: str,
    resource_id,
    old_value: dict | None,
    new_value: dict | None,
    branch_id=None,
) -> None:
    """
    Write an AuditLog entry for an Expense change.

    Silently swallows errors so audit logging never blocks the HTTP response.
    Requirement 12.2: audit record must capture old and new values on every
    modify/delete operation.
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
            resource_type="Expense",
            resource_id=resource_id,
            old_value=old_value,
            new_value=new_value,
            status="success",
            failure_reason="",
        )
    except Exception as exc:
        logger.warning(
            "Failed to write AuditLog for Expense action %s: %s",
            action_code,
            exc,
            exc_info=True,
        )


def _get_period_dates(period: str) -> tuple[date, date]:
    """
    Return (start_date, end_date) for the requested period relative to today.

    Supported period values: daily, weekly, monthly, annual.
    Defaults to monthly on unrecognised input.
    """
    today = date.today()
    if period == "daily":
        return today, today
    elif period == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, today
    elif period == "annual":
        return date(today.year, 1, 1), today
    else:
        # monthly (default)
        return date(today.year, today.month, 1), today


# ---------------------------------------------------------------------------
# ExpenseViewSet
# ---------------------------------------------------------------------------


class ExpenseViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Full CRUD ViewSet for Expense records.

    Queryset scoping:
      - Branch_Manager: only own branch's expenses
      - Tenant_Owner: all branches (django-tenants schema already scopes tenant)
      - Super_Admin: all

    Requirements: 4.2, 12.1, 12.2, 12.3
    """

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    serializer_class = ExpenseSerializer
    permission_classes = [IsBranchManager]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "report"):
            return [IsFinancialReader()]
        return [IsBranchManager()]

    def get_queryset(self):
        from apps.authentication.models import UserRole

        user = self.request.user
        branch_pk = self.kwargs.get("branch_pk")

        qs = Expense.objects.select_related("branch")

        if branch_pk:
            # List / create context: filter to the specified branch.
            # For branch-scoped roles, additionally verify it's the user's branch.
            if hasattr(user, "role") and user.role == UserRole.BRANCH_MANAGER:
                if user.branch_id and str(user.branch_id) != str(branch_pk):
                    return qs.none()
            qs = qs.filter(branch_id=branch_pk)
        else:
            # Detail view (retrieve / partial_update / destroy):
            # scope to user's own branch for Branch_Manager.
            if hasattr(user, "role") and user.role == UserRole.BRANCH_MANAGER:
                if user.branch_id:
                    qs = qs.filter(branch_id=user.branch_id)
                else:
                    qs = qs.none()
            # Tenant_Owner and Super_Admin see all (django-tenants scopes by schema)

        return qs.order_by("-date_incurred", "-created_at")

    def perform_create(self, serializer):
        """
        Attach the branch from the URL, save the expense, trigger profit update,
        and write an EXPENSE_CREATE audit log entry.
        """
        from apps.financials.tasks import update_profit

        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        instance = serializer.save(branch=branch)

        # Trigger async profit recalculation
        try:
            update_profit.delay(str(branch.id), "daily")
        except Exception as exc:
            logger.warning("update_profit.delay failed: %s", exc)

        _write_expense_audit(
            request=self.request,
            action_code="EXPENSE_CREATE",
            resource_id=instance.id,
            old_value=None,
            new_value=_snapshot_expense(instance),
            branch_id=branch.id,
        )

        if _dispatch_webhook is not None:
            _dispatch_webhook(
                branch_id=str(branch.id),
                event_type="expense.created",
                payload={
                    "expense_id": str(instance.id),
                    "description": instance.description,
                    "category": instance.category,
                    "amount": str(instance.amount),
                    "date": str(instance.date_incurred),
                    "branch_id": str(branch.id),
                },
            )

        push_staff_events(str(branch.id), "expense.updated", {
            "expense_id": str(instance.id), "description": instance.description, "action": "created",
        })

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH — partial update with full audit log (old_value → new_value).
        Requirement 12.2: record old and new values on modify.
        """
        from apps.financials.tasks import update_profit

        instance = self.get_object()
        old_snapshot = _snapshot_expense(instance)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_instance = serializer.save()

        new_snapshot = _snapshot_expense(updated_instance)

        # Trigger async profit recalculation
        try:
            update_profit.delay(str(updated_instance.branch_id), "daily")
        except Exception as exc:
            logger.warning("update_profit.delay failed: %s", exc)

        _write_expense_audit(
            request=request,
            action_code="EXPENSE_UPDATE",
            resource_id=updated_instance.id,
            old_value=old_snapshot,
            new_value=new_snapshot,
            branch_id=updated_instance.branch_id,
        )

        push_staff_events(str(updated_instance.branch_id), "expense.updated", {
            "expense_id": str(updated_instance.id), "description": updated_instance.description, "action": "updated",
        })

        return Response(
            self.get_serializer(updated_instance).data,
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        """
        DELETE — capture full snapshot before deletion, then write audit log.
        Requirement 12.2: record old_value on deletion.
        """
        from apps.financials.tasks import update_profit

        instance = self.get_object()
        old_snapshot = _snapshot_expense(instance)
        branch_id = instance.branch_id
        resource_id = instance.id
        _description = instance.description

        instance.delete()

        # Trigger async profit recalculation
        try:
            update_profit.delay(str(branch_id), "daily")
        except Exception as exc:
            logger.warning("update_profit.delay failed: %s", exc)

        _write_expense_audit(
            request=request,
            action_code="EXPENSE_DELETE",
            resource_id=resource_id,
            old_value=old_snapshot,
            new_value=None,
            branch_id=branch_id,
        )

        push_staff_events(str(branch_id), "expense.updated", {
            "expense_id": str(resource_id), "description": _description, "action": "deleted",
        })

        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Report action — GET /api/v1/branches/{branch_pk}/expenses/report/
    # ------------------------------------------------------------------

    @action(
        detail=False,
        methods=["get"],
        url_path="report",
        permission_classes=[IsFinancialReader],
    )
    def report(self, request, branch_pk=None):
        """
        GET /api/v1/branches/{branch_pk}/expenses/report/

        Returns a summary of expenses for the requested period:
          - total_amount: Decimal sum for the period
          - breakdown_by_category: {category: Decimal total}
          - expenses: list of expense objects
          - period_start / period_end

        Query params:
          ?period=daily|weekly|monthly|annual  (default: monthly)
          ?category=food_purchases|...         (optional filter)

        Requirement 12.3: expense reporting with category breakdown.
        """
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        period = request.query_params.get("period", "monthly")
        category_filter = request.query_params.get("category")
        period_start, period_end = _get_period_dates(period)

        qs = Expense.objects.filter(
            branch=branch,
            date_incurred__gte=period_start,
            date_incurred__lte=period_end,
        )

        if category_filter:
            qs = qs.filter(category=category_filter)

        # Aggregate total
        total_agg = qs.aggregate(total=Sum("amount"))
        total_amount = total_agg["total"] or Decimal("0.00")

        # Breakdown by category
        breakdown = {}
        for cat_key, _ in Expense._meta.get_field("category").choices:
            cat_total = qs.filter(category=cat_key).aggregate(t=Sum("amount"))["t"]
            if cat_total:
                breakdown[cat_key] = str(Decimal(str(cat_total)).quantize(Decimal("0.01")))

        # Serialised expense list
        serializer = self.get_serializer(qs.order_by("-date_incurred"), many=True)

        return Response(
            {
                "branch_id": str(branch.id),
                "period": period,
                "period_start": str(period_start),
                "period_end": str(period_end),
                "total_amount": str(total_amount),
                "breakdown_by_category": breakdown,
                "expenses": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    # -- CSV export --------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request, branch_pk=None, **kwargs):
        qs = self.get_queryset()
        rows = []
        for exp in qs:
            rows.append({
                "Date": str(exp.date_incurred),
                "Category": exp.category,
                "Description": exp.description,
                "Amount": str(exp.amount),
                "Reference": exp.reference_number or "",
                "Notes": exp.notes or "",
            })
        return csv_response(rows, f"expenses_{branch_pk}.csv")

    # ------------------------------------------------------------------
    # Export action — POST /api/v1/branches/{branch_pk}/expenses/export/
    # ------------------------------------------------------------------

    @action(
        detail=False,
        methods=["post"],
        url_path="export",
        permission_classes=[IsBranchManager],
    )
    def export(self, request, branch_pk=None):
        """
        POST /api/v1/branches/{branch_pk}/expenses/export/

        Enqueues a PDF or CSV export Celery task for the specified period
        and report type, then immediately returns the task ID.

        Request body:
          {
            "format": "pdf" | "csv",   (default: "csv")
            "period": "daily" | "weekly" | "monthly" | "annual",  (default: "monthly")
            "report_type": "expenses"  (extensible; default: "expenses")
          }

        Returns:
          {"task_id": "...", "status": "queued"}

        Requirement 12.4: export triggers correctly and returns task_id.
        """
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        fmt = request.data.get("format", "csv").lower()
        period = request.data.get("period", "monthly")
        report_type = request.data.get("report_type", "expenses")
        user_id = str(request.user.pk) if request.user and request.user.is_authenticated else None

        from apps.financials.tasks import export_report_csv, export_report_pdf

        if fmt == "pdf":
            task = export_report_pdf.delay(str(branch.id), period, report_type, user_id)
        else:
            task = export_report_csv.delay(str(branch.id), period, report_type, user_id)

        return Response(
            {"task_id": str(task.id), "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )
