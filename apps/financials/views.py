"""
financials/views.py

ViewSets for Income, financial dashboard, consolidated report, and report export.

Endpoints:
  GET/POST  /api/v1/branches/{branch_pk}/income/          — list + create income
  GET       /api/v1/branches/{branch_pk}/financials/       — branch financial dashboard
  GET       /api/v1/tenant/financials/                     — consolidated (Tenant_Owner)
  POST      /api/v1/branches/{branch_pk}/reports/          — report export trigger

Permission matrix (Requirement 4.2):
  IncomeViewSet:
    create (manual non-order income)  → IsBranchManager
    list / retrieve                   → IsFinancialReader
  FinancialDashboardViewSet:
    GET                               → IsFinancialReader
  ConsolidatedFinancialViewSet:
    GET                               → IsTenantOwner
  FinancialReportViewSet:
    POST                              → IsFinancialReader

Requirements: 13.1, 13.2, 4.2
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import models as models
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.translation import get_language
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.branches.models import Branch
from apps.financials.models import Income
from apps.shared.csv_export import csv_response
from apps.financials.serializers import IncomeSerializer, ProfitRecordSerializer
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsFinancialReader,
    IsTenantOwner,
)

logger = logging.getLogger(__name__)


def _get_period_dates(period: str) -> tuple[date, date]:
    """Return (start_date, end_date) for a named period relative to today."""
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
# IncomeViewSet
# ---------------------------------------------------------------------------


class IncomeViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/income/  — list income records
    POST   /api/v1/branches/{branch_pk}/income/  — create manual income

    Permission:
      list / retrieve → IsFinancialReader
      create          → IsBranchManager

    Requirements: 13.1, 4.2
    """

    http_method_names = ["get", "post", "head", "options"]
    serializer_class = IncomeSerializer
    permission_classes = [IsBranchManager]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsFinancialReader()]
        return [IsBranchManager()]

    def get_queryset(self):
        from apps.authentication.models import UserRole

        user = self.request.user
        branch_pk = self.kwargs.get("branch_pk")

        qs = Income.objects.select_related("branch", "order")

        if branch_pk:
            if hasattr(user, "role") and user.role == UserRole.BRANCH_MANAGER:
                if user.branch_id and str(user.branch_id) != str(branch_pk):
                    return qs.none()
            qs = qs.filter(branch_id=branch_pk)
        else:
            if hasattr(user, "role") and user.role == UserRole.BRANCH_MANAGER:
                if user.branch_id:
                    qs = qs.filter(branch_id=user.branch_id)
                else:
                    qs = qs.none()

        return qs.order_by("-date", "-created_at")

    def perform_create(self, serializer):
        """Create a manual income entry, then trigger profit recalculation."""
        from apps.financials.tasks import update_profit

        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        instance = serializer.save(branch=branch)

        try:
            update_profit.delay(str(branch.id), "daily")
        except Exception as exc:
            logger.warning("update_profit.delay failed: %s", exc)

    # -- CSV export --------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request, branch_pk=None, **kwargs):
        qs = self.get_queryset()
        rows = []
        for inc in qs:
            rows.append({
                "Date": str(inc.date),
                "Source": inc.source,
                "Amount": str(inc.amount),
                "Description": inc.description or "",
                "Order Number": inc.order.order_number if inc.order else "",
                "Created At": inc.created_at.isoformat() if inc.created_at else "",
            })
        return csv_response(rows, f"income_{branch_pk}.csv")


# ---------------------------------------------------------------------------
# FinancialDashboardViewSet
# ---------------------------------------------------------------------------


class FinancialDashboardViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    GET /api/v1/branches/{branch_pk}/financials/

    Returns the financial dashboard for a branch:
      - Daily / weekly / monthly income, expenses, and profit
      - Top-selling menu items (by quantity sold)
      - Order volume by hour (for today)

    Permission: IsFinancialReader (Branch_Manager, Tenant_Owner, Super_Admin)

    Requirements: 13.2, 4.2
    """

    permission_classes = [IsFinancialReader]
    http_method_names = ["get", "head", "options"]

    def list(self, request, branch_pk=None):
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        from apps.expenses.models import Expense
        from apps.orders.models import Order, OrderItem

        today = date.today()

        def _period_summary(period: str) -> dict:
            p_start, p_end = _get_period_dates(period)
            income_total = (
                Income.objects.filter(
                    branch=branch, date__gte=p_start, date__lte=p_end
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            # Include income from served orders
            order_income = (
                Order.objects.filter(
                    branch=branch,
                    status="served",
                    placed_at__date__gte=p_start,
                    placed_at__date__lte=p_end,
                ).aggregate(t=Sum("total_amount"))["t"]
                or Decimal("0.00")
            )
            income_total += order_income
            expense_total = (
                Expense.objects.filter(
                    branch=branch,
                    date_incurred__gte=p_start,
                    date_incurred__lte=p_end,
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            return {
                "income": str(income_total),
                "expenses": str(expense_total),
                "profit": str(income_total - expense_total),
                "period_start": str(p_start),
                "period_end": str(p_end),
                "order_income": str(order_income),
            }

        # Top-selling items — period-aware
        period_param = request.query_params.get("period", "daily")
        top_period_start, _ = _get_period_dates(period_param)
        top_items = (
            OrderItem.objects.filter(
                order__branch=branch,
                order__placed_at__date__gte=top_period_start,
                order__placed_at__date__lte=today,
                order__status__in=["confirmed", "received", "preparing", "ready", "served"],
            )
            .values("menu_item__name", "menu_item__name_am", "menu_item_id")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(
                    models.ExpressionWrapper(
                        models.F("quantity") * models.F("unit_price"),
                        output_field=models.DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                order_count=Count("order", distinct=True),
            )
            .order_by("-total_quantity")[:10]
        )

        # Order volume by hour (today)
        orders_today = Order.objects.filter(branch=branch, placed_at__date=today)
        volume_by_hour: dict[int, int] = {}
        for order in orders_today:
            hour = order.placed_at.hour
            volume_by_hour[hour] = volume_by_hour.get(hour, 0) + 1

        return Response(
            {
                "branch_id": str(branch.id),
                "generated_at": timezone.now().isoformat(),
                "daily": _period_summary("daily"),
                "weekly": _period_summary("weekly"),
                "monthly": _period_summary("monthly"),
                "top_selling_items": [
                    {
                        "menu_item_id": str(item["menu_item_id"]),
                        "name": item["menu_item__name"],
                        "name_am": item.get("menu_item__name_am") or "",
                        "name_translated": (
                            item["menu_item__name_am"]
                            if get_language() == "am" and item.get("menu_item__name_am")
                            else item["menu_item__name"]
                        ),
                        "total_quantity": item["total_quantity"],
                        "total_revenue": str(item["total_revenue"] or "0.00"),
                        "order_count": item["order_count"],
                    }
                    for item in top_items
                ],
                "order_volume_by_hour": {str(h): c for h, c in sorted(volume_by_hour.items())},
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# ConsolidatedFinancialViewSet
# ---------------------------------------------------------------------------


class ConsolidatedFinancialViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    GET /api/v1/tenant/financials/

    Aggregates financial data across all branches for the requesting
    Tenant_Owner.

    Query parameters:
        period — "daily", "weekly", or "monthly" (default: "monthly")

    Response includes per-branch summaries with income, expenses, net_profit,
    order_count, avg_order_value, top_item_name, and a comparison key
    highlighting top performance.

    Permission: IsTenantOwner only.

    Requirements: 8.3, 13.2, 13.3, 4.2
    """

    permission_classes = [IsTenantOwner]
    http_method_names = ["get", "head", "options"]

    def list(self, request):
        from apps.expenses.models import Expense
        from apps.orders.models import Order, OrderItem

        # django-tenants: all queries are already scoped to the current tenant schema.
        branches = Branch.objects.filter(is_active=True)
        today = date.today()
        period = request.query_params.get("period", "monthly")
        period_start, _ = _get_period_dates(period)

        branch_summaries = []
        total_income_all = Decimal("0.00")
        total_expenses_all = Decimal("0.00")
        total_order_count = 0

        for branch in branches:
            inc = (
                Income.objects.filter(
                    branch=branch, date__gte=period_start, date__lte=today
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            exp = (
                Expense.objects.filter(
                    branch=branch,
                    date_incurred__gte=period_start,
                    date_incurred__lte=today,
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            order_count = Order.objects.filter(
                branch=branch,
                placed_at__date__gte=period_start,
                placed_at__date__lte=today,
                status__in=["confirmed", "received", "preparing", "ready", "served"],
            ).count()

            # Top item by quantity for this branch in the period
            top_item = (
                OrderItem.objects.filter(
                    order__branch=branch,
                    order__placed_at__date__gte=period_start,
                    order__placed_at__date__lte=today,
                    order__status__in=["confirmed", "received", "preparing", "ready", "served"],
                )
                .values("menu_item__name", "menu_item__name_am")
                .annotate(total_qty=Sum("quantity"))
                .order_by("-total_qty")
                .first()
            )

            total_income_all += inc
            total_expenses_all += exp
            total_order_count += order_count
            branch_summaries.append(
                {
                    "branch_id": str(branch.id),
                    "branch_name": branch.name,
                    "income": str(inc),
                    "expenses": str(exp),
                    "net_profit": str(inc - exp),
                    "order_count": order_count,
                    "avg_order_value": str(round(inc / order_count, 2)) if order_count > 0 else "0.00",
                    "top_item_name": top_item["menu_item__name"] if top_item else None,
                    "top_item_name_am": (top_item.get("menu_item__name_am") or "") if top_item else None,
                    "top_item_name_translated": (
                        top_item["menu_item__name_am"]
                        if top_item and get_language() == "am" and top_item.get("menu_item__name_am")
                        else top_item["menu_item__name"] if top_item else None
                    ),
                }
            )

        # Comparison highlights
        comparison = {}
        if branch_summaries:
            highest_income = max(branch_summaries, key=lambda b: Decimal(b["income"]))
            lowest_expenses = min(branch_summaries, key=lambda b: Decimal(b["expenses"]))
            highest_margin = max(
                branch_summaries,
                key=lambda b: (
                    Decimal(b["net_profit"]) / Decimal(b["income"])
                    if Decimal(b["income"]) > 0 else Decimal("-999999")
                ),
            )
            comparison = {
                "highest_income_branch": highest_income["branch_name"],
                "highest_income": highest_income["income"],
                "lowest_expenses_branch": lowest_expenses["branch_name"],
                "lowest_expenses": lowest_expenses["expenses"],
                "highest_margin_branch": highest_margin["branch_name"],
                "highest_margin_profit": highest_margin["net_profit"],
                "total_orders": total_order_count,
            }

        return Response(
            {
                "generated_at": timezone.now().isoformat(),
                "period": period,
                "period_start": str(period_start),
                "period_end": str(today),
                "total_income": str(total_income_all),
                "total_expenses": str(total_expenses_all),
                "net_profit": str(total_income_all - total_expenses_all),
                "branches": branch_summaries,
                "comparison": comparison,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# FinancialReportViewSet
# ---------------------------------------------------------------------------


class FinancialReportViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    POST /api/v1/branches/{branch_pk}/reports/

    Triggers an async report export (PDF or CSV) for income and expenses.

    Request body:
      { "format": "pdf"|"csv", "period": "daily"|"weekly"|"monthly"|"annual",
        "report_type": "financials" }

    Returns: {"task_id": "...", "status": "queued"}

    Permission: IsFinancialReader

    Requirements: 12.4, 4.2
    """

    permission_classes = [IsFinancialReader]
    http_method_names = ["post", "head", "options"]

    def create(self, request, branch_pk=None):
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        fmt = request.data.get("format", "csv").lower()
        period = request.data.get("period", "monthly")
        report_type = request.data.get("report_type", "financials")
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
