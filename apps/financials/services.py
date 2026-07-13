"""
financials/services.py

Pure service class for financial calculations.

FinancialService decouples computation logic from views and Celery tasks so
that both can call the same well-tested code path.

Requirements: 13.3, 13.4
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

logger = logging.getLogger(__name__)


def _get_period_dates(period: str, ref_date: date | None = None) -> tuple[date, date]:
    """
    Return (start_date, end_date) for the requested period relative to *ref_date*
    (defaults to today).

    Supported values: daily, weekly, monthly, annual.
    Falls back to daily on unrecognised input.
    """
    today = ref_date or date.today()
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, today
    elif period == "monthly":
        return date(today.year, today.month, 1), today
    elif period == "annual":
        return date(today.year, 1, 1), today
    else:
        # daily (default)
        return today, today


class FinancialService:
    """
    Service class for financial data computation.

    All methods are static so that they can be called without instantiation
    from views, Celery tasks, or tests.

    Requirements: 13.3, 13.4
    """

    # ------------------------------------------------------------------
    # compute_profit
    # ------------------------------------------------------------------

    @staticmethod
    def compute_profit(branch, period: str) -> dict:
        """
        Compute net profit for *branch* over the given *period*.

        Logic:
          net_profit = sum(Income.amount) - sum(Expense.amount) for the period.

        Period values: 'daily', 'weekly', 'monthly', 'annual'.

        Cache strategy:
          - Checks Redis (Django cache) first using key:
              profit_{branch_id}_{period}_{today}
          - Falls back to a DB query on cache miss.
          - After computing from DB, stores the result in the cache for 1 hour.

        Returns:
          {
            "total_income":    str,
            "total_expenses":  str,
            "net_profit":      str,
            "period_start":    str  (YYYY-MM-DD),
            "period_end":      str  (YYYY-MM-DD),
          }

        Called by update_profit Celery task and directly from views.

        Requirements: 13.3
        """
        from apps.expenses.models import Expense
        from apps.financials.models import Income

        today = date.today()
        branch_id = str(branch.id) if hasattr(branch, "id") else str(branch)
        cache_key = f"profit_{branch_id}_{period}_{today}"

        # --- Cache lookup ---
        try:
            from django.core.cache import cache

            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        except Exception as cache_exc:
            logger.warning("FinancialService.compute_profit: cache.get failed: %s", cache_exc)

        # --- DB computation ---
        period_start, period_end = _get_period_dates(period, ref_date=today)

        total_income = (
            Income.objects.filter(
                branch_id=branch_id,
                date__gte=period_start,
                date__lte=period_end,
            ).aggregate(t=Sum("amount"))["t"]
            or Decimal("0.00")
        )

        total_expenses = (
            Expense.objects.filter(
                branch_id=branch_id,
                date_incurred__gte=period_start,
                date_incurred__lte=period_end,
            ).aggregate(t=Sum("amount"))["t"]
            or Decimal("0.00")
        )

        net_profit = total_income - total_expenses

        result = {
            "total_income": str(total_income),
            "total_expenses": str(total_expenses),
            "net_profit": str(net_profit),
            "period_start": str(period_start),
            "period_end": str(period_end),
        }

        # --- Store in cache ---
        try:
            from django.core.cache import cache

            cache.set(cache_key, result, timeout=3600)
        except Exception as cache_exc:
            logger.warning("FinancialService.compute_profit: cache.set failed: %s", cache_exc)

        return result

    # ------------------------------------------------------------------
    # get_dashboard_data
    # ------------------------------------------------------------------

    @staticmethod
    def get_dashboard_data(branch) -> dict:
        """
        Build the full financial dashboard payload for a branch.

        Returns:
          {
            "branch_id":             str,
            "daily":                 {income, expenses, profit, period_start, period_end},
            "weekly":                {income, expenses, profit, period_start, period_end},
            "monthly":               {income, expenses, profit, period_start, period_end},
            "revenue_trend":         [{date, income, expenses, profit}, ...],  # past 30 days
            "expense_breakdown":     [{category, total}, ...],
            "top_items_by_revenue":  [{menu_item_id, name, total_revenue}, ...],  # top 10
            "order_volume_by_hour":  {str(hour): count, ...},
          }

        Requirements: 13.3, 13.4
        """
        from apps.expenses.models import Expense
        from apps.financials.models import Income

        branch_id = str(branch.id) if hasattr(branch, "id") else str(branch)
        today = date.today()

        # --- Current period summaries ---
        daily = FinancialService.compute_profit(branch, "daily")
        weekly = FinancialService.compute_profit(branch, "weekly")
        monthly = FinancialService.compute_profit(branch, "monthly")

        # --- Revenue trend: past 30 days ---
        trend_start = today - timedelta(days=29)
        trend: list[dict] = []
        for offset in range(30):
            day = trend_start + timedelta(days=offset)
            day_income = (
                Income.objects.filter(
                    branch_id=branch_id,
                    date=day,
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            day_expenses = (
                Expense.objects.filter(
                    branch_id=branch_id,
                    date_incurred=day,
                ).aggregate(t=Sum("amount"))["t"]
                or Decimal("0.00")
            )
            trend.append(
                {
                    "date": str(day),
                    "income": str(day_income),
                    "expenses": str(day_expenses),
                    "profit": str(day_income - day_expenses),
                }
            )

        # --- Expense breakdown by category (current month) ---
        month_start = date(today.year, today.month, 1)
        breakdown_qs = (
            Expense.objects.filter(
                branch_id=branch_id,
                date_incurred__gte=month_start,
                date_incurred__lte=today,
            )
            .values("category")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
        expense_breakdown = [
            {"category": row["category"], "total": str(row["total"])}
            for row in breakdown_qs
        ]

        # --- Top-selling items by revenue (current month, top 10) ---
        try:
            from apps.orders.models import OrderItem

            top_items_qs = (
                OrderItem.objects.filter(
                    order__branch_id=branch_id,
                    order__placed_at__date__gte=month_start,
                    order__status="served",
                )
                .values("menu_item__name", "menu_item_id")
                .annotate(total_revenue=Sum("unit_price"))
                .order_by("-total_revenue")[:10]
            )
            top_items = [
                {
                    "menu_item_id": str(row["menu_item_id"]),
                    "name": row["menu_item__name"],
                    "total_revenue": str(row["total_revenue"]),
                }
                for row in top_items_qs
            ]
        except Exception as exc:
            logger.warning("get_dashboard_data: top items query failed: %s", exc)
            top_items = []

        # --- Order volume by hour (today) ---
        try:
            from apps.orders.models import Order

            orders_today = Order.objects.filter(
                branch_id=branch_id, placed_at__date=today
            )
            volume_by_hour: dict[str, int] = {}
            for order in orders_today:
                hour = str(order.placed_at.hour)
                volume_by_hour[hour] = volume_by_hour.get(hour, 0) + 1
        except Exception as exc:
            logger.warning("get_dashboard_data: volume by hour query failed: %s", exc)
            volume_by_hour = {}

        return {
            "branch_id": branch_id,
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
            "revenue_trend": trend,
            "expense_breakdown": expense_breakdown,
            "top_items_by_revenue": top_items,
            "order_volume_by_hour": volume_by_hour,
        }
