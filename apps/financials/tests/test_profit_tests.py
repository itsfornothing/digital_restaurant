"""
apps/financials/tests/test_profit_tests.py

Profit calculation tests covering core financial logic.

Test cases:
  TC-P01: Income=1000, Expense=300 → net_profit=700
  TC-P02: Add expense 100 → profit decreases by 100
  TC-P03: Income=500, no expenses → profit=500
  TC-P04: Expenses=800 > Income=300 → profit=-500 (negative is valid)
  TC-P05: record_income for 3 served orders totalling 450 ETB → income=450
  TC-API12: GET /api/v1/branches/{id}/financials/ → dashboard profit matches income-expenses

Each case also tests FinancialService.compute_profit() directly.

Requirements: 13.1, 13.2, 13.3, 13.4
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch
from apps.expenses.models import Expense
from apps.financials.models import Income, ProfitRecord
from apps.financials.services import FinancialService

User = get_user_model()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def financials_dashboard_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/financials/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Profit Test Branch",
        address="Churchill Ave, Addis Ababa",
        phone="0922222222",
        email="profit@branch.com",
    )


@pytest.fixture
def branch_manager(db, branch):
    return User.objects.create_user(
        email="profit.manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=branch,
    )


@pytest.fixture
def table(db, branch):
    from apps.branches.models import Table
    return Table.objects.create(branch=branch, number="P1")


@pytest.fixture
def menu_item(db, branch):
    from apps.menus.models import MenuItem
    return MenuItem.objects.create(
        branch=branch,
        name="Tibs",
        price=Decimal("150.00"),
        prep_time_minutes=15,
        status="available",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_income(branch, amount: str) -> Income:
    """Create an Income record for today with the given amount."""
    return Income.objects.create(
        branch=branch,
        source="other",
        amount=Decimal(amount),
        date=date.today(),
    )


def _make_expense(branch, amount: str, category: str = "utilities") -> Expense:
    """Create an Expense record for today with the given amount."""
    return Expense.objects.create(
        branch=branch,
        description="Test expense",
        category=category,
        amount=Decimal(amount),
        date_incurred=date.today(),
    )


# ---------------------------------------------------------------------------
# TC-P01: Income=1000, Expense=300 → net_profit=700
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_p01_profit_formula(db, branch):
    """
    TC-P01: Basic profit formula.

    Given income=1000 and expenses=300, net_profit must equal 700.

    Validates: Requirements 13.2, 13.3
    """
    _make_income(branch, "1000.00")
    _make_expense(branch, "300.00")

    # Test via FinancialService.compute_profit directly
    result = FinancialService.compute_profit(branch, "daily")

    assert Decimal(result["total_income"]) == Decimal("1000.00"), (
        f"Expected total_income=1000.00, got {result['total_income']}"
    )
    assert Decimal(result["total_expenses"]) == Decimal("300.00"), (
        f"Expected total_expenses=300.00, got {result['total_expenses']}"
    )
    assert Decimal(result["net_profit"]) == Decimal("700.00"), (
        f"Expected net_profit=700.00, got {result['net_profit']}"
    )


# ---------------------------------------------------------------------------
# TC-P02: Add expense 100 → profit decreases by 100
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_p02_profit_decreases_on_expense(db, branch):
    """
    TC-P02: Adding an expense reduces profit by that amount.

    Given initial income=500 and expenses=200, profit=300.
    After adding expense=100, profit must decrease to 200.

    Validates: Requirements 13.2, 13.3
    """
    _make_income(branch, "500.00")
    _make_expense(branch, "200.00")

    # Clear cache to force fresh DB read
    from django.core.cache import cache
    cache.clear()

    result_before = FinancialService.compute_profit(branch, "daily")
    profit_before = Decimal(result_before["net_profit"])

    # Add another expense of 100
    _make_expense(branch, "100.00")

    # Clear cache again so new expense is picked up
    cache.clear()

    result_after = FinancialService.compute_profit(branch, "daily")
    profit_after = Decimal(result_after["net_profit"])

    assert profit_after == profit_before - Decimal("100.00"), (
        f"Expected profit to decrease by 100 from {profit_before}, got {profit_after}"
    )


# ---------------------------------------------------------------------------
# TC-P03: Income=500, no expenses → profit=500
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_p03_profit_no_expenses(db, branch):
    """
    TC-P03: When there are no expenses, profit equals income.

    Validates: Requirements 13.2, 13.3
    """
    _make_income(branch, "500.00")

    result = FinancialService.compute_profit(branch, "daily")

    assert Decimal(result["total_expenses"]) == Decimal("0.00"), (
        f"Expected total_expenses=0.00, got {result['total_expenses']}"
    )
    assert Decimal(result["net_profit"]) == Decimal("500.00"), (
        f"Expected net_profit=500.00, got {result['net_profit']}"
    )


# ---------------------------------------------------------------------------
# TC-P04: Expenses=800 > Income=300 → profit=-500 (negative is valid)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_p04_negative_profit(db, branch):
    """
    TC-P04: Net profit can be negative when expenses exceed income.

    Given income=300 and expenses=800, net_profit must equal -500.

    Validates: Requirements 13.2, 13.3
    """
    _make_income(branch, "300.00")
    _make_expense(branch, "800.00")

    result = FinancialService.compute_profit(branch, "daily")

    assert Decimal(result["net_profit"]) == Decimal("-500.00"), (
        f"Expected net_profit=-500.00, got {result['net_profit']}"
    )
    assert Decimal(result["net_profit"]) < Decimal("0.00"), (
        "Negative profit must be accepted as valid (not clamped to zero)"
    )


# ---------------------------------------------------------------------------
# TC-P05: record_income for 3 served orders totalling 450 ETB → income=450
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_p05_served_orders_income(db, branch, table, menu_item):
    """
    TC-P05: record_income for 3 served orders totalling 450 ETB → total income=450.

    Three orders at 150 ETB each are created (status='served').
    record_income is called for each order.
    The resulting total daily income must equal 450.

    Validates: Requirements 13.1, 13.2
    """
    from apps.financials.tasks import record_income
    from apps.orders.models import Order, OrderItem

    amounts = [Decimal("150.00"), Decimal("150.00"), Decimal("150.00")]
    orders = []
    for amount in amounts:
        order = Order.objects.create(
            branch=branch,
            table=table,
            status="served",
            total_amount=amount,
        )
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=1,
            unit_price=amount,
        )
        orders.append(order)

    # Call record_income for each order (patch update_profit.delay to avoid side effects)
    with patch("apps.financials.tasks.update_profit.delay"):
        for order in orders:
            record_income(str(order.id))

    # Sum of income records for today for this branch
    today = date.today()
    total_income = (
        Income.objects.filter(branch=branch, date=today)
        .aggregate(t=__import__("django.db.models", fromlist=["Sum"]).Sum("amount"))["t"]
        or Decimal("0.00")
    )

    assert total_income == Decimal("450.00"), (
        f"Expected total income=450.00 for 3 orders, got {total_income}"
    )

    # Also verify via FinancialService.compute_profit
    result = FinancialService.compute_profit(branch, "daily")
    assert Decimal(result["total_income"]) == Decimal("450.00"), (
        f"FinancialService.compute_profit total_income expected 450.00, got {result['total_income']}"
    )


# ---------------------------------------------------------------------------
# TC-API12: GET /api/v1/branches/{id}/financials/ → dashboard profit matches
#           income - expenses
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tc_api12_dashboard_profit_matches_calculation(
    db, api_client, branch_manager, branch
):
    """
    TC-API12: Dashboard daily profit field must equal (income - expenses).

    Creates known income and expense records for today, then requests the
    financial dashboard and asserts that the profit value in the response
    matches the expected calculation.

    Validates: Requirements 13.2, 13.4
    """
    today = date.today()

    # Arrange: 750 income, 250 expenses → expected profit = 500
    Income.objects.create(
        branch=branch, source="order", amount=Decimal("750.00"), date=today
    )
    Expense.objects.create(
        branch=branch,
        description="Daily supplies",
        category="food_purchases",
        amount=Decimal("250.00"),
        date_incurred=today,
    )

    api_client.force_authenticate(user=branch_manager)
    resp = api_client.get(financials_dashboard_url(branch.id))

    assert resp.status_code == status.HTTP_200_OK, (
        f"Expected 200, got {resp.status_code}: {resp.data}"
    )

    daily_data = resp.data.get("daily", {})
    dashboard_income = Decimal(daily_data["income"])
    dashboard_expenses = Decimal(daily_data["expenses"])
    dashboard_profit = Decimal(daily_data["profit"])

    expected_profit = dashboard_income - dashboard_expenses
    assert dashboard_profit == expected_profit, (
        f"Dashboard profit {dashboard_profit} does not match "
        f"income ({dashboard_income}) - expenses ({dashboard_expenses}) = {expected_profit}"
    )
    assert dashboard_profit == Decimal("500.00"), (
        f"Expected dashboard profit=500.00, got {dashboard_profit}"
    )

    # Cross-check: FinancialService.compute_profit must agree with dashboard
    service_result = FinancialService.compute_profit(branch, "daily")
    service_profit = Decimal(service_result["net_profit"])
    assert service_profit == dashboard_profit, (
        f"FinancialService net_profit ({service_profit}) must match dashboard profit ({dashboard_profit})"
    )
