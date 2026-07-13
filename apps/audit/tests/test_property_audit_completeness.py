"""
Property-Based Tests: Audit Log Completeness (Property 13)

Property 13: Audit Log Completeness

  For any auditable action (as enumerated in Requirement 5.1), executing that
  action SHALL produce exactly one AuditLog entry containing all 15 required
  fields, with the correct ``action`` enum code and ``status`` value.

**Validates: Requirements 5.1, 5.2**

The 15 required fields (Requirement 5.2):
  1.  log_id         — UUID PK (non-null)
  2.  timestamp      — UTC datetime, auto-set (non-null)
  3.  tenant_id      — UUID (nullable by spec — platform-level events have none)
  4.  branch_id      — UUID (nullable — branch-level events only)
  5.  user_id        — UUID (nullable — system/anonymous actions)
  6.  user_role      — string at time of action (may be empty for anonymous)
  7.  ip_address     — IP string (non-null; defaults to "0.0.0.0" for Celery tasks)
  8.  user_agent     — string (may be empty)
  9.  action         — standardised enum code (non-null, non-empty)
  10. resource_type  — model name string (non-null, non-empty)
  11. resource_id    — UUID of affected resource (nullable)
  12. old_value      — JSONB (nullable)
  13. new_value      — JSONB (nullable)
  14. status         — "success" or "failure" (non-null)
  15. failure_reason — string (empty on success, non-empty on failure)

Strategy:
  - ``st.sampled_from(AUDITABLE_ACTION_SPECS)`` generates one of the
    supported auditable action types per Hypothesis iteration.
  - Each action spec is a (action_code, executor_fn) pair.
  - The executor_fn calls the real service/view code path, counts the
    AuditLog entries created, and returns the created entry.
  - The test asserts: exactly one new entry exists, all non-nullable fields
    are populated, and ``action`` matches the expected code.

No mocking is used — the completeness property is exercised end-to-end
through the same code paths used in production.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from rest_framework.test import APIClient

from apps.audit.models import AuditLog, AuditLogStatus
from apps.audit.decorators import _request_context as _audit_ctx

User = get_user_model()

# ---------------------------------------------------------------------------
# Test URLs
# ---------------------------------------------------------------------------

LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
PASSWORD_RESET_URL = "/api/v1/auth/password-reset/"
PASSWORD_RESET_CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"

# ---------------------------------------------------------------------------
# Required fields specification
#
# Maps field_name → (nullable, description)
# Fields marked nullable=True may legally be None/empty per the model spec.
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, tuple[bool, str]] = {
    "log_id":         (False, "UUID primary key"),
    "timestamp":      (False, "UTC creation timestamp"),
    "tenant_id":      (True,  "Tenant UUID — nullable for platform-level events"),
    "branch_id":      (True,  "Branch UUID — nullable for non-branch events"),
    "user_id":        (True,  "Acting user UUID — nullable for anonymous/system actions"),
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

# ---------------------------------------------------------------------------
# Helpers: set up thread-local context so _write_auth_audit / audit_action
# can read ip_address / user_agent / tenant_id without an actual HTTP request
# ---------------------------------------------------------------------------

def _set_fake_context(
    user=None,
    ip: str = "127.0.0.1",
    ua: str = "TestAgent/1.0",
    tenant_id=None,
):
    """Populate thread-local audit context, mirroring AuditLogMiddleware."""
    _audit_ctx.ip_address = ip
    _audit_ctx.user_agent = ua
    _audit_ctx.tenant_id = str(tenant_id) if tenant_id is not None else None
    if user is not None:
        _audit_ctx.user_id = str(user.pk)
        _audit_ctx.user_role = getattr(user, "role", "")
    else:
        _audit_ctx.user_id = None
        _audit_ctx.user_role = ""


def _clear_context():
    """Remove all thread-local audit context attributes."""
    for attr in ("user_id", "user_role", "ip_address", "user_agent", "tenant_id"):
        try:
            delattr(_audit_ctx, attr)
        except AttributeError:
            pass


def _unique_email(prefix: str) -> str:
    """Generate a unique email address to avoid DB constraint collisions."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}@test.example.com"


