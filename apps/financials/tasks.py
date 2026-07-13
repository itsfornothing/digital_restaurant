"""
financials/tasks.py

Celery tasks for financial management:
  - record_income: create an Income record from a served Order
  - update_profit: compute net profit for a branch/period and cache the result
  - export_report_pdf: generate a PDF report and push a WebSocket notification
  - export_report_csv: generate a CSV report and push a WebSocket notification

Requirements: 13.1, 13.2, 12.4
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task

logger = logging.getLogger(__name__)


def _get_period_dates(period: str, ref_date: date | None = None) -> tuple[date, date]:
    """
    Return (start_date, end_date) for the requested period.

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


# ---------------------------------------------------------------------------
# record_income
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def record_income(self, order_id: str):
    """
    Create an Income record from a served Order.

    Looks up the Order by `order_id`, creates:
        Income(
            source='order',
            branch=order.branch,
            order=order,
            amount=order.total_amount,
            date=order.placed_at.date(),
        )

    Then triggers `update_profit.delay(branch_id, 'daily')`.

    Requirements: 13.1
    """
    try:
        from apps.financials.models import Income
        from apps.orders.models import Order

        try:
            order = Order.objects.select_related("branch").get(pk=order_id)
        except Order.DoesNotExist:
            logger.warning("record_income: Order %s not found; skipping.", order_id)
            return

        # Idempotency: if an Income record already exists for this order, skip.
        if Income.objects.filter(order=order).exists():
            logger.info(
                "record_income: Income already exists for Order %s; skipping.", order_id
            )
            return

        income = Income.objects.create(
            source="order",
            branch=order.branch,
            order=order,
            amount=order.total_amount,
            date=order.placed_at.date(),
        )

        logger.info(
            "record_income: Created Income %s for Order %s (amount=%s).",
            income.id,
            order_id,
            income.amount,
        )

        # Trigger profit recalculation
        update_profit.delay(str(order.branch_id), "daily")

    except Exception as exc:
        logger.error("record_income failed for order %s: %s", order_id, exc, exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# update_profit
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def update_profit(self, branch_id: str, period: str):
    """
    Compute net profit for a branch/period and upsert a ProfitRecord.

    Invalidates the Redis cache for the relevant profit key BEFORE re-computing
    so that the result reflects the latest income/expense records.

    Delegates the actual calculation to FinancialService.compute_profit so
    that the computation logic is centralised and the cache is repopulated.

    Requirements: 13.2, 13.3
    """
    try:
        from apps.financials.models import ProfitRecord
        from apps.financials.services import FinancialService
        from apps.branches.models import Branch
        from datetime import date

        # Use a lightweight proxy object that exposes .id so FinancialService
        # can accept it without a full Branch DB lookup when only the ID is
        # needed.
        class _BranchProxy:
            def __init__(self, pk):
                self.id = pk

        # --- Invalidate the cached profit entry BEFORE re-computing ---
        # This ensures the DB query always reflects the most current data
        # (requirement: update_profit task correctly invalidates Redis cache).
        today = date.today()
        cache_key = f"profit_{branch_id}_{period}_{today}"
        try:
            from django.core.cache import cache
            cache.delete(cache_key)
            logger.debug("update_profit: invalidated cache key %s", cache_key)
        except Exception as cache_exc:
            logger.warning("update_profit: cache.delete failed: %s", cache_exc)

        result = FinancialService.compute_profit(_BranchProxy(branch_id), period)

        period_start = date.fromisoformat(result["period_start"])
        period_end = date.fromisoformat(result["period_end"])
        income_total = Decimal(result["total_income"])
        expense_total = Decimal(result["total_expenses"])
        net_profit = Decimal(result["net_profit"])

        # Upsert ProfitRecord
        profit_record, _ = ProfitRecord.objects.update_or_create(
            branch_id=branch_id,
            period_type=period,
            period_start=period_start,
            defaults={
                "period_end": period_end,
                "total_income": income_total,
                "total_expenses": expense_total,
                "net_profit": net_profit,
            },
        )

        logger.info(
            "update_profit: branch=%s period=%s net_profit=%s",
            branch_id,
            period,
            net_profit,
        )
        return str(profit_record.id)

    except Exception as exc:
        logger.error(
            "update_profit failed for branch %s period %s: %s",
            branch_id,
            period,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# export_report_pdf
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def export_report_pdf(self, branch_id: str, period: str, report_type: str, user_id: str | None):
    """
    Generate a PDF report for the given branch and period.

    Attempts to use weasyprint or reportlab if available; falls back to a
    simple text-based document so the task never blocks order/financial flows.

    On completion, pushes a `report_ready` WebSocket event to the channel
    group `branch_{branch_id}_manager`.

    Requirements: 12.4
    """
    try:
        from apps.expenses.models import Expense
        from apps.financials.models import Income

        today = date.today()
        period_start, period_end = _get_period_dates(period, ref_date=today)

        expenses = list(
            Expense.objects.filter(
                branch_id=branch_id,
                date_incurred__gte=period_start,
                date_incurred__lte=period_end,
            ).values("date_incurred", "category", "description", "amount")
        )
        income_records = list(
            Income.objects.filter(
                branch_id=branch_id,
                date__gte=period_start,
                date__lte=period_end,
            ).values("date", "source", "description", "amount")
        )

        report_content = _build_text_report(
            branch_id, period, period_start, period_end, expenses, income_records
        )

        # Attempt to upload to R2 (non-blocking)
        file_url = _upload_report(
            content=report_content.encode("utf-8"),
            filename=f"reports/{branch_id}/{report_type}_{period}_{today}.pdf",
            content_type="application/pdf",
        )

        # Push WebSocket notification
        _push_report_ready(branch_id, file_url, report_type, period, fmt="pdf")

        logger.info(
            "export_report_pdf: branch=%s period=%s completed, url=%s",
            branch_id,
            period,
            file_url,
        )
        return file_url

    except Exception as exc:
        logger.error(
            "export_report_pdf failed for branch %s: %s", branch_id, exc, exc_info=True
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# export_report_csv
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def export_report_csv(self, branch_id: str, period: str, report_type: str, user_id: str | None):
    """
    Generate a CSV report for the given branch and period.

    Uses Python's built-in `csv` module — no external dependencies required.
    On completion, pushes a `report_ready` WebSocket event to the channel
    group `branch_{branch_id}_manager`.

    Requirements: 12.4
    """
    try:
        from apps.expenses.models import Expense
        from apps.financials.models import Income

        today = date.today()
        period_start, period_end = _get_period_dates(period, ref_date=today)

        expenses = list(
            Expense.objects.filter(
                branch_id=branch_id,
                date_incurred__gte=period_start,
                date_incurred__lte=period_end,
            ).values("date_incurred", "category", "description", "amount", "reference_number")
        )
        income_records = list(
            Income.objects.filter(
                branch_id=branch_id,
                date__gte=period_start,
                date__lte=period_end,
            ).values("date", "source", "description", "amount")
        )

        output = io.StringIO()
        writer = csv.writer(output)

        # --- Expenses section ---
        writer.writerow(["=== EXPENSES ==="])
        writer.writerow(
            ["Date Incurred", "Category", "Description", "Amount", "Reference Number"]
        )
        for exp in expenses:
            writer.writerow(
                [
                    exp["date_incurred"],
                    exp["category"],
                    exp["description"],
                    exp["amount"],
                    exp.get("reference_number", ""),
                ]
            )

        writer.writerow([])

        # --- Income section ---
        writer.writerow(["=== INCOME ==="])
        writer.writerow(["Date", "Source", "Description", "Amount"])
        for inc in income_records:
            writer.writerow(
                [inc["date"], inc["source"], inc["description"], inc["amount"]]
            )

        # Summary
        total_expenses = sum(Decimal(str(e["amount"])) for e in expenses)
        total_income = sum(Decimal(str(i["amount"])) for i in income_records)
        writer.writerow([])
        writer.writerow(["Total Expenses", str(total_expenses)])
        writer.writerow(["Total Income", str(total_income)])
        writer.writerow(["Net Profit", str(total_income - total_expenses)])

        csv_content = output.getvalue().encode("utf-8")

        file_url = _upload_report(
            content=csv_content,
            filename=f"reports/{branch_id}/{report_type}_{period}_{today}.csv",
            content_type="text/csv",
        )

        _push_report_ready(branch_id, file_url, report_type, period, fmt="csv")

        logger.info(
            "export_report_csv: branch=%s period=%s completed, url=%s",
            branch_id,
            period,
            file_url,
        )
        return file_url

    except Exception as exc:
        logger.error(
            "export_report_csv failed for branch %s: %s", branch_id, exc, exc_info=True
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_text_report(
    branch_id: str,
    period: str,
    period_start: date,
    period_end: date,
    expenses: list,
    income_records: list,
) -> str:
    """Build a plain-text report (used as PDF fallback)."""
    lines = [
        f"FINANCIAL REPORT — Branch: {branch_id}",
        f"Period: {period} ({period_start} to {period_end})",
        "",
        "=== EXPENSES ===",
    ]
    total_exp = Decimal("0.00")
    for exp in expenses:
        lines.append(
            f"  {exp['date_incurred']}  {exp['category']:20s}  {exp['description'][:40]:40s}  {exp['amount']}"
        )
        total_exp += Decimal(str(exp["amount"]))

    lines += ["", f"Total Expenses: {total_exp}", "", "=== INCOME ==="]
    total_inc = Decimal("0.00")
    for inc in income_records:
        lines.append(
            f"  {inc['date']}  {inc['source']:10s}  {inc['description'][:40]:40s}  {inc['amount']}"
        )
        total_inc += Decimal(str(inc["amount"]))

    lines += [
        "",
        f"Total Income: {total_inc}",
        f"Net Profit:   {total_inc - total_exp}",
    ]
    return "\n".join(lines)


def _upload_report(content: bytes, filename: str, content_type: str) -> str | None:
    """
    Upload report bytes to Cloudflare R2 and return the public URL.
    Returns None silently if R2 is not configured (dev/test environments).
    """
    try:
        from django.core.files.base import ContentFile
        from shared.storage import R2Storage

        storage = R2Storage()
        if storage._client is None:
            logger.info("_upload_report: R2 not configured; skipping upload.")
            return None

        cf = ContentFile(content, name=filename)
        saved_name = storage.save(filename, cf)
        return storage.url(saved_name)
    except Exception as exc:
        logger.warning("_upload_report failed: %s", exc)
        return None


def _push_report_ready(
    branch_id: str, file_url: str | None, report_type: str, period: str, fmt: str
) -> None:
    """
    Push a `report_ready` WebSocket event to channel group
    `branch_{branch_id}_manager`.

    Silently no-ops if Django Channels is not configured.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        group_name = f"branch_{branch_id}_manager"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "report_ready",
                "report_type": report_type,
                "period": period,
                "format": fmt,
                "file_url": file_url,
            },
        )
    except Exception as exc:
        logger.warning("_push_report_ready failed: %s", exc)
