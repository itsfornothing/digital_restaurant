"""
tests/e2e/test_financial_dashboard.py

E2E-02: Manager financial dashboard
Validates: Requirements 12.2, 13.3, 13.4

Simulates a Branch Manager:
  1. Authenticating and creating an Expense record
  2. Checking that the financial dashboard reflects income and expense totals
  3. Updating the expense amount and verifying the dashboard re-computes profit
  4. Verifying that the audit log captures the old and new expense amounts

Setup:
  - One Branch with 900 ETB of Income (source='order', created directly)
  - One Branch Manager user linked to that branch
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.authentication.models import User, UserRole
from apps.branches.models import Branch
from apps.financials.models import Income


# ---------------------------------------------------------------------------
# Local fixtures (do not modify shared conftest.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def branch_manager_setup(db):
    """
    Create a Branch, a Branch Manager user linked to that branch, and
    900 ETB of Income for today.

    Returns:
        tuple: (branch, branch_manager_user)
    """
    branch = Branch.objects.create(
        name="Financial Dashboard E2E Branch",
        address="456 Finance Street, Addis Ababa",
        phone="0922334455",
        email="finance-e2e@restaurant.com",
    )

    branch_manager = User.objects.create_user(
        email="branch.manager.e2e@restaurant.com",
        password="SecurePass!2024",
        role=UserRole.BRANCH_MANAGER,
        branch=branch,
    )

    # Create 900 ETB income directly (source='order') for today
    Income.objects.create(
        branch=branch,
        source="order",
        amount=Decimal("900.00"),
        description="Order income for E2E test",
        date=date.today(),
    )

    return branch, branch_manager


# ---------------------------------------------------------------------------
# E2E Test Class
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.e2e
class TestFinancialDashboardE2E02:
    """
    E2E-02: Manager financial dashboard

    Tests the full financial dashboard workflow:
      1. Branch Manager creates an Expense
      2. Dashboard returns correct income, expenses, and profit totals
      3. Expense is updated; dashboard reflects new profit in real time
      4. Audit log captures old and new expense amounts with correct user and timestamp

    Validates: Requirements 12.2, 13.3, 13.4
    """

    # ------------------------------------------------------------------
    # Step 1: POST /api/v1/branches/{id}/expenses/ → 201
    # ------------------------------------------------------------------

    def test_step1_create_expense_returns_201(self, branch_manager_setup):
        """
        Step 1: POST expense → 201 with expense_id.

        Validates: Requirement 12.1
        """
        branch, branch_manager = branch_manager_setup
        client = APIClient()
        client.force_authenticate(user=branch_manager)

        response = client.post(
            f"/api/v1/branches/{branch.id}/expenses/",
            {
                "description": "Electricity",
                "category": "utilities",
                "amount": "350.00",
                "date_incurred": str(date.today()),
            },
            format="json",
        )

        assert response.status_code == 201, (
            f"Expected 201 when creating expense, got {response.status_code}: {response.data}"
        )

        data = response.data
        assert "id" in data, "Response must contain expense id"
        assert data["description"] == "Electricity"
        assert Decimal(data["amount"]) == Decimal("350.00")

    # ------------------------------------------------------------------
    # Step 2: GET /api/v1/branches/{id}/financials/ → income=900, expenses=350, profit=550
    # ------------------------------------------------------------------

    def test_step2_dashboard_reflects_income_and_expense(self, branch_manager_setup):
        """
        Step 2: GET financial dashboard → income=900, expenses=350, profit=550.

        Validates: Requirement 13.3
        """
        branch, branch_manager = branch_manager_setup
        client = APIClient()
        client.force_authenticate(user=branch_manager)

        # Create the expense first
        expense_response = client.post(
            f"/api/v1/branches/{branch.id}/expenses/",
            {
                "description": "Electricity",
                "category": "utilities",
                "amount": "350.00",
                "date_incurred": str(date.today()),
            },
            format="json",
        )
        assert expense_response.status_code == 201

        # Get financial dashboard
        dashboard_response = client.get(f"/api/v1/branches/{branch.id}/financials/")

        assert dashboard_response.status_code == 200, (
            f"Expected 200 from financial dashboard, got {dashboard_response.status_code}: "
            f"{dashboard_response.data}"
        )

        data = dashboard_response.data
        assert "daily" in data, "Dashboard response must contain 'daily' period summary"

        daily = data["daily"]
        assert Decimal(daily["income"]) == Decimal("900.00"), (
            f"Expected income=900.00, got {daily['income']}"
        )
        assert Decimal(daily["expenses"]) == Decimal("350.00"), (
            f"Expected expenses=350.00, got {daily['expenses']}"
        )
        assert Decimal(daily["profit"]) == Decimal("550.00"), (
            f"Expected profit=550.00, got {daily['profit']}"
        )

    # ------------------------------------------------------------------
    # Step 3: PATCH /api/v1/expenses/{id}/ → 200; dashboard profit updated
    # ------------------------------------------------------------------

    def test_step3_update_expense_updates_profit(self, branch_manager_setup):
        """
        Step 3: PATCH expense amount to 200 → dashboard profit becomes 700.

        Validates: Requirement 13.3 (real-time profit update on expense modification)
        """
        branch, branch_manager = branch_manager_setup
        client = APIClient()
        client.force_authenticate(user=branch_manager)

        # Create the expense
        expense_response = client.post(
            f"/api/v1/branches/{branch.id}/expenses/",
            {
                "description": "Electricity",
                "category": "utilities",
                "amount": "350.00",
                "date_incurred": str(date.today()),
            },
            format="json",
        )
        assert expense_response.status_code == 201
        expense_id = expense_response.data["id"]

        # Update the expense amount
        patch_response = client.patch(
            f"/api/v1/expenses/{expense_id}/",
            {"amount": "200.00"},
            format="json",
        )

        assert patch_response.status_code == 200, (
            f"Expected 200 from expense PATCH, got {patch_response.status_code}: "
            f"{patch_response.data}"
        )
        assert Decimal(patch_response.data["amount"]) == Decimal("200.00"), (
            f"Expected updated amount=200.00, got {patch_response.data['amount']}"
        )

        # Get updated financial dashboard
        dashboard_response = client.get(f"/api/v1/branches/{branch.id}/financials/")
        assert dashboard_response.status_code == 200

        daily = dashboard_response.data["daily"]
        assert Decimal(daily["income"]) == Decimal("900.00"), (
            f"Expected income still 900.00 after expense update, got {daily['income']}"
        )
        assert Decimal(daily["expenses"]) == Decimal("200.00"), (
            f"Expected expenses=200.00 after PATCH, got {daily['expenses']}"
        )
        assert Decimal(daily["profit"]) == Decimal("700.00"), (
            f"Expected profit=700.00 after expense update, got {daily['profit']}"
        )

    # ------------------------------------------------------------------
    # Step 4: GET /api/v1/audit-logs/?action=EXPENSE_UPDATE → audit entry present
    # ------------------------------------------------------------------

    def test_step4_audit_log_records_expense_update(self, branch_manager_setup):
        """
        Step 4: Audit log contains EXPENSE_UPDATE entry with old/new amount values,
        correct user_id, and a valid ISO-8601 timestamp.

        Validates: Requirement 12.2, 13.4
        """
        branch, branch_manager = branch_manager_setup
        client = APIClient()
        client.force_authenticate(user=branch_manager)

        # Create and then update the expense
        expense_response = client.post(
            f"/api/v1/branches/{branch.id}/expenses/",
            {
                "description": "Electricity",
                "category": "utilities",
                "amount": "350.00",
                "date_incurred": str(date.today()),
            },
            format="json",
        )
        assert expense_response.status_code == 201
        expense_id = expense_response.data["id"]

        patch_response = client.patch(
            f"/api/v1/expenses/{expense_id}/",
            {"amount": "200.00"},
            format="json",
        )
        assert patch_response.status_code == 200

        # Query audit log for EXPENSE_UPDATE action
        audit_response = client.get("/api/v1/audit-logs/?action=EXPENSE_UPDATE")

        assert audit_response.status_code == 200, (
            f"Expected 200 from audit log query, got {audit_response.status_code}: "
            f"{audit_response.data}"
        )

        # Handle paginated or unpaginated results
        audit_data = audit_response.data
        if isinstance(audit_data, dict):
            entries = audit_data.get("results", [])
        else:
            entries = list(audit_data)

        assert len(entries) > 0, (
            "Expected at least one EXPENSE_UPDATE entry in audit log after patching expense"
        )

        # Find the entry matching our expense update (resource_id matches expense_id)
        matching_entries = [
            e for e in entries
            if str(e.get("resource_id")) == str(expense_id)
        ]
        assert len(matching_entries) >= 1, (
            f"Expected audit log entry for expense_id={expense_id}, "
            f"found entries with resource_ids: {[e.get('resource_id') for e in entries]}"
        )

        entry = matching_entries[0]

        # Verify old_value contains the original amount (350)
        old_value = entry.get("old_value")
        assert old_value is not None, "Audit log entry must have old_value"
        assert "amount" in old_value, f"old_value must contain 'amount' key, got: {old_value}"
        assert Decimal(str(old_value["amount"])) == Decimal("350.00"), (
            f"old_value.amount should be 350.00, got {old_value['amount']}"
        )

        # Verify new_value contains the updated amount (200)
        new_value = entry.get("new_value")
        assert new_value is not None, "Audit log entry must have new_value"
        assert "amount" in new_value, f"new_value must contain 'amount' key, got: {new_value}"
        assert Decimal(str(new_value["amount"])) == Decimal("200.00"), (
            f"new_value.amount should be 200.00, got {new_value['amount']}"
        )

        # Verify user_id matches the branch manager
        assert str(entry.get("user_id")) == str(branch_manager.id), (
            f"Audit log entry user_id {entry.get('user_id')} must match "
            f"branch manager id {branch_manager.id}"
        )

        # Verify timestamp is a valid ISO-8601 datetime string
        timestamp = entry.get("timestamp")
        assert timestamp is not None, "Audit log entry must have a timestamp"
        iso8601_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        )
        assert iso8601_pattern.match(str(timestamp)), (
            f"Audit log timestamp '{timestamp}' must be a valid ISO-8601 datetime"
        )

    # ------------------------------------------------------------------
    # Integrated E2E test (all steps in one test)
    # ------------------------------------------------------------------

    def test_complete_financial_dashboard_flow_e2e(self, branch_manager_setup):
        """
        Complete E2E-02: all 4 steps in sequence.

        1. Authenticate as Branch Manager
        2. POST expense (Electricity, 350 ETB) → 201, save expense_id
        3. GET financials → income=900, expenses=350, profit=550
        4. PATCH expense amount to 200 → 200
        5. GET financials → profit=700 (real-time update)
        6. GET audit-logs?action=EXPENSE_UPDATE → entry with old=350, new=200,
           matching user_id, valid timestamp

        Validates: Requirements 12.2, 13.3, 13.4 (E2E-02)
        """
        branch, branch_manager = branch_manager_setup
        client = APIClient()

        # Step 1: Authenticate as Branch Manager
        client.force_authenticate(user=branch_manager)

        # Step 2: Create expense
        expense_response = client.post(
            f"/api/v1/branches/{branch.id}/expenses/",
            {
                "description": "Electricity",
                "category": "utilities",
                "amount": "350.00",
                "date_incurred": str(date.today()),
            },
            format="json",
        )
        assert expense_response.status_code == 201, (
            f"Step 2 failed — expected 201 creating expense, got "
            f"{expense_response.status_code}: {expense_response.data}"
        )
        expense_id = expense_response.data["id"]

        # Step 3: GET financial dashboard — verify income=900, expenses=350, profit=550
        dash1_response = client.get(f"/api/v1/branches/{branch.id}/financials/")
        assert dash1_response.status_code == 200, (
            f"Step 3 failed — expected 200 from dashboard, got "
            f"{dash1_response.status_code}: {dash1_response.data}"
        )

        daily1 = dash1_response.data["daily"]
        assert Decimal(daily1["income"]) == Decimal("900.00"), (
            f"Step 3 failed — income expected 900.00, got {daily1['income']}"
        )
        assert Decimal(daily1["expenses"]) == Decimal("350.00"), (
            f"Step 3 failed — expenses expected 350.00, got {daily1['expenses']}"
        )
        assert Decimal(daily1["profit"]) == Decimal("550.00"), (
            f"Step 3 failed — profit expected 550.00, got {daily1['profit']}"
        )

        # Step 4: PATCH expense amount to 200
        patch_response = client.patch(
            f"/api/v1/expenses/{expense_id}/",
            {"amount": "200.00"},
            format="json",
        )
        assert patch_response.status_code == 200, (
            f"Step 4 failed — expected 200 from PATCH, got "
            f"{patch_response.status_code}: {patch_response.data}"
        )

        # Step 5: GET financial dashboard — verify profit updated to 700 in real time
        dash2_response = client.get(f"/api/v1/branches/{branch.id}/financials/")
        assert dash2_response.status_code == 200, (
            f"Step 5 failed — expected 200 from updated dashboard, got "
            f"{dash2_response.status_code}: {dash2_response.data}"
        )

        daily2 = dash2_response.data["daily"]
        assert Decimal(daily2["expenses"]) == Decimal("200.00"), (
            f"Step 5 failed — expenses expected 200.00 after PATCH, got {daily2['expenses']}"
        )
        assert Decimal(daily2["profit"]) == Decimal("700.00"), (
            f"Step 5 failed — profit expected 700.00 after PATCH, got {daily2['profit']}"
        )

        # Step 6: GET audit logs filtered by EXPENSE_UPDATE action
        audit_response = client.get("/api/v1/audit-logs/?action=EXPENSE_UPDATE")
        assert audit_response.status_code == 200, (
            f"Step 6 failed — expected 200 from audit-logs, got "
            f"{audit_response.status_code}: {audit_response.data}"
        )

        audit_data = audit_response.data
        if isinstance(audit_data, dict):
            entries = audit_data.get("results", [])
        else:
            entries = list(audit_data)

        # Find the matching audit entry for this expense update
        matching_entries = [
            e for e in entries
            if str(e.get("resource_id")) == str(expense_id)
        ]
        assert len(matching_entries) >= 1, (
            f"Step 6 failed — no EXPENSE_UPDATE audit entry found for expense_id={expense_id}. "
            f"All entries: {[{'resource_id': e.get('resource_id'), 'action': e.get('action')} for e in entries]}"
        )

        entry = matching_entries[0]

        # old_value must contain original amount 350
        old_value = entry.get("old_value")
        assert old_value is not None, "Step 6 failed — audit entry missing old_value"
        assert Decimal(str(old_value["amount"])) == Decimal("350.00"), (
            f"Step 6 failed — old_value.amount expected 350.00, got {old_value.get('amount')}"
        )

        # new_value must contain updated amount 200
        new_value = entry.get("new_value")
        assert new_value is not None, "Step 6 failed — audit entry missing new_value"
        assert Decimal(str(new_value["amount"])) == Decimal("200.00"), (
            f"Step 6 failed — new_value.amount expected 200.00, got {new_value.get('amount')}"
        )

        # user_id must match branch manager
        assert str(entry.get("user_id")) == str(branch_manager.id), (
            f"Step 6 failed — audit entry user_id {entry.get('user_id')} "
            f"must match branch manager id {branch_manager.id}"
        )

        # timestamp must be a valid ISO-8601 datetime
        timestamp = entry.get("timestamp")
        assert timestamp is not None, "Step 6 failed — audit entry missing timestamp"
        iso8601_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        assert iso8601_pattern.match(str(timestamp)), (
            f"Step 6 failed — timestamp '{timestamp}' must be a valid ISO-8601 datetime"
        )