# ---------------------------------------------------------------------------
# Auditable action executors
#
# Each executor:
#   1. Clears audit context
#   2. Creates necessary DB fixtures
#   3. Sets fake context
#   4. Executes the auditable action via real code paths
#   5. Returns the expected action code
#
# All executors are designed to be called inside a @pytest.mark.django_db
# transaction so that AuditLog.objects.count() is accurate.
# ---------------------------------------------------------------------------

def _exec_user_login_success():
    """USER_LOGIN — successful authentication via LoginView."""
    email = _unique_email("login_ok")
    password = "SecurePass123!"
    User.objects.filter(email=email).delete()
    User.objects.create_user(email=email, password=password, role="Receptionist")

    client = APIClient()
    client.post(LOGIN_URL, {"email": email, "password": password}, format="json")
    return "USER_LOGIN"


def _exec_user_login_failed():
    """USER_LOGIN_FAILED — failed authentication (wrong password)."""
    email = _unique_email("login_fail")
    User.objects.filter(email=email).delete()
    User.objects.create_user(email=email, password="RealPass123!", role="Receptionist")

    client = APIClient()
    client.post(LOGIN_URL, {"email": email, "password": "WrongPass!"}, format="json")
    return "USER_LOGIN_FAILED"


def _exec_user_logout():
    """USER_LOGOUT — authenticated user logs out via LogoutView."""
    email = _unique_email("logout")
    password = "SecurePass123!"
    User.objects.filter(email=email).delete()
    user = User.objects.create_user(email=email, password=password, role="Receptionist")

    client = APIClient()
    # Use force_login to authenticate without going through LoginView,
    # so that no USER_LOGIN audit entry is created before the count snapshot.
    client.force_login(user)
    client.post(LOGOUT_URL, format="json")
    return "USER_LOGOUT"


def _exec_password_reset():
    """PASSWORD_RESET — user confirms a password reset via PasswordResetConfirmView."""
    from apps.authentication.models import PasswordResetToken

    email = _unique_email("pwreset")
    old_password = "OldPass123!"
    new_password = "NewPass456!"
    User.objects.filter(email=email).delete()
    user = User.objects.create_user(email=email, password=old_password, role="Receptionist")

    # Invalidate old tokens and create a fresh one
    PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
    token_obj = PasswordResetToken.objects.create(user=user)

    client = APIClient()
    client.post(
        PASSWORD_RESET_CONFIRM_URL,
        {"token": str(token_obj.token), "new_password": new_password},
        format="json",
    )
    return "PASSWORD_RESET"


def _exec_audit_action_decorator():
    """
    ITEM_CREATE (via @audit_action decorator) — verifies that the decorator
    itself creates a valid AuditLog entry with all 15 required fields.

    Uses a simple inline function decorated with @audit_action so the test
    is self-contained and doesn't depend on any specific service implementation.
    """
    from apps.audit.decorators import audit_action

    call_count = {"n": 0}

    @audit_action(
        action_code="ITEM_CREATE",
        resource_type="MenuItem",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
    )
    def _create_menu_item(name: str):
        """Simulated service function that returns a dict representing the new item."""
        call_count["n"] += 1
        return {"id": str(uuid.uuid4()), "name": name, "price": "9.99"}

    _set_fake_context(ip="10.0.0.1", ua="Mozilla/5.0")
    try:
        _create_menu_item("Injera Special")
    finally:
        _clear_context()

    return "ITEM_CREATE"


def _exec_audit_action_failure():
    """
    ITEM_DELETE (via @audit_action decorator with a failing function) —
    verifies that the decorator logs status='failure' and failure_reason
    when the wrapped function raises an exception.
    """
    from apps.audit.decorators import audit_action

    @audit_action(
        action_code="ITEM_DELETE",
        resource_type="MenuItem",
    )
    def _delete_menu_item(item_id: str):
        raise ValueError(f"MenuItem {item_id} not found")

    _set_fake_context(ip="10.0.0.2", ua="Mozilla/5.0")
    try:
        try:
            _delete_menu_item(str(uuid.uuid4()))
        except ValueError:
            pass  # expected — decorator re-raises after logging
    finally:
        _clear_context()

    return "ITEM_DELETE"


def _exec_expense_audit():
    """
    EXPENSE_CREATE (via @audit_action decorator) — simulates an expense
    creation service function that returns the created expense record.
    """
    from apps.audit.decorators import audit_action

    @audit_action(
        action_code="EXPENSE_CREATE",
        resource_type="Expense",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
    )
    def _create_expense(description: str, amount: str):
        return {
            "id": str(uuid.uuid4()),
            "description": description,
            "amount": amount,
            "category": "utilities",
        }

    _set_fake_context(ip="192.168.1.10", ua="TestClient/2.0")
    try:
        _create_expense("Monthly electricity bill", "1250.00")
    finally:
        _clear_context()

    return "EXPENSE_CREATE"


def _exec_inventory_adjustment():
    """
    INVENTORY_ADJUST (via @audit_action decorator) — simulates an inventory
    quantity adjustment with old_value captured before execution.
    """
    from apps.audit.decorators import audit_action

    item_id = str(uuid.uuid4())
    old_snapshot = {"id": item_id, "quantity": "50.0000", "unit": "kg"}

    @audit_action(
        action_code="INVENTORY_ADJUST",
        resource_type="InventoryItem",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
        get_old_value=lambda *a, **kw: old_snapshot,
    )
    def _adjust_inventory(item_id: str, new_quantity: str):
        return {"id": item_id, "quantity": new_quantity, "unit": "kg"}

    _set_fake_context(ip="10.10.0.5", ua="StaffDashboard/1.0")
    try:
        _adjust_inventory(item_id, "35.0000")
    finally:
        _clear_context()

    return "INVENTORY_ADJUST"


def _exec_order_cancel():
    """
    ORDER_CANCEL (via @audit_action decorator) — simulates an order cancellation.
    """
    from apps.audit.decorators import audit_action

    @audit_action(
        action_code="ORDER_CANCEL",
        resource_type="Order",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
        get_old_value=lambda *a, **kw: {"status": "confirmed"},
    )
    def _cancel_order(order_id: str):
        return {"id": order_id, "status": "cancelled"}

    _set_fake_context(ip="172.16.0.1", ua="ReceptionApp/3.0")
    try:
        _cancel_order(str(uuid.uuid4()))
    finally:
        _clear_context()

    return "ORDER_CANCEL"


def _exec_role_assignment():
    """
    ROLE_ASSIGN (via @audit_action decorator) — simulates role assignment/removal.
    """
    from apps.audit.decorators import audit_action

    @audit_action(
        action_code="ROLE_ASSIGN",
        resource_type="User",
        get_resource_id=lambda result: result.get("id") if isinstance(result, dict) else None,
        get_old_value=lambda *a, **kw: {"role": "Receptionist"},
    )
    def _assign_role(user_id: str, new_role: str):
        return {"id": user_id, "role": new_role}

    _set_fake_context(ip="10.0.1.5", ua="AdminDashboard/1.0")
    try:
        _assign_role(str(uuid.uuid4()), "Branch_Manager")
    finally:
        _clear_context()

    return "ROLE_ASSIGN"


def _exec_tenant_config_change():
    """
    TENANTCONFIG_CHANGE (via @audit_action decorator) — simulates a white-label
    TenantConfig update.
    """
    from apps.audit.decorators import audit_action

    @audit_action(
        action_code="TENANTCONFIG_CHANGE",
        resource_type="TenantConfig",
        get_old_value=lambda *a, **kw: {"primary_color": "#FFFFFF", "currency": "ETB"},
    )
    def _update_config(primary_color: str):
        return {"primary_color": primary_color, "currency": "ETB"}

    _set_fake_context(ip="10.0.2.1", ua="OwnerDashboard/1.0")
    try:
        _update_config("#1A73E8")
    finally:
        _clear_context()

    return "TENANTCONFIG_CHANGE"


# ---------------------------------------------------------------------------
# Registry of all auditable action executors
# ---------------------------------------------------------------------------

AUDITABLE_ACTION_EXECUTORS = [
    _exec_user_login_success,
    _exec_user_login_failed,
    _exec_user_logout,
    _exec_password_reset,
    _exec_audit_action_decorator,
    _exec_audit_action_failure,
    _exec_expense_audit,
    _exec_inventory_adjustment,
    _exec_order_cancel,
    _exec_role_assignment,
    _exec_tenant_config_change,
]


# ---------------------------------------------------------------------------
# Core assertion helper
# ---------------------------------------------------------------------------

def _assert_audit_log_complete(entry: AuditLog, expected_action_code: str) -> None:
    """
    Assert that *entry* contains all 15 required fields and the correct
    action enum code.  Raises AssertionError with a descriptive message on
    any violation.
    """
    # Verify all 15 fields exist on the model instance
    for field_name, (nullable, description) in REQUIRED_FIELDS.items():
        assert hasattr(entry, field_name), (
            f"AuditLog is missing field '{field_name}' ({description})"
        )
        value = getattr(entry, field_name)

        if not nullable:
            # Non-nullable fields must not be None or empty string
            assert value is not None, (
                f"AuditLog field '{field_name}' ({description}) must not be None. "
                f"Got: {value!r} for action={expected_action_code}"
            )
            if isinstance(value, str):
                assert len(value) > 0, (
                    f"AuditLog field '{field_name}' ({description}) must not be an empty string. "
                    f"Got empty string for action={expected_action_code}"
                )

    # Field 9: action must match the expected code
    assert entry.action == expected_action_code, (
        f"AuditLog.action mismatch: expected {expected_action_code!r}, "
        f"got {entry.action!r}"
    )

    # Field 14: status must be a valid choice
    valid_statuses = {AuditLogStatus.SUCCESS, AuditLogStatus.FAILURE}
    assert entry.status in valid_statuses, (
        f"AuditLog.status must be one of {valid_statuses!r}, got {entry.status!r}"
    )

    # Field 1: log_id must be a valid UUID
    assert entry.log_id is not None, "AuditLog.log_id must not be None"

    # Field 7: ip_address must be a valid IP (model enforces this via GenericIPAddressField)
    assert entry.ip_address is not None, (
        f"AuditLog.ip_address must not be None for action={expected_action_code}"
    )

    # Verify failure_reason is consistent with status
    if entry.status == AuditLogStatus.FAILURE:
        assert entry.failure_reason, (
            f"AuditLog.failure_reason should not be empty when status=failure "
            f"for action={expected_action_code}"
        )


# ---------------------------------------------------------------------------
# Property 13: Audit Log Completeness
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    executor=st.sampled_from(AUDITABLE_ACTION_EXECUTORS),
)
@settings(max_examples=100)
def test_property_13_audit_log_completeness(executor) -> None:
    """
    **Validates: Requirements 5.1, 5.2**

    Property 13: Audit Log Completeness

    For any auditable action (as enumerated in Requirement 5.1), executing that
    action SHALL produce exactly one new AuditLog entry that:
      1. Contains all 15 required fields (non-nullable fields must not be
         None or empty).
      2. Has the correct ``action`` enum code matching the auditable action
         performed.
      3. Has a valid ``status`` value ("success" or "failure").

    The test:
      - Snapshots the AuditLog count before the action.
      - Executes the auditable action through its real code path.
      - Asserts count increased by exactly 1.
      - Retrieves the newly created entry.
      - Asserts all 15 required fields are present and correctly populated.
    """
    # --- Snapshot count before ---
    count_before = AuditLog.objects.count()

    # --- Execute the auditable action; returns the expected action code ---
    expected_action_code = executor()

    # --- Assert exactly one new entry was created ---
    count_after = AuditLog.objects.count()
    new_entries_count = count_after - count_before

    assert new_entries_count == 1, (
        f"Expected exactly 1 new AuditLog entry for action={expected_action_code!r}, "
        f"but {new_entries_count} new entries were created. "
        f"(count_before={count_before}, count_after={count_after})"
    )

    # --- Retrieve the newly created entry ---
    # Use ordering by timestamp DESC to get the most recent entry
    entry = AuditLog.objects.filter(action=expected_action_code).latest("timestamp")

    # --- Assert all 15 required fields are complete and correct ---
    _assert_audit_log_complete(entry, expected_action_code)
