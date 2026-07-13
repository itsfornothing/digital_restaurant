"""
apps/financials/tests/test_financials_api.py

API-level tests for Income recording, financial dashboard, and related tasks.

Test cases:
  - record_income task creates an Income record from a served Order
  - record_income is idempotent (no duplicate Income for same Order)
  - Manual income creation via IncomeViewSet (IsBranchManager)
  - Receptionist cannot create income → 403
  - GET /api/v1/branches/{id}/financials/ → dashboard data
  - update_profit task correctly computes net_profit and caches result

Requirements: 13.1, 13.2
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch
from apps.financials.models import Income, ProfitRecord

User = get_user_model()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def income_list_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/income/"


def financials_dashboard_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/financials/"


def tenant_financials_url():
    return "/api/v1/tenant/financials/"


def report_export_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/reports/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Finance Test Branch",
        address="Bole Road, Addis Ababa",
        phone="0911111111",
        email="finance@branch.com",
    )


@pytest.fixture
def branch_manager(db, branch):
    return User.objects.create_user(
        email="fin.manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=branch,
    )


@pytest.fixture
def tenant_owner(db):
    return User.objects.create_user(
        email="fin.owner@tenant.com",
        password="Pass1234!",
        role="Tenant_Owner",
    )


@pytest.fixture
def receptionist(db, branch):
    return User.objects.create_user(
        email="fin.receptionist@branch.com",
        password="Pass1234!",
        role="Receptionist",
        branch=branch,
    )


@pytest.fixture
def table(db, branch):
    from apps.branches.models import Table
    return Table.objects.create(branch=branch, number="T1")


@pytest.fixture
def menu_item(db, branch):
    from apps.menus.models import MenuItem
    return MenuItem.objects.create(
        branch=branch,
        name="Injera",
        price=Decimal("80.00"),
        prep_time_minutes=10,
        status="available",
    )


@pytest.fixture
def served_order(db, branch, table, menu_item):
    from apps.orders.models import Order, OrderItem
    order = Order.objects.create(
        branch=branch,
        table=table,
        status="served",
        total_amount=Decimal("160.00"),
    )
    OrderItem.objects.create(
        order=order,
        menu_item=menu_item,
        quantity=2,
        unit_price=Decimal("80.00"),
    )
    return order


@pytest.fixture
def income_record(db, branch):
    return Income.objects.create(
        branch=branch,
        source="order",
        amount=Decimal("500.00"),
        date=date.today(),
    )


# ---------------------------------------------------------------------------
# record_income Celery task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRecordIncomeTask:
    """record_income task creates Income from a served Order."""

    def test_record_income_creates_income_record(self, served_order):
        """
        record_income(order_id) must create an Income record with:
          source='order', branch=order.branch, amount=order.total_amount,
          date=order.placed_at.date()

        Requirement 13.1.
        """
        from apps.financials.tasks import record_income

        with patch("apps.financials.tasks.update_profit.delay"):
            record_income(str(served_order.id))

        income = Income.objects.filter(order=served_order).first()
        assert income is not None, (
            "record_income: Expected an Income record to be created for the order"
        )
        assert income.source == "order"
        assert income.branch == served_order.branch
        assert income.amount == served_order.total_amount
        assert income.date == served_order.placed_at.date()

    def test_record_income_is_idempotent(self, served_order):
        """
        Calling record_income twice for the same order must NOT create
        a duplicate Income record.
        """
        from apps.financials.tasks import record_income

        with patch("apps.financials.tasks.update_profit.delay"):
            record_income(str(served_order.id))
            record_income(str(served_order.id))

        count = Income.objects.filter(order=served_order).count()
        assert count == 1, (
            f"record_income must be idempotent; expected 1 record, got {count}"
        )

    def test_record_income_triggers_update_profit(self, served_order):
        """record_income must call update_profit.delay after creating income."""
        from apps.financials.tasks import record_income

        with patch("apps.financials.tasks.update_profit.delay") as mock_delay:
            record_income(str(served_order.id))

        mock_delay.assert_called_once_with(str(served_order.branch_id), "daily")

    def test_record_income_nonexistent_order_does_not_raise(self):
        """record_income with an unknown order_id logs a warning but does not crash."""
        from apps.financials.tasks import record_income

        # Should not raise; should silently skip
        with patch("apps.financials.tasks.update_profit.delay"):
            record_income(str(uuid.uuid4()))

        # No income should have been created
        assert Income.objects.count() == 0


# ---------------------------------------------------------------------------
# update_profit Celery task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdateProfitTask:
    """update_profit computes net_profit and upserts a ProfitRecord."""

    def test_update_profit_creates_profit_record(self, branch):
        """
        update_profit must create a ProfitRecord for the branch/period.
        Requirement 13.2.
        """
        from apps.expenses.models import Expense
        from apps.financials.tasks import update_profit

        today = date.today()
        # Create income and expense
        Income.objects.create(
            branch=branch,
            source="other",
            amount=Decimal("1000.00"),
            date=today,
        )
        Expense.objects.create(
            branch=branch,
            description="Test expense",
            category="utilities",
            amount=Decimal("300.00"),
            date_incurred=today,
        )

        with patch("django.core.cache.cache.set"):
            update_profit(str(branch.id), "daily")

        record = ProfitRecord.objects.filter(
            branch=branch, period_type="daily"
        ).first()
        assert record is not None, "update_profit must create a ProfitRecord"
        assert record.total_income == Decimal("1000.00")
        assert record.total_expenses == Decimal("300.00")
        assert record.net_profit == Decimal("700.00")

    def test_update_profit_upserts_existing_record(self, branch):
        """Calling update_profit twice updates the existing ProfitRecord."""
        from apps.expenses.models import Expense
        from apps.financials.tasks import update_profit

        today = date.today()
        Income.objects.create(
            branch=branch, source="other", amount=Decimal("500.00"), date=today
        )

        with patch("django.core.cache.cache.set"):
            update_profit(str(branch.id), "daily")
            # Add more income and recompute
            Income.objects.create(
                branch=branch, source="other", amount=Decimal("200.00"), date=today
            )
            update_profit(str(branch.id), "daily")

        # Should still be only one ProfitRecord for today
        count = ProfitRecord.objects.filter(
            branch=branch, period_type="daily", period_start=today
        ).count()
        assert count == 1, "update_profit must upsert (not duplicate) the ProfitRecord"

        record = ProfitRecord.objects.get(
            branch=branch, period_type="daily", period_start=today
        )
        assert record.total_income == Decimal("700.00")

    def test_update_profit_net_profit_negative(self, branch):
        """Net profit is negative when expenses exceed income."""
        from apps.expenses.models import Expense
        from apps.financials.tasks import update_profit

        today = date.today()
        Income.objects.create(branch=branch, source="other", amount=Decimal("100.00"), date=today)
        Expense.objects.create(
            branch=branch,
            description="Heavy expense",
            category="rent",
            amount=Decimal("900.00"),
            date_incurred=today,
        )

        with patch("django.core.cache.cache.set"):
            update_profit(str(branch.id), "daily")

        record = ProfitRecord.objects.get(branch=branch, period_type="daily", period_start=today)
        assert record.net_profit == Decimal("-800.00")


# ---------------------------------------------------------------------------
# Manual income creation via IncomeViewSet
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIncomeViewSet:
    """Income API endpoints."""

    def test_branch_manager_can_create_manual_income(
        self, api_client, branch_manager, branch
    ):
        """Branch Manager can create a manual non-order income record."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.post(
                income_list_url(branch.id),
                {
                    "source": "event",
                    "amount": "1500.00",
                    "description": "Private dinner event",
                    "date": str(date.today()),
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {resp.status_code}: {resp.data}"
        )
        assert Income.objects.filter(branch=branch, source="event").exists()

    def test_receptionist_cannot_create_income(
        self, api_client, receptionist, branch
    ):
        """Receptionist cannot create income → 403."""
        api_client.force_authenticate(user=receptionist)
        resp = api_client.post(
            income_list_url(branch.id),
            {"source": "other", "amount": "100.00", "date": str(date.today())},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_branch_manager_can_list_income(
        self, api_client, branch_manager, branch, income_record
    ):
        """Branch Manager (IsFinancialReader) can list income records."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(income_list_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        ids = [r["id"] for r in resp.data]
        assert str(income_record.id) in ids

    def test_tenant_owner_can_list_income(
        self, api_client, tenant_owner, branch, income_record
    ):
        """Tenant_Owner (IsFinancialReader) can list income records."""
        api_client.force_authenticate(user=tenant_owner)
        resp = api_client.get(income_list_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_cannot_list_income(self, api_client, branch):
        """Unauthenticated access → 401/403."""
        resp = api_client.get(income_list_url(branch.id))
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# Financial Dashboard
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFinancialDashboard:
    """GET /api/v1/branches/{id}/financials/ returns KPI data."""

    def test_dashboard_returns_period_summaries(
        self, api_client, branch_manager, branch, income_record
    ):
        """Dashboard includes daily, weekly, monthly summaries."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(financials_dashboard_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK, (
            f"Expected 200, got {resp.status_code}: {resp.data}"
        )
        data = resp.data
        assert "daily" in data
        assert "weekly" in data
        assert "monthly" in data
        assert "top_selling_items" in data
        assert "order_volume_by_hour" in data
        assert "generated_at" in data

    def test_dashboard_income_totals_match(
        self, api_client, branch_manager, branch
    ):
        """Dashboard daily income total matches sum of today's Income records."""
        today = date.today()
        Income.objects.create(branch=branch, source="order", amount=Decimal("200.00"), date=today)
        Income.objects.create(branch=branch, source="event", amount=Decimal("300.00"), date=today)

        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(financials_dashboard_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        daily_income = Decimal(resp.data["daily"]["income"])
        assert daily_income == Decimal("500.00"), (
            f"Expected daily income=500.00, got {daily_income}"
        )

    def test_receptionist_cannot_access_dashboard(
        self, api_client, receptionist, branch
    ):
        """Receptionist is not IsFinancialReader → 403."""
        api_client.force_authenticate(user=receptionist)
        resp = api_client.get(financials_dashboard_url(branch.id))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_dashboard_nonexistent_branch_returns_404(
        self, api_client, branch_manager
    ):
        """Dashboard for a non-existent branch → 404."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(financials_dashboard_url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Report export
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFinancialReportExport:
    """POST /api/v1/branches/{id}/reports/ enqueues export task."""

    def test_export_returns_task_id(
        self, api_client, branch_manager, branch
    ):
        """Branch Manager can trigger a report export → 202 with task_id."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.export_report_csv.delay") as mock_task:
            mock_task.return_value.id = str(uuid.uuid4())
            resp = api_client.post(
                report_export_url(branch.id),
                {"format": "csv", "period": "monthly", "report_type": "financials"},
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert "task_id" in resp.data
        assert resp.data.get("status") == "queued"

    def test_receptionist_cannot_trigger_export(
        self, api_client, receptionist, branch
    ):
        """Receptionist cannot trigger report export → 403."""
        api_client.force_authenticate(user=receptionist)
        resp = api_client.post(
            report_export_url(branch.id),
            {"format": "csv"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Tenant consolidated view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConsolidatedFinancials:
    """GET /api/v1/tenant/financials/ returns aggregate across branches."""

    def test_tenant_owner_can_access_consolidated_view(
        self, api_client, tenant_owner, branch, income_record
    ):
        """Tenant_Owner can access the consolidated financial view."""
        api_client.force_authenticate(user=tenant_owner)
        resp = api_client.get(tenant_financials_url())
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert "total_income" in data
        assert "total_expenses" in data
        assert "net_profit" in data
        assert "branches" in data

    def test_branch_manager_cannot_access_consolidated_view(
        self, api_client, branch_manager
    ):
        """Branch Manager is not IsTenantOwner → 403."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(tenant_financials_url())
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_access_consolidated_view(self, api_client):
        """Unauthenticated access → 401/403."""
        resp = api_client.get(tenant_financials_url())
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
