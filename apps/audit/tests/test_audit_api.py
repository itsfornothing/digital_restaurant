"""
apps/audit/tests/test_audit_api.py

API-level test suite for audit log endpoints covering:
  TC-F03  : Expense PATCH → audit log entry shows both old_value and new_value
  TC-F04  : Expense DELETE → audit log entry records deletion with old_value set
  TC-M05  : MenuItem price change → audit log entry contains old and new price
  TC-API15: GET /api/v1/audit-logs/ as Branch Manager → only own-branch entries
  TC-API16: DELETE /api/v1/audit-logs/ as any role → 405 Method Not Allowed
  TC-S12  : PUT /api/v1/audit-logs/ as any role including Super Admin → 405

Since Expense and MenuItem models are stubs (Tasks 10 and 13 respectively),
TC-F03, TC-F04, and TC-M05 simulate what the audit log would contain *after*
those operations by creating AuditLog entries directly with the appropriate
action codes and old_value/new_value payloads.  The tests verify that the
AuditLog API correctly surfaces those values.

Validates: Requirements 5.1, 5.4, 5.5, 5.6, 5.7
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditLog, AuditLogStatus
from apps.authentication.models import UserRole
from apps.branches.models import Branch

User = get_user_model()

AUDIT_LOG_LIST_URL = "/api/v1/audit-logs/"

# ---------------------------------------------------------------------------
# Module-level helpers (mirrors test_property_audit_scope_enforcement.py)
# ---------------------------------------------------------------------------


def _make_branch(name: str) -> Branch:
    return Branch.objects.create(name=name)


def _make_user(role: str, branch: Branch = None) -> User:
    email = f"{role.lower().replace('_', '')}_{uuid.uuid4().hex[:8]}@test.example.com"
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=role,
        branch=branch,
    )


def _get_branch_uuid(branch: Branch) -> uuid.UUID:
    """Return the UUID PK of the branch."""
    return branch.pk if isinstance(branch.pk, uuid.UUID) else uuid.UUID(int=branch.pk)


def _make_audit_log(
    *,
    action: str,
    resource_type: str = "TestResource",
    branch_id=None,
    tenant_id=None,
    old_value=None,
    new_value=None,
    status: str = AuditLogStatus.SUCCESS,
) -> AuditLog:
    return AuditLog.objects.create(
        branch_id=branch_id,
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        user_role="Branch_Manager",
        ip_address="127.0.0.1",
        user_agent="TestAgent/1.0",
        action=action,
        resource_type=resource_type,
        resource_id=uuid.uuid4(),
        old_value=old_value,
        new_value=new_value,
        status=status,
    )


def _get_all_results(client: APIClient, url: str) -> list:
    """Follow cursor-pagination links and return flat result list."""
    all_results = []
    while url:
        response = client.get(url)
        assert response.status_code == 200, (
            f"Expected HTTP 200, got {response.status_code}: "
            f"{getattr(response, 'data', response.content)!r}"
        )
        data = response.data
        if isinstance(data, dict) and "results" in data:
            all_results.extend(data["results"])
            url = data.get("next")
        elif isinstance(data, list):
            all_results.extend(data)
            url = None
        else:
            url = None
    return all_results


# ---------------------------------------------------------------------------
# TC-F03: Expense PATCH → audit log shows old_value and new_value
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCF03ExpensePatchAuditValues:
    """
    TC-F03: After PATCH on an expense resource, the audit log entry shows
    both old_value and new_value.

    Approach: create an AuditLog entry with action='EXPENSE_UPDATE' that
    carries both old_value (the pre-patch state) and new_value (the post-patch
    state).  Authenticate as a Super Admin (unfiltered view) and verify the
    entry is returned with the correct old/new values.

    Validates: Requirements 5.1, 5.4
    """

    def test_expense_update_log_contains_old_value(self, db):
        old_data = {"amount": "100.00", "category": "food", "description": "Old entry"}
        new_data = {"amount": "150.00", "category": "food", "description": "Updated entry"}

        log = _make_audit_log(
            action="EXPENSE_UPDATE",
            resource_type="Expense",
            old_value=old_data,
            new_value=new_data,
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1, (
            f"TC-F03: expected exactly 1 EXPENSE_UPDATE entry with log_id={log.log_id}, "
            f"found {len(matching)} (Requirement 5.1)"
        )
        entry = matching[0]
        assert entry["old_value"] is not None, (
            "TC-F03: EXPENSE_UPDATE audit entry must have old_value set (Requirement 5.1)"
        )
        assert entry["old_value"]["amount"] == "100.00", (
            f"TC-F03: old_value.amount must be '100.00', got {entry['old_value'].get('amount')!r}"
        )

    def test_expense_update_log_contains_new_value(self, db):
        old_data = {"amount": "100.00", "category": "food"}
        new_data = {"amount": "150.00", "category": "food"}

        log = _make_audit_log(
            action="EXPENSE_UPDATE",
            resource_type="Expense",
            old_value=old_data,
            new_value=new_data,
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1, (
            f"TC-F03: expected exactly 1 EXPENSE_UPDATE entry, found {len(matching)}"
        )
        entry = matching[0]
        assert entry["new_value"] is not None, (
            "TC-F03: EXPENSE_UPDATE audit entry must have new_value set (Requirement 5.1)"
        )
        assert entry["new_value"]["amount"] == "150.00", (
            f"TC-F03: new_value.amount must be '150.00', got {entry['new_value'].get('amount')!r}"
        )

    def test_expense_update_log_action_code_is_correct(self, db):
        log = _make_audit_log(
            action="EXPENSE_UPDATE",
            resource_type="Expense",
            old_value={"amount": "50.00"},
            new_value={"amount": "75.00"},
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1
        assert matching[0]["action"] == "EXPENSE_UPDATE", (
            f"TC-F03: action must be 'EXPENSE_UPDATE', got {matching[0]['action']!r}"
        )


# ---------------------------------------------------------------------------
# TC-F04: Expense DELETE → audit log records deletion with old_value set
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCF04ExpenseDeleteAuditValues:
    """
    TC-F04: After DELETE on an expense resource, the audit log entry records
    the deletion with old_value set and new_value=None.

    Approach: create an AuditLog entry with action='EXPENSE_DELETE' that
    carries the pre-deletion state in old_value and None as new_value.
    Verify the API surfaces both fields correctly.

    Validates: Requirements 5.1, 5.4
    """

    def test_expense_delete_log_has_old_value(self, db):
        old_data = {"amount": "200.00", "category": "utilities", "description": "Internet bill"}

        log = _make_audit_log(
            action="EXPENSE_DELETE",
            resource_type="Expense",
            old_value=old_data,
            new_value=None,
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1, (
            f"TC-F04: expected 1 EXPENSE_DELETE entry, found {len(matching)} (Requirement 5.1)"
        )
        entry = matching[0]
        assert entry["old_value"] is not None, (
            "TC-F04: EXPENSE_DELETE audit entry must have old_value set (Requirement 5.1)"
        )
        assert entry["old_value"]["amount"] == "200.00", (
            f"TC-F04: old_value.amount must be '200.00', got {entry['old_value'].get('amount')!r}"
        )

    def test_expense_delete_log_new_value_is_null(self, db):
        log = _make_audit_log(
            action="EXPENSE_DELETE",
            resource_type="Expense",
            old_value={"amount": "200.00"},
            new_value=None,
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1
        entry = matching[0]
        assert entry["new_value"] is None, (
            f"TC-F04: EXPENSE_DELETE new_value must be null for a deletion, "
            f"got {entry['new_value']!r} (Requirement 5.1)"
        )

    def test_expense_delete_log_action_code(self, db):
        log = _make_audit_log(
            action="EXPENSE_DELETE",
            resource_type="Expense",
            old_value={"amount": "99.00"},
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1
        assert matching[0]["action"] == "EXPENSE_DELETE", (
            f"TC-F04: action must be 'EXPENSE_DELETE', got {matching[0]['action']!r}"
        )


# ---------------------------------------------------------------------------
# TC-M05: MenuItem price change → audit log entry with old and new price
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCM05MenuItemPriceChangeAudit:
    """
    TC-M05: After PATCH on a menu item (price change), the audit log entry
    contains old and new price in old_value and new_value respectively.

    Approach: create an AuditLog entry with action='MENUITEM_PRICE_CHANGE'
    carrying {'price': '10.00'} as old_value and {'price': '15.00'} as
    new_value.  Verify the API returns the entry with correct price values.

    Validates: Requirements 5.1
    """

    def test_menu_item_price_change_log_old_price(self, db):
        log = _make_audit_log(
            action="MENUITEM_PRICE_CHANGE",
            resource_type="MenuItem",
            old_value={"price": "10.00", "name": "Shiro"},
            new_value={"price": "15.00", "name": "Shiro"},
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1, (
            f"TC-M05: expected 1 MENUITEM_PRICE_CHANGE entry, found {len(matching)}"
        )
        entry = matching[0]
        assert entry["old_value"]["price"] == "10.00", (
            f"TC-M05: old_value.price must be '10.00', got {entry['old_value'].get('price')!r} "
            f"(Requirement 5.1)"
        )

    def test_menu_item_price_change_log_new_price(self, db):
        log = _make_audit_log(
            action="MENUITEM_PRICE_CHANGE",
            resource_type="MenuItem",
            old_value={"price": "10.00"},
            new_value={"price": "15.00"},
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1
        entry = matching[0]
        assert entry["new_value"]["price"] == "15.00", (
            f"TC-M05: new_value.price must be '15.00', got {entry['new_value'].get('price')!r} "
            f"(Requirement 5.1)"
        )

    def test_menu_item_price_change_log_action_code(self, db):
        log = _make_audit_log(
            action="MENUITEM_PRICE_CHANGE",
            resource_type="MenuItem",
            old_value={"price": "10.00"},
            new_value={"price": "15.00"},
        )

        admin = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(admin)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        matching = [r for r in all_results if r.get("log_id") == str(log.log_id)]

        assert len(matching) == 1
        assert matching[0]["action"] == "MENUITEM_PRICE_CHANGE", (
            f"TC-M05: action must be 'MENUITEM_PRICE_CHANGE', got {matching[0]['action']!r}"
        )


# ---------------------------------------------------------------------------
# TC-API15: GET /api/v1/audit-logs/ as Branch Manager → only own-branch entries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCAPI15BranchManagerScopedView:
    """
    TC-API15: GET /api/v1/audit-logs/ as a Branch Manager returns ONLY entries
    for that manager's own branch — entries from other branches must not appear.

    Validates: Requirements 5.5, 5.6, 5.7
    """

    def test_branch_manager_sees_only_own_branch_entries(self, db):
        suffix = uuid.uuid4().hex[:8]
        branch_a = _make_branch(f"Branch A {suffix}")
        branch_b = _make_branch(f"Branch B {suffix}")
        branch_a_uuid = _get_branch_uuid(branch_a)
        branch_b_uuid = _get_branch_uuid(branch_b)

        manager_a = _make_user(UserRole.BRANCH_MANAGER, branch=branch_a)

        # 3 entries for branch_a, 2 for branch_b
        for _ in range(3):
            _make_audit_log(action="OWN_ACTION", branch_id=branch_a_uuid)
        for _ in range(2):
            _make_audit_log(action="OTHER_ACTION", branch_id=branch_b_uuid)

        client = APIClient()
        client.force_login(manager_a)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)

        branch_a_str = str(branch_a_uuid)
        branch_b_str = str(branch_b_uuid)

        for entry in all_results:
            assert entry.get("branch_id") == branch_a_str, (
                f"TC-API15: Branch Manager received entry with branch_id="
                f"{entry.get('branch_id')!r}, expected only {branch_a_str!r}. "
                f"Branch Managers must see only their own branch entries "
                f"(Requirements 5.6, 5.7)."
            )

        other_entries = [r for r in all_results if r.get("branch_id") == branch_b_str]
        assert len(other_entries) == 0, (
            f"TC-API15: Branch Manager received {len(other_entries)} entries from "
            f"branch_b — these must not appear (Requirements 5.6, 5.7)."
        )

    def test_branch_manager_sees_correct_count_of_own_entries(self, db):
        suffix = uuid.uuid4().hex[:8]
        branch_a = _make_branch(f"Branch A {suffix}")
        branch_b = _make_branch(f"Branch B {suffix}")
        branch_a_uuid = _get_branch_uuid(branch_a)
        branch_b_uuid = _get_branch_uuid(branch_b)

        manager_a = _make_user(UserRole.BRANCH_MANAGER, branch=branch_a)

        for _ in range(4):
            _make_audit_log(action="SCOPE_TEST", branch_id=branch_a_uuid)
        for _ in range(3):
            _make_audit_log(action="SCOPE_OTHER", branch_id=branch_b_uuid)

        client = APIClient()
        client.force_login(manager_a)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        branch_a_str = str(branch_a_uuid)
        own_entries = [r for r in all_results if r.get("branch_id") == branch_a_str]

        assert len(own_entries) == 4, (
            f"TC-API15: Branch Manager should see exactly 4 own-branch entries, "
            f"got {len(own_entries)} (Requirements 5.6, 5.7)."
        )

    def test_branch_manager_null_branch_entries_not_visible(self, db):
        """Entries with branch_id=None must not appear for a Branch Manager."""
        suffix = uuid.uuid4().hex[:8]
        branch_a = _make_branch(f"Branch A {suffix}")
        branch_a_uuid = _get_branch_uuid(branch_a)

        manager_a = _make_user(UserRole.BRANCH_MANAGER, branch=branch_a)

        _make_audit_log(action="OWN_ACTION", branch_id=branch_a_uuid)
        _make_audit_log(action="NULL_BRANCH_ACTION", branch_id=None)

        client = APIClient()
        client.force_login(manager_a)

        all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)
        branch_a_str = str(branch_a_uuid)

        null_entries = [r for r in all_results if r.get("branch_id") is None]
        assert len(null_entries) == 0, (
            f"TC-API15: Branch Manager must not see null-branch entries, "
            f"found {len(null_entries)} (Requirements 5.6, 5.7)."
        )

        own_entries = [r for r in all_results if r.get("branch_id") == branch_a_str]
        assert len(own_entries) == 1, (
            f"TC-API15: expected 1 own-branch entry, got {len(own_entries)}."
        )


# ---------------------------------------------------------------------------
# TC-API16: DELETE /api/v1/audit-logs/ as any role → 405 Method Not Allowed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCAPI16DeleteNotAllowed:
    """
    TC-API16: DELETE /api/v1/audit-logs/ as any role returns 405 Method Not Allowed.

    AuditLogs are immutable — the router only registers GET routes.  Any
    DELETE attempt on the list or detail URL must return 405.

    Validates: Requirements 5.4
    """

    def test_delete_list_as_branch_manager_returns_405(self, db):
        branch = _make_branch(f"Branch {uuid.uuid4().hex[:6]}")
        user = _make_user(UserRole.BRANCH_MANAGER, branch=branch)
        client = APIClient()
        client.force_login(user)

        response = client.delete(AUDIT_LOG_LIST_URL)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-API16: DELETE /api/v1/audit-logs/ as Branch_Manager must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_delete_list_as_tenant_owner_returns_405(self, db):
        user = _make_user(UserRole.TENANT_OWNER)
        client = APIClient()
        client.force_login(user)

        response = client.delete(AUDIT_LOG_LIST_URL)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-API16: DELETE /api/v1/audit-logs/ as Tenant_Owner must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_delete_list_as_super_admin_returns_405(self, db):
        user = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(user)

        response = client.delete(AUDIT_LOG_LIST_URL)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-API16: DELETE /api/v1/audit-logs/ as Super_Admin must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_delete_detail_as_super_admin_returns_405(self, db):
        """DELETE on a specific log entry must also be rejected with 405."""
        log = _make_audit_log(action="SOME_ACTION")
        url = f"{AUDIT_LOG_LIST_URL}{log.log_id}/"

        user = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(user)

        response = client.delete(url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-API16: DELETE on a specific audit log entry must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )


# ---------------------------------------------------------------------------
# TC-S12: PUT /api/v1/audit-logs/ as any role including Super Admin → 405
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCS12PutNotAllowed:
    """
    TC-S12: PUT /api/v1/audit-logs/ as any role, including Super Admin,
    returns 405 Method Not Allowed.

    AuditLogs are immutable — PUT (full-replace) is never permitted regardless
    of privilege level.

    Validates: Requirements 5.4
    """

    def test_put_list_as_super_admin_returns_405(self, db):
        user = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(user)

        response = client.put(AUDIT_LOG_LIST_URL, data={}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-S12: PUT /api/v1/audit-logs/ as Super_Admin must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_put_list_as_tenant_owner_returns_405(self, db):
        user = _make_user(UserRole.TENANT_OWNER)
        client = APIClient()
        client.force_login(user)

        response = client.put(AUDIT_LOG_LIST_URL, data={}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-S12: PUT /api/v1/audit-logs/ as Tenant_Owner must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_put_list_as_branch_manager_returns_405(self, db):
        branch = _make_branch(f"Branch {uuid.uuid4().hex[:6]}")
        user = _make_user(UserRole.BRANCH_MANAGER, branch=branch)
        client = APIClient()
        client.force_login(user)

        response = client.put(AUDIT_LOG_LIST_URL, data={}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-S12: PUT /api/v1/audit-logs/ as Branch_Manager must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_put_detail_as_super_admin_returns_405(self, db):
        """PUT on a specific log entry must also be rejected."""
        log = _make_audit_log(action="SOME_ACTION")
        url = f"{AUDIT_LOG_LIST_URL}{log.log_id}/"

        user = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(user)

        response = client.put(url, data={"action": "TAMPERED"}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-S12: PUT on a specific audit log entry as Super_Admin must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )

    def test_patch_list_as_super_admin_returns_405(self, db):
        """
        PATCH on the list URL is also disallowed — included here to cover the
        full range of mutating verbs that must be blocked.
        """
        user = _make_user(UserRole.SUPER_ADMIN)
        client = APIClient()
        client.force_login(user)

        response = client.patch(AUDIT_LOG_LIST_URL, data={}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-S12 (PATCH): PATCH /api/v1/audit-logs/ as Super_Admin must return 405, "
            f"got {response.status_code} (Requirement 5.4)."
        )
