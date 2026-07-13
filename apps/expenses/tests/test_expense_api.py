"""
apps/expenses/tests/test_expense_api.py

API-level tests for Expense management (Task 13.5).

Test cases:
  TC-F01: POST /api/v1/branches/{id}/expenses/ → 201, expense created
  TC-F02: POST with amount = -500 → 400
  TC-F03: PATCH amount 500→200 → audit log shows old=500, new=200
  TC-F04: DELETE → audit log records deletion with old_value
  TC-F05: GET expense report for current month → totals match; breakdown correct
  TC-API09: POST /api/v1/branches/{id}/expenses/ as Receptionist → 403
  TC-API10: PATCH /api/v1/expenses/{id}/ as Manager → 200, audit log entry created
  Additional: export endpoint, cross-branch isolation, unauthenticated access

Requirements: 12.1, 12.2, 12.3, 12.4
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
from apps.expenses.models import Expense

User = get_user_model()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def expense_list_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/expenses/"


def expense_detail_url(pk):
    return f"/api/v1/expenses/{pk}/"


def expense_report_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/expenses/report/"


def expense_export_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/expenses/export/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Test Branch",
        address="Bole Road, Addis Ababa",
        phone="0911000001",
        email="branch@test.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="Piazza, Addis Ababa",
        phone="0911000002",
        email="other@test.com",
    )


@pytest.fixture
def branch_manager(db, branch):
    return User.objects.create_user(
        email="manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=branch,
    )


@pytest.fixture
def other_manager(db, other_branch):
    return User.objects.create_user(
        email="other.manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=other_branch,
    )


@pytest.fixture
def tenant_owner(db):
    return User.objects.create_user(
        email="owner@tenant.com",
        password="Pass1234!",
        role="Tenant_Owner",
    )


@pytest.fixture
def receptionist(db, branch):
    return User.objects.create_user(
        email="receptionist@branch.com",
        password="Pass1234!",
        role="Receptionist",
        branch=branch,
    )


@pytest.fixture
def expense(db, branch):
    return Expense.objects.create(
        branch=branch,
        description="Monthly grocery purchase",
        category="food_purchases",
        amount=Decimal("500.00"),
        date_incurred=date.today(),
    )


@pytest.fixture
def expense_payload():
    return {
        "description": "Office supplies",
        "category": "miscellaneous",
        "amount": "250.00",
        "date_incurred": str(date.today()),
    }


# ---------------------------------------------------------------------------
# TC-F01: POST → 201, expense created
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseCreate:
    """TC-F01: POST creates an expense record and returns 201."""

    def test_branch_manager_can_create_expense(
        self, api_client, branch_manager, branch, expense_payload
    ):
        """
        TC-F01: Branch Manager POSTs a valid expense → 201 Created.
        Expense record is persisted with the correct branch and amount.
        """
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.post(
                expense_list_url(branch.id),
                expense_payload,
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED, (
            f"TC-F01: Expected 201, got {resp.status_code}: {resp.data}"
        )
        assert Expense.objects.filter(
            branch=branch, description=expense_payload["description"]
        ).exists()

    def test_create_increases_branch_expense_count(
        self, api_client, branch_manager, branch, expense_payload
    ):
        """TC-F01: Creating an expense increases the branch's expense count."""
        api_client.force_authenticate(user=branch_manager)
        before = Expense.objects.filter(branch=branch).count()
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.post(
                expense_list_url(branch.id),
                expense_payload,
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED
        assert Expense.objects.filter(branch=branch).count() == before + 1

    def test_created_expense_has_correct_fields(
        self, api_client, branch_manager, branch, expense_payload
    ):
        """Response body includes all expected fields."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.post(
                expense_list_url(branch.id),
                expense_payload,
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.data
        assert "id" in data
        assert "branch_id" in data
        assert data["amount"] == expense_payload["amount"]
        assert data["category"] == expense_payload["category"]


# ---------------------------------------------------------------------------
# TC-F02: POST with amount = -500 → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseNegativeAmount:
    """TC-F02: Negative or zero amount is rejected with 400."""

    @pytest.mark.parametrize("bad_amount", ["-500.00", "-0.01", "0.00"])
    def test_negative_or_zero_amount_rejected(
        self, api_client, branch_manager, branch, bad_amount
    ):
        """
        TC-F02: Amounts ≤ 0 must return 400.
        MinValueValidator(0.01) enforces this at the model level.
        """
        api_client.force_authenticate(user=branch_manager)
        payload = {
            "description": "Invalid expense",
            "category": "utilities",
            "amount": bad_amount,
            "date_incurred": str(date.today()),
        }
        resp = api_client.post(
            expense_list_url(branch.id),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, (
            f"TC-F02: Expected 400 for amount={bad_amount}, got {resp.status_code}"
        )
        # The custom exception handler wraps errors: {"error": {"code": ..., "message": "..."}}
        # Validation errors surface the field name and message in the "message" string.
        assert "error" in resp.data, f"TC-F02: Expected error envelope, got: {resp.data}"
        assert "amount" in resp.data["error"].get("message", ""), (
            f"TC-F02: Expected error mentioning 'amount' field, got: {resp.data}"
        )

    def test_valid_minimum_amount_accepted(
        self, api_client, branch_manager, branch
    ):
        """Amount of 0.01 (minimum) must be accepted."""
        api_client.force_authenticate(user=branch_manager)
        payload = {
            "description": "Minimal cost",
            "category": "miscellaneous",
            "amount": "0.01",
            "date_incurred": str(date.today()),
        }
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.post(
                expense_list_url(branch.id),
                payload,
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# TC-F03: PATCH amount 500→200 → audit log shows old=500, new=200
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpensePatchAuditLog:
    """TC-F03: PATCH records old and new values in the AuditLog."""

    def test_patch_amount_creates_audit_log_with_old_and_new_value(
        self, api_client, branch_manager, branch, expense
    ):
        """
        TC-F03: PATCH /api/v1/expenses/{pk}/ by Branch Manager →
        AuditLog entry with action=EXPENSE_UPDATE, old_value contains old
        amount, new_value contains new amount.

        Requirement 12.2: old and new values must be recorded.
        """
        from apps.audit.models import AuditLog

        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.patch(
                expense_detail_url(expense.id),
                {"amount": "200.00"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-F03: Expected 200, got {resp.status_code}: {resp.data}"
        )

        # Verify the expense was actually updated
        expense.refresh_from_db()
        assert expense.amount == Decimal("200.00"), (
            f"TC-F03: Expected amount 200.00, got {expense.amount}"
        )

        # Verify audit log entry
        audit_entry = AuditLog.objects.filter(
            action="EXPENSE_UPDATE",
            resource_id=expense.id,
        ).first()
        assert audit_entry is not None, "TC-F03: Expected EXPENSE_UPDATE audit log entry"
        assert audit_entry.old_value is not None
        assert audit_entry.new_value is not None
        assert audit_entry.old_value.get("amount") == "500.00", (
            f"TC-F03: Expected old amount '500.00', got {audit_entry.old_value.get('amount')}"
        )
        assert audit_entry.new_value.get("amount") == "200.00", (
            f"TC-F03: Expected new amount '200.00', got {audit_entry.new_value.get('amount')}"
        )

    def test_patch_description_audit_log(
        self, api_client, branch_manager, branch, expense
    ):
        """PATCH on description field also generates an EXPENSE_UPDATE audit log."""
        from apps.audit.models import AuditLog

        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.patch(
                expense_detail_url(expense.id),
                {"description": "Updated description"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK
        entry = AuditLog.objects.filter(
            action="EXPENSE_UPDATE", resource_id=expense.id
        ).first()
        assert entry is not None
        assert entry.old_value["description"] == "Monthly grocery purchase"
        assert entry.new_value["description"] == "Updated description"


# ---------------------------------------------------------------------------
# TC-F04: DELETE → audit log records deletion with old_value
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseDeleteAuditLog:
    """TC-F04: DELETE records old_value in the AuditLog and removes the expense."""

    def test_delete_expense_creates_audit_log_with_old_value(
        self, api_client, branch_manager, branch, expense
    ):
        """
        TC-F04: DELETE /api/v1/expenses/{pk}/ → 204 No Content.
        AuditLog entry with action=EXPENSE_DELETE and old_value capturing
        the full expense snapshot (Requirement 12.2).
        """
        from apps.audit.models import AuditLog

        expense_id = expense.id
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.delete(expense_detail_url(expense_id))
        assert resp.status_code == status.HTTP_204_NO_CONTENT, (
            f"TC-F04: Expected 204, got {resp.status_code}"
        )

        # Expense should be gone
        assert not Expense.objects.filter(id=expense_id).exists(), (
            "TC-F04: Expense must be deleted from the database"
        )

        # Audit log must capture the deletion
        audit_entry = AuditLog.objects.filter(
            action="EXPENSE_DELETE",
            resource_id=expense_id,
        ).first()
        assert audit_entry is not None, "TC-F04: Expected EXPENSE_DELETE audit log entry"
        assert audit_entry.old_value is not None, (
            "TC-F04: old_value must be populated for DELETE"
        )
        assert audit_entry.new_value is None, (
            "TC-F04: new_value must be None for DELETE"
        )
        # old_value should contain the expense amount
        assert audit_entry.old_value.get("amount") == "500.00"

    def test_delete_nonexistent_expense_returns_404(
        self, api_client, branch_manager
    ):
        """Deleting a non-existent expense returns 404."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.delete(expense_detail_url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# TC-F05: GET expense report → totals match; breakdown by category correct
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseReport:
    """TC-F05: GET expense report returns totals and category breakdown."""

    def test_report_total_matches_sum_of_expenses(
        self, api_client, branch_manager, branch
    ):
        """
        TC-F05: total_amount in report equals sum of all expenses for the
        period; breakdown_by_category is correctly populated.
        """
        today = date.today()
        # Create two expenses in different categories
        Expense.objects.create(
            branch=branch,
            description="Rent payment",
            category="rent",
            amount=Decimal("3000.00"),
            date_incurred=today,
        )
        Expense.objects.create(
            branch=branch,
            description="Electricity bill",
            category="utilities",
            amount=Decimal("500.00"),
            date_incurred=today,
        )

        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(expense_report_url(branch.id) + "?period=monthly")
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-F05: Expected 200, got {resp.status_code}: {resp.data}"
        )

        data = resp.data
        assert "total_amount" in data
        assert "breakdown_by_category" in data
        assert "expenses" in data
        assert "period_start" in data
        assert "period_end" in data

        # Total must match the sum
        total = Decimal(data["total_amount"])
        assert total == Decimal("3500.00"), (
            f"TC-F05: Expected total_amount=3500.00, got {total}"
        )

        # Breakdown by category
        breakdown = data["breakdown_by_category"]
        assert breakdown.get("rent") == "3000.00", (
            f"TC-F05: Expected rent=3000.00, got {breakdown.get('rent')}"
        )
        assert breakdown.get("utilities") == "500.00", (
            f"TC-F05: Expected utilities=500.00, got {breakdown.get('utilities')}"
        )

    def test_report_list_matches_period(
        self, api_client, branch_manager, branch
    ):
        """
        TC-F05: The `expenses` list in the report only contains expenses
        within the requested period.
        """
        today = date.today()
        import datetime

        # Expense this month
        exp_in = Expense.objects.create(
            branch=branch,
            description="In period",
            category="food_purchases",
            amount=Decimal("100.00"),
            date_incurred=today,
        )
        # Expense in a previous month (should not appear in current monthly report)
        past_date = date(today.year - 1, 1, 15) if today.month == 1 else date(today.year, 1, 1) if today.month > 1 else today
        exp_out = Expense.objects.create(
            branch=branch,
            description="Out of period",
            category="food_purchases",
            amount=Decimal("999.00"),
            date_incurred=date(2020, 1, 1),
        )

        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(expense_report_url(branch.id) + "?period=monthly")
        assert resp.status_code == status.HTTP_200_OK

        expense_ids = [e["id"] for e in resp.data["expenses"]]
        assert str(exp_in.id) in expense_ids
        assert str(exp_out.id) not in expense_ids

    def test_report_category_filter(
        self, api_client, branch_manager, branch
    ):
        """?category= filters the report to only the specified category."""
        today = date.today()
        Expense.objects.create(
            branch=branch,
            description="Flour",
            category="food_purchases",
            amount=Decimal("200.00"),
            date_incurred=today,
        )
        Expense.objects.create(
            branch=branch,
            description="Internet",
            category="utilities",
            amount=Decimal("300.00"),
            date_incurred=today,
        )

        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(
            expense_report_url(branch.id) + "?period=monthly&category=food_purchases"
        )
        assert resp.status_code == status.HTTP_200_OK
        for exp in resp.data["expenses"]:
            assert exp["category"] == "food_purchases"

    def test_tenant_owner_can_view_report(
        self, api_client, tenant_owner, branch
    ):
        """Tenant_Owner (IsFinancialReader) can access the expense report."""
        api_client.force_authenticate(user=tenant_owner)
        resp = api_client.get(expense_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_cannot_view_report(self, api_client, branch):
        """Unauthenticated access to expense report → 401/403."""
        resp = api_client.get(expense_report_url(branch.id))
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# TC-API09: POST as Receptionist → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseRBACReceptionist:
    """TC-API09: Receptionist cannot create expenses → 403."""

    def test_receptionist_cannot_create_expense(
        self, api_client, receptionist, branch
    ):
        """
        TC-API09: POST /api/v1/branches/{id}/expenses/ as Receptionist → 403.
        Only Branch_Manager may create expenses (Requirement 4.2, 12.1).
        """
        api_client.force_authenticate(user=receptionist)
        payload = {
            "description": "Unauthorized expense",
            "category": "miscellaneous",
            "amount": "100.00",
            "date_incurred": str(date.today()),
        }
        resp = api_client.post(
            expense_list_url(branch.id),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-API09: Expected 403, got {resp.status_code}"
        )

    def test_receptionist_can_list_expenses(
        self, api_client, receptionist, branch, expense
    ):
        """
        Receptionist does NOT have IsFinancialReader (only Branch_Manager,
        Tenant_Owner, Super_Admin). List should return 403.
        """
        api_client.force_authenticate(user=receptionist)
        resp = api_client.get(expense_list_url(branch.id))
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# TC-API10: PATCH as Manager → 200, audit log entry created
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpensePatchByManager:
    """TC-API10: PATCH by Manager → 200 and audit log entry created."""

    def test_manager_patch_creates_audit_log(
        self, api_client, branch_manager, branch, expense
    ):
        """
        TC-API10: PATCH /api/v1/expenses/{id}/ as Branch Manager → 200.
        An AuditLog entry with action=EXPENSE_UPDATE must be created.

        Requirement 12.2 and TC-API10.
        """
        from apps.audit.models import AuditLog

        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.patch(
                expense_detail_url(expense.id),
                {"description": "Updated description", "amount": "350.00"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-API10: Expected 200, got {resp.status_code}: {resp.data}"
        )

        audit_entry = AuditLog.objects.filter(
            action="EXPENSE_UPDATE",
            resource_id=expense.id,
        ).first()
        assert audit_entry is not None, (
            "TC-API10: An EXPENSE_UPDATE AuditLog entry must be created on PATCH"
        )
        # Verify the log captured old and new values
        assert audit_entry.old_value is not None
        assert audit_entry.new_value is not None
        assert audit_entry.old_value.get("amount") == "500.00"
        assert audit_entry.new_value.get("amount") == "350.00"


# ---------------------------------------------------------------------------
# Additional: update_profit is triggered
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdateProfitTriggered:
    """update_profit.delay is called after create, patch, and delete."""

    def test_update_profit_called_on_create(
        self, api_client, branch_manager, branch, expense_payload
    ):
        """update_profit.delay must be called after creating an expense."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay") as mock_delay:
            api_client.post(
                expense_list_url(branch.id),
                expense_payload,
                format="json",
            )
        mock_delay.assert_called_once()

    def test_update_profit_called_on_patch(
        self, api_client, branch_manager, expense
    ):
        """update_profit.delay must be called after patching an expense."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay") as mock_delay:
            api_client.patch(
                expense_detail_url(expense.id),
                {"amount": "100.00"},
                format="json",
            )
        mock_delay.assert_called_once()

    def test_update_profit_called_on_delete(
        self, api_client, branch_manager, expense
    ):
        """update_profit.delay must be called after deleting an expense."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay") as mock_delay:
            api_client.delete(expense_detail_url(expense.id))
        mock_delay.assert_called_once()


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseExport:
    """Export endpoint enqueues a task and returns task_id."""

    def test_export_csv_returns_task_id(
        self, api_client, branch_manager, branch
    ):
        """
        POST /api/v1/branches/{id}/expenses/export/ → 202 with task_id.
        Requirement 12.4.
        """
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.export_report_csv.delay") as mock_task:
            mock_task.return_value.id = str(uuid.uuid4())
            resp = api_client.post(
                expense_export_url(branch.id),
                {"format": "csv", "period": "monthly"},
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED, (
            f"Expected 202, got {resp.status_code}: {resp.data}"
        )
        assert "task_id" in resp.data
        assert resp.data.get("status") == "queued"

    def test_export_pdf_enqueues_pdf_task(
        self, api_client, branch_manager, branch
    ):
        """POST with format=pdf enqueues the PDF export task."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.export_report_pdf.delay") as mock_task:
            mock_task.return_value.id = str(uuid.uuid4())
            resp = api_client.post(
                expense_export_url(branch.id),
                {"format": "pdf", "period": "weekly"},
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data.get("status") == "queued"
        mock_task.assert_called_once()

    def test_receptionist_cannot_export(
        self, api_client, receptionist, branch
    ):
        """Receptionist cannot trigger exports → 403."""
        api_client.force_authenticate(user=receptionist)
        resp = api_client.post(
            expense_export_url(branch.id),
            {"format": "csv"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Cross-branch isolation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpenseCrossBranchIsolation:
    """Branch Manager cannot access another branch's expenses."""

    def test_manager_cannot_see_other_branch_expenses(
        self, api_client, other_manager, branch, expense
    ):
        """
        Branch Manager for other_branch lists expenses for branch → empty list.
        """
        api_client.force_authenticate(user=other_manager)
        resp = api_client.get(expense_list_url(branch.id))
        # Either 403 or empty list satisfies the isolation requirement
        if resp.status_code == status.HTTP_200_OK:
            items = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
            assert len(items) == 0, (
                "Cross-branch isolation: Manager must not see other branch's expenses"
            )
        else:
            assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_manager_can_patch_own_branch_expense(
        self, api_client, branch_manager, expense
    ):
        """Branch Manager can patch their own branch's expense."""
        api_client.force_authenticate(user=branch_manager)
        with patch("apps.financials.tasks.update_profit.delay"):
            resp = api_client.patch(
                expense_detail_url(expense.id),
                {"notes": "Patched by manager"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK
