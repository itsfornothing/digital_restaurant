"""
Property-Based Tests: Expense Audit Trail Completeness (Property 24)

Property 24: Expense Audit Trail Completeness

  For any Expense modification or deletion operation, exactly one AuditLog
  entry SHALL be produced containing the correct ``old_value`` and ``new_value``
  JSON representations of the expense record, with:
    - ``action`` matching the expected action code (EXPENSE_UPDATE / EXPENSE_DELETE)
    - ``old_value`` containing the pre-modification state of the expense
    - ``new_value`` containing the post-modification state (or None on deletion)
    - ``resource_type`` = "Expense"
    - ``resource_id`` = the expense's UUID
    - All 15 required AuditLog fields populated

**Validates: Requirements 12.2**

Requirement 12.2 states:
  "WHEN an Expense record is modified or deleted, THE Audit_Logger SHALL
  record the old and new values, the modifying user, and the timestamp in
  an immutable audit entry."

Sub-properties tested:

  Property 24a — Modification audit completeness:
    For any Expense with any valid combination of field values, performing a
    PATCH (partial update) SHALL produce exactly one new AuditLog entry where:
      - action = "EXPENSE_UPDATE"
      - old_value contains the expense's pre-PATCH state
      - new_value contains the expense's post-PATCH state
      - resource_type = "Expense"
      - resource_id = expense.id
      - All 15 required fields are populated (non-nullable fields are non-null)

  Property 24b — Deletion audit completeness:
    For any Expense with any valid combination of field values, performing a
    DELETE SHALL produce exactly one new AuditLog entry where:
      - action = "EXPENSE_DELETE"
      - old_value contains the expense's pre-deletion state
      - new_value is None (deletion leaves no after-state)
      - resource_type = "Expense"
      - resource_id = expense.id
      - All 15 required fields are populated

Strategy:
  - Hypothesis generates diverse expense field values (descriptions, categories,
    amounts, dates, notes) to exercise the audit trail across many inputs.
  - Each test creates a real Branch, a real User, and a real Expense, then
    performs PATCH or DELETE via the API client, checking the resulting
    AuditLog entry.
  - The Celery ``update_profit`` task is patched out to avoid Celery infrastructure
    requirements in the test environment.
  - @settings(max_examples=500) as specified for Property 24.
  - No mocking of audit logic — the tests exercise the real _write_expense_audit
    code path in expenses/views.py.

Requirements: 12.2
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.expenses.models import EXPENSE_CATEGORIES, Expense

User = get_user_model()

# ---------------------------------------------------------------------------
# Constants — 15 required AuditLog fields (Requirement 5.2)
# ---------------------------------------------------------------------------

# Maps field_name → (nullable, description)
# nullable=True means the field may legally be None/empty per the spec
REQUIRED_FIELDS: dict[str, tuple[bool, str]] = {
    "log_id":         (False, "UUID primary key"),
    "timestamp":      (False, "UTC creation timestamp"),
    "tenant_id":      (True,  "Tenant UUID — nullable for tenant-schema events"),
    "branch_id":      (True,  "Branch UUID — nullable if not branch-level"),
    "user_id":        (True,  "Acting user UUID — nullable for anonymous/system"),
    "user_role":      (True,  "Role at time of action — may be empty string"),
    "ip_address":     (False, "IP address — defaults to 0.0.0.0 for Celery tasks"),
    "user_agent":     (True,  "HTTP User-Agent — may be empty"),
    "action":         (False, "Standardised action enum code"),
    "resource_type":  (False, "Affected model/resource name"),
    "resource_id":    (True,  "UUID of affected resource — nullable"),
    "old_value":      (True,  "JSONB before-state — nullable"),
    "new_value":      (True,  "JSONB after-state — nullable"),
    "status":         (False, "success or failure"),
    "failure_reason": (True,  "Human-readable failure description — empty on success"),
}

# Snapshot field names that must appear inside old_value / new_value
EXPENSE_SNAPSHOT_FIELDS = {
    "id",
    "branch_id",
    "description",
    "category",
    "amount",
    "date_incurred",
    "notes",
    "reference_number",
}

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def expense_list_url(branch_pk) -> str:
    return f"/api/v1/branches/{branch_pk}/expenses/"


def expense_detail_url(pk) -> str:
    return f"/api/v1/expenses/{pk}/"


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Valid expense category keys
CATEGORY_KEYS = [key for key, _ in EXPENSE_CATEGORIES]
st_category = st.sampled_from(CATEGORY_KEYS)

# Descriptions: printable text, 1–200 chars (avoids surrogate chars)
st_description = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Zs")),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip())  # must have at least one non-space char

# Amounts: Decimal values from 0.01 to 999999.99 (12 digits, 2 decimal places)
st_amount = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Dates: within ±2 years of today to keep tests realistic
_today = date.today()
st_date_incurred = st.dates(
    min_value=_today - timedelta(days=730),
    max_value=_today + timedelta(days=730),
)

# Notes and reference numbers: optional short strings
st_notes = st.one_of(
    st.just(""),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Zs")),
        min_size=0,
        max_size=100,
    ),
)

st_reference_number = st.one_of(
    st.just(""),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Zs")),
        min_size=0,
        max_size=50,
    ),
)

# Patch payloads — at least one mutable field is changed
PATCHABLE_FIELDS = ["description", "amount", "notes", "reference_number"]
st_patch_field = st.sampled_from(PATCHABLE_FIELDS)
st_patch_description = st_description
st_patch_amount = st_amount
st_patch_notes = st_notes
st_patch_reference = st_reference_number

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_branch() -> Branch:
    """Create and persist a Branch fixture for each test iteration."""
    return Branch.objects.create(
        name=f"Branch-{uuid.uuid4().hex[:6]}",
        address="Test Address",
        phone="0911000001",
        email=f"branch_{uuid.uuid4().hex[:6]}@test.com",
    )


def _make_branch_manager(branch: Branch) -> "User":
    """Create and persist a Branch_Manager user attached to the given branch."""
    return User.objects.create_user(
        email=f"manager_{uuid.uuid4().hex[:8]}@test.com",
        password="TestPass123!",
        role="Branch_Manager",
        branch=branch,
    )


def _make_expense(branch: Branch, category: str, amount: Decimal, date_incurred: date,
                  description: str = "Test expense", notes: str = "",
                  reference_number: str = "") -> Expense:
    """Create and persist an Expense for the given branch."""
    return Expense.objects.create(
        branch=branch,
        description=description[:500],
        category=category,
        amount=amount,
        date_incurred=date_incurred,
        notes=notes[:1000] if notes else "",
        reference_number=reference_number[:100] if reference_number else "",
    )


def _assert_all_15_fields_present(entry: AuditLog, expected_action: str) -> None:
    """
    Assert all 15 required AuditLog fields are present and that non-nullable
    fields are not None or empty.

    Raises AssertionError with descriptive message on any violation.
    """
    for field_name, (nullable, description) in REQUIRED_FIELDS.items():
        assert hasattr(entry, field_name), (
            f"AuditLog is missing required field '{field_name}' ({description}). "
            f"All 15 fields are required by Requirement 5.2."
        )
        value = getattr(entry, field_name)
        if not nullable:
            assert value is not None, (
                f"AuditLog field '{field_name}' ({description}) must not be None "
                f"for action={expected_action!r}. Got: {value!r}"
            )
            if isinstance(value, str):
                assert len(value) > 0, (
                    f"AuditLog field '{field_name}' ({description}) must not be "
                    f"empty string for action={expected_action!r}."
                )

    # Field 9: action code must match
    assert entry.action == expected_action, (
        f"AuditLog.action mismatch: expected {expected_action!r}, "
        f"got {entry.action!r}."
    )

    # Field 14: status must be valid
    assert entry.status in ("success", "failure"), (
        f"AuditLog.status must be 'success' or 'failure', "
        f"got {entry.status!r} for action={expected_action!r}."
    )

    # Field 7: ip_address must be populated
    assert entry.ip_address is not None, (
        f"AuditLog.ip_address must not be None for action={expected_action!r}."
    )


def _assert_expense_resource_fields(
    entry: AuditLog, expected_resource_id, expected_action: str
) -> None:
    """
    Assert the resource-identification fields on the AuditLog entry are correct:
      - resource_type = "Expense"
      - resource_id = expected_resource_id
    """
    assert entry.resource_type == "Expense", (
        f"AuditLog.resource_type must be 'Expense' for expense actions, "
        f"got {entry.resource_type!r} for action={expected_action!r}. "
        f"(Requirement 12.2)"
    )
    assert str(entry.resource_id) == str(expected_resource_id), (
        f"AuditLog.resource_id must equal the expense UUID. "
        f"Expected {expected_resource_id!r}, got {entry.resource_id!r}. "
        f"(Requirement 12.2)"
    )


def _assert_snapshot_fields_present(snapshot: dict, label: str, action: str) -> None:
    """
    Assert that the snapshot dict (old_value or new_value) contains all
    expected expense snapshot fields.
    """
    for field in EXPENSE_SNAPSHOT_FIELDS:
        assert field in snapshot, (
            f"AuditLog.{label} is missing field '{field}' for action={action!r}. "
            f"The audit entry must capture all expense fields "
            f"(Requirement 12.2). Got keys: {sorted(snapshot.keys())}"
        )


# ---------------------------------------------------------------------------
# Property 24a: Modification audit trail completeness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(
    category=st_category,
    amount=st_amount,
    date_incurred=st_date_incurred,
    description=st_description,
    notes=st_notes,
    patch_field=st_patch_field,
    new_description=st_patch_description,
    new_amount=st_patch_amount,
)
@settings(max_examples=500)
def test_property_24a_expense_modification_produces_one_audit_entry(
    category: str,
    amount: Decimal,
    date_incurred: date,
    description: str,
    notes: str,
    patch_field: str,
    new_description: str,
    new_amount: Decimal,
) -> None:
    """
    **Validates: Requirements 12.2**

    Property 24a: Expense Modification Audit Trail Completeness

    For any Expense with any valid field values, performing a PATCH (partial
    update) SHALL produce exactly one new AuditLog entry where:
      1. ``action`` = "EXPENSE_UPDATE"
      2. ``old_value`` contains the pre-PATCH state of the expense
         (including the original ``amount``, ``description``, ``category``,
         ``date_incurred``, ``notes``, ``reference_number``)
      3. ``new_value`` contains the post-PATCH state of the expense
      4. ``resource_type`` = "Expense"
      5. ``resource_id`` = the expense's UUID
      6. All 15 required AuditLog fields are populated

    The property holds regardless of which field is updated and regardless
    of the specific field values involved.
    """
    # --- Setup fixtures ---
    branch = _make_branch()
    manager = _make_branch_manager(branch)
    expense = _make_expense(
        branch=branch,
        category=category,
        amount=amount,
        date_incurred=date_incurred,
        description=description,
        notes=notes,
    )
    expense_id = expense.id
    original_amount = str(expense.amount)
    original_description = expense.description

    # --- Build patch payload (change one field) ---
    if patch_field == "description":
        assume(new_description.strip() != "")
        # Ensure the new description is genuinely different from the original
        # so that old_value != new_value after the patch
        assume(new_description != description)
        patch_payload = {"description": new_description}
    elif patch_field == "amount":
        # Ensure new amount differs from original so we can verify old/new differ
        assume(new_amount != amount)
        patch_payload = {"amount": str(new_amount)}
    elif patch_field == "notes":
        patch_payload = {"notes": notes + " updated"}
    else:
        patch_payload = {"reference_number": "REF-UPDATED"}

    # --- Snapshot count before ---
    count_before = AuditLog.objects.filter(action="EXPENSE_UPDATE").count()

    # --- Execute PATCH via API ---
    client = APIClient()
    client.force_login(manager)
    with patch("apps.financials.tasks.update_profit.delay"):
        response = client.patch(
            expense_detail_url(expense_id),
            patch_payload,
            format="json",
        )

    assert response.status_code == 200, (
        f"PATCH /api/v1/expenses/{{id}}/ returned HTTP {response.status_code}, "
        f"expected 200. Response: {getattr(response, 'data', response.content)!r}. "
        f"The expense must be patchable by a Branch_Manager (Requirement 12.1)."
    )

    # --- Assert exactly one new EXPENSE_UPDATE entry was created ---
    count_after = AuditLog.objects.filter(action="EXPENSE_UPDATE").count()
    new_entries = count_after - count_before

    assert new_entries == 1, (
        f"Expected exactly 1 new EXPENSE_UPDATE AuditLog entry after PATCH, "
        f"but {new_entries} new entries were created. "
        f"(count_before={count_before}, count_after={count_after}). "
        f"Requirement 12.2: every Expense modification must produce exactly "
        f"one audit entry."
    )

    # --- Retrieve the new entry ---
    entry = (
        AuditLog.objects
        .filter(action="EXPENSE_UPDATE", resource_id=expense_id)
        .latest("timestamp")
    )

    # --- Assert all 15 required fields are populated ---
    _assert_all_15_fields_present(entry, "EXPENSE_UPDATE")

    # --- Assert resource identification ---
    _assert_expense_resource_fields(entry, expense_id, "EXPENSE_UPDATE")

    # --- Assert old_value is present and contains expense snapshot ---
    assert entry.old_value is not None, (
        f"AuditLog.old_value must not be None for EXPENSE_UPDATE. "
        f"The pre-modification state must be captured (Requirement 12.2)."
    )
    _assert_snapshot_fields_present(entry.old_value, "old_value", "EXPENSE_UPDATE")

    # --- Assert new_value is present and contains expense snapshot ---
    assert entry.new_value is not None, (
        f"AuditLog.new_value must not be None for EXPENSE_UPDATE. "
        f"The post-modification state must be captured (Requirement 12.2)."
    )
    _assert_snapshot_fields_present(entry.new_value, "new_value", "EXPENSE_UPDATE")

    # --- Assert old_value reflects the pre-PATCH state ---
    assert entry.old_value["amount"] == original_amount, (
        f"AuditLog.old_value['amount'] must equal the pre-PATCH amount. "
        f"Expected {original_amount!r}, got {entry.old_value['amount']!r}. "
        f"(Requirement 12.2)"
    )
    assert entry.old_value["description"] == original_description, (
        f"AuditLog.old_value['description'] must equal the pre-PATCH description. "
        f"Expected {original_description!r}, got {entry.old_value['description']!r}. "
        f"(Requirement 12.2)"
    )

    # --- Assert new_value reflects the post-PATCH state ---
    expense.refresh_from_db()
    assert entry.new_value["amount"] == str(expense.amount), (
        f"AuditLog.new_value['amount'] must equal the post-PATCH amount. "
        f"Expected {str(expense.amount)!r}, got {entry.new_value['amount']!r}. "
        f"(Requirement 12.2)"
    )
    assert entry.new_value["description"] == expense.description, (
        f"AuditLog.new_value['description'] must equal the post-PATCH description. "
        f"Expected {expense.description!r}, got {entry.new_value['description']!r}. "
        f"(Requirement 12.2)"
    )

    # --- Assert old_value ≠ new_value when a field actually changed ---
    if patch_field in ("amount", "description"):
        assert entry.old_value != entry.new_value, (
            f"AuditLog old_value and new_value must differ when a field was "
            f"changed by the PATCH. old_value: {entry.old_value!r}, "
            f"new_value: {entry.new_value!r}. (Requirement 12.2)"
        )

    # --- Assert status is success ---
    assert entry.status == "success", (
        f"AuditLog.status must be 'success' for a completed EXPENSE_UPDATE. "
        f"Got {entry.status!r}. (Requirement 12.2)"
    )


# ---------------------------------------------------------------------------
# Property 24b: Deletion audit trail completeness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(
    category=st_category,
    amount=st_amount,
    date_incurred=st_date_incurred,
    description=st_description,
    notes=st_notes,
    reference_number=st_reference_number,
)
@settings(max_examples=500)
def test_property_24b_expense_deletion_produces_one_audit_entry(
    category: str,
    amount: Decimal,
    date_incurred: date,
    description: str,
    notes: str,
    reference_number: str,
) -> None:
    """
    **Validates: Requirements 12.2**

    Property 24b: Expense Deletion Audit Trail Completeness

    For any Expense with any valid field values, performing a DELETE SHALL
    produce exactly one new AuditLog entry where:
      1. ``action`` = "EXPENSE_DELETE"
      2. ``old_value`` contains the pre-deletion state of the expense
         (the full snapshot that was deleted)
      3. ``new_value`` is None (no after-state exists for a deleted record)
      4. ``resource_type`` = "Expense"
      5. ``resource_id`` = the (now-deleted) expense's UUID
      6. All 15 required AuditLog fields are populated

    The property holds for any combination of valid expense field values —
    Hypothesis exercises this across 500 iterations.
    """
    # --- Setup fixtures ---
    branch = _make_branch()
    manager = _make_branch_manager(branch)
    expense = _make_expense(
        branch=branch,
        category=category,
        amount=amount,
        date_incurred=date_incurred,
        description=description,
        notes=notes,
        reference_number=reference_number,
    )
    expense_id = expense.id
    branch_id = expense.branch_id

    # Capture the full snapshot that should appear in old_value
    expected_old_snapshot = {
        "id": str(expense.id),
        "branch_id": str(expense.branch_id),
        "description": expense.description,
        "category": expense.category,
        "amount": str(expense.amount),
        "date_incurred": str(expense.date_incurred),
        "notes": expense.notes,
        "reference_number": expense.reference_number,
    }

    # --- Snapshot count before ---
    count_before = AuditLog.objects.filter(action="EXPENSE_DELETE").count()

    # --- Execute DELETE via API ---
    client = APIClient()
    client.force_login(manager)
    with patch("apps.financials.tasks.update_profit.delay"):
        response = client.delete(expense_detail_url(expense_id))

    assert response.status_code == 204, (
        f"DELETE /api/v1/expenses/{{id}}/ returned HTTP {response.status_code}, "
        f"expected 204 No Content. "
        f"Response: {getattr(response, 'data', response.content)!r}. "
        f"The expense must be deletable by a Branch_Manager (Requirement 12.1)."
    )

    # --- Verify expense was actually deleted from DB ---
    assert not Expense.objects.filter(id=expense_id).exists(), (
        f"Expense {expense_id} still exists in the DB after DELETE response 204. "
        f"The expense must be removed (Requirement 12.1)."
    )

    # --- Assert exactly one new EXPENSE_DELETE entry was created ---
    count_after = AuditLog.objects.filter(action="EXPENSE_DELETE").count()
    new_entries = count_after - count_before

    assert new_entries == 1, (
        f"Expected exactly 1 new EXPENSE_DELETE AuditLog entry after DELETE, "
        f"but {new_entries} new entries were created. "
        f"(count_before={count_before}, count_after={count_after}). "
        f"Requirement 12.2: every Expense deletion must produce exactly "
        f"one audit entry."
    )

    # --- Retrieve the new entry ---
    entry = (
        AuditLog.objects
        .filter(action="EXPENSE_DELETE", resource_id=expense_id)
        .latest("timestamp")
    )

    # --- Assert all 15 required fields are populated ---
    _assert_all_15_fields_present(entry, "EXPENSE_DELETE")

    # --- Assert resource identification ---
    _assert_expense_resource_fields(entry, expense_id, "EXPENSE_DELETE")

    # --- Assert old_value is present and matches the pre-deletion snapshot ---
    assert entry.old_value is not None, (
        f"AuditLog.old_value must not be None for EXPENSE_DELETE. "
        f"The pre-deletion state must be captured (Requirement 12.2)."
    )
    _assert_snapshot_fields_present(entry.old_value, "old_value", "EXPENSE_DELETE")

    # Each field in the expected snapshot must match old_value exactly
    for field, expected_value in expected_old_snapshot.items():
        actual_value = entry.old_value.get(field)
        assert actual_value == expected_value, (
            f"AuditLog.old_value['{field}'] mismatch for EXPENSE_DELETE. "
            f"Expected {expected_value!r}, got {actual_value!r}. "
            f"The old_value must faithfully capture the expense record as it "
            f"existed before deletion (Requirement 12.2)."
        )

    # --- Assert new_value is None for deletion ---
    assert entry.new_value is None, (
        f"AuditLog.new_value must be None for EXPENSE_DELETE (no after-state "
        f"exists once the record is deleted). "
        f"Got {entry.new_value!r}. (Requirement 12.2)"
    )

    # --- Assert status is success ---
    assert entry.status == "success", (
        f"AuditLog.status must be 'success' for a completed EXPENSE_DELETE. "
        f"Got {entry.status!r}. (Requirement 12.2)"
    )
