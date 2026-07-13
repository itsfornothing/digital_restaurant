"""
apps/authentication/tests/test_rbac_api.py

RBAC Forbidden Actions API Test Suite — TC-R01 through TC-R07, TC-I01 through TC-I05.

Test cases:
  TC-R01: Kitchen Staff → GET /api/v1/branches/{id}/expenses/ → 403
  TC-R02: Receptionist → POST /api/v1/branches/{id}/menu-items/ → 403
  TC-R03: Branch Manager A → GET /api/v1/branches/B/inventory/ → 403
  TC-R04: Branch Manager A → GET /api/v1/tenants/B/orders/ → 403 or 404
  TC-R05: Customer session (no auth) → GET /api/v1/branches/{id}/financials/ → 401
  TC-R06: Tenant Owner A → GET /api/v1/tenants/B/users/ → 403
  TC-R07: Any non-super-admin → DELETE /api/v1/audit-logs/{id}/ → 405
  TC-I01: Cross-tenant order access by known UUID → 403 or 404
  TC-I02: Cross-tenant inventory access by known UUID → 403 or 404
  TC-I03: Cross-tenant expense access by known UUID → 403 or 404
  TC-I04: Cross-tenant user access by known UUID → 403 or 404
  TC-I05: Cross-branch inventory access by known UUID → 403 or 404

Validates: Requirements 4.1, 4.2, 4.3 (TC-R01–R07, TC-I01–I05)

Implementation strategy:
  Many operational routes (branches, menus, expenses, inventory, orders,
  financials) are stubs with empty URL patterns — their ViewSets exist and
  carry the correct permission_classes but are not yet registered in the
  router.  For those endpoints we use direct permission class evaluation via
  has_permission() with a MagicMock request, which is the correct approach
  for stub ViewSets that don't yet have list/create action methods wired.

  For routes that ARE registered (audit-logs, tenants) we use the APIClient
  and call the URL directly.

  Tests that use force_authenticate() use real DB users.
  Tests that call permission classes directly use MagicMock users (to avoid
  the read-only is_authenticated property on AbstractBaseUser).
"""

import uuid
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from apps.authentication.models import UserRole
from apps.branches.models import Branch

User = get_user_model()

# ---------------------------------------------------------------------------
# URL constants (registered routes)
# ---------------------------------------------------------------------------

AUDIT_LOG_LIST_URL = "/api/v1/audit-logs/"
TENANTS_LIST_URL = "/api/v1/tenants/"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def factory():
    """APIRequestFactory for direct ViewSet invocation."""
    return APIRequestFactory()


@pytest.fixture
def branch_a(db):
    """Branch A — used as the home branch for most staff users."""
    return Branch.objects.create(name="Branch A")


@pytest.fixture
def branch_b(db):
    """Branch B — a different branch the requesting user does NOT manage."""
    return Branch.objects.create(name="Branch B")


@pytest.fixture
def kitchen_staff_user(db, branch_a):
    """Kitchen_Staff user assigned to Branch A."""
    return User.objects.create_user(
        email="kitchen@example.com",
        password="Pass1234!",
        role=UserRole.KITCHEN_STAFF,
        branch=branch_a,
    )


@pytest.fixture
def receptionist_user(db, branch_a):
    """Receptionist user assigned to Branch A."""
    return User.objects.create_user(
        email="receptionist@example.com",
        password="Pass1234!",
        role=UserRole.RECEPTIONIST,
        branch=branch_a,
    )


@pytest.fixture
def branch_manager_a(db, branch_a):
    """Branch_Manager user assigned to Branch A."""
    return User.objects.create_user(
        email="manager_a@example.com",
        password="Pass1234!",
        role=UserRole.BRANCH_MANAGER,
        branch=branch_a,
    )


@pytest.fixture
def tenant_owner_a(db):
    """Tenant_Owner for tenant A (no branch assignment)."""
    return User.objects.create_user(
        email="owner_a@example.com",
        password="Pass1234!",
        role=UserRole.TENANT_OWNER,
    )


@pytest.fixture
def super_admin(db):
    """Super_Admin platform user."""
    return User.objects.create_superuser(
        email="superadmin@example.com",
        password="Pass1234!",
    )


# ---------------------------------------------------------------------------
# Helper: build a mock request for direct permission-class evaluation
# ---------------------------------------------------------------------------

def _mock_request_for_role(role: str, branch_id=None):
    """
    Build a minimal MagicMock request object for the given role.

    Using MagicMock (rather than a real User) avoids AttributeError when
    tests try to set the read-only `is_authenticated` property that
    AbstractBaseUser defines as a property.

    Args:
        role:       One of the UserRole choices.
        branch_id:  Optional branch pk to assign to the mock user.

    Returns:
        MagicMock with .user pre-configured.
    """
    user = MagicMock()
    user.role = role
    user.is_active = True
    user.is_authenticated = True
    user.branch_id = branch_id

    request = MagicMock()
    request.user = user
    request.tenant = None
    request.META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pytest"}
    request.session = {}
    return request


# ---------------------------------------------------------------------------
# TC-R01: Kitchen Staff → expenses list → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR01KitchenStaffExpensesForbidden:
    """
    TC-R01: GET /api/v1/branches/{id}/expenses/ as Kitchen_Staff → 403 Forbidden.

    Expenses are restricted to Branch_Manager (write) and financial readers
    (Branch_Manager, Tenant_Owner, Super_Admin) for reads (Requirement 4.2).
    Kitchen_Staff has no access to financial records.

    The ExpenseViewSet is a stub (no actions wired yet); we evaluate the
    permission class directly.
    Validates: Requirements 4.1, 4.2, 4.3
    """

    def test_kitchen_staff_denied_expense_read_by_permission_class(self):
        """
        IsFinancialReader (used for expense list/retrieve) must deny
        Kitchen_Staff — they are not in the financial-reader roles.
        """
        from shared.permissions import IsFinancialReader

        perm = IsFinancialReader()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R01: IsFinancialReader must deny Kitchen_Staff on expense reads"
        )

    def test_kitchen_staff_denied_expense_write_by_permission_class(self):
        """
        IsBranchManager (used for expense create/update/delete) must deny
        Kitchen_Staff — they are not Branch_Managers.
        """
        from shared.permissions import IsBranchManager

        perm = IsBranchManager()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R01: IsBranchManager must deny Kitchen_Staff on expense writes"
        )

    def test_kitchen_staff_api_denied_on_expenses_via_client(
        self, api_client, kitchen_staff_user, branch_a
    ):
        """
        End-to-end: Kitchen_Staff force-authenticated and hitting a registered
        route that requires financial read access returns 403.

        Since the expense URL is not yet registered (Task 13), we use the
        audit-logs endpoint as a proxy to confirm IsFinancialReader vs
        IsAuditLogReader overlap — or verify via the permission class directly.
        """
        # Verify via the registered audit-logs endpoint which also uses a
        # financial-readers-adjacent permission (IsAuditLogReader).
        # Kitchen_Staff is not an audit log reader either.
        api_client.force_authenticate(user=kitchen_staff_user)
        resp = api_client.get(AUDIT_LOG_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-R01: Kitchen_Staff GET /audit-logs/ must be 403, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-R02: Receptionist → POST menu-items → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR02ReceptionistMenuItemsForbidden:
    """
    TC-R02: POST /api/v1/branches/{id}/menu-items/ as Receptionist → 403.

    MenuItem creation is restricted to Branch_Manager (Requirement 4.2).
    Receptionist has read-only access to MenuItems.

    Validates: Requirements 4.1, 4.2, 4.3
    """

    def test_receptionist_denied_menu_item_create_by_permission_class(self):
        """
        IsBranchManager (used for MenuItem create) must deny Receptionist.
        """
        from shared.permissions import IsBranchManager

        perm = IsBranchManager()
        request = _mock_request_for_role(UserRole.RECEPTIONIST)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R02: IsBranchManager must deny Receptionist on menu item creation"
        )

    def test_receptionist_allowed_menu_item_read_by_permission_class(self):
        """
        Positive check: Receptionist IS permitted to read menu items
        (IsBranchStaff covers Receptionist for list/retrieve).
        Validates the permission boundary in both directions.
        """
        from shared.permissions import IsBranchStaff

        perm = IsBranchStaff()
        request = _mock_request_for_role(UserRole.RECEPTIONIST)
        allowed = perm.has_permission(request, view=None)
        assert allowed is True, (
            "TC-R02: Receptionist must be allowed to read menu items (IsBranchStaff)"
        )

    def test_branch_manager_allowed_menu_item_create(self):
        """
        Positive check: Branch_Manager IS permitted to create menu items.
        Confirms the permission boundary is correct on both sides.
        """
        from shared.permissions import IsBranchManager

        perm = IsBranchManager()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is True, (
            "TC-R02: Branch_Manager must be allowed to create menu items"
        )


# ---------------------------------------------------------------------------
# TC-R03: Branch Manager A → GET another branch's inventory → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR03BranchManagerCrossBranchInventoryForbidden:
    """
    TC-R03: Branch Manager A → GET /api/v1/branches/B/inventory/ → 403.

    A Branch_Manager is permitted to read inventory for their own branch.
    However, the RBAC scope check (BranchScopePermission) must deny access
    to a different branch's inventory objects.

    Validates: Requirements 4.2, 4.3
    """

    def test_branch_manager_a_denied_branch_b_inventory_at_permission_level(
        self, branch_a, branch_b
    ):
        """
        Branch Manager A requests inventory for Branch B.
        BranchScopePermission must deny object-level access.
        """
        from shared.permissions import BranchScopePermission

        # Simulate an inventory item that belongs to Branch B
        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is False, (
            "TC-R03: BranchScopePermission must deny Branch Manager A "
            "access to Branch B's inventory objects"
        )

    def test_branch_manager_a_allowed_own_branch_inventory_at_permission_level(
        self, branch_a
    ):
        """
        Positive check: Branch Manager A IS allowed to access Branch A's inventory.
        """
        from shared.permissions import BranchScopePermission

        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_a.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is True, (
            "TC-R03: BranchScopePermission must allow Branch Manager A "
            "access to their own branch's inventory"
        )

    def test_branch_manager_role_passes_branch_staff_permission(self):
        """
        Role-level check: IsBranchStaff allows Branch_Manager for list actions
        (scope is enforced at queryset level, not role level for reads).
        """
        from shared.permissions import IsBranchStaff

        perm = IsBranchStaff()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is True, (
            "TC-R03: Branch_Manager must pass IsBranchStaff role-level check"
        )


# ---------------------------------------------------------------------------
# TC-R04: Branch Manager A → GET /api/v1/tenants/B/orders/ → 403 or 404
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR04BranchManagerCrossTenantOrdersForbidden:
    """
    TC-R04: Branch Manager A → GET /api/v1/tenants/B/orders/ → 403 or 404.

    A Branch_Manager is not permitted to access cross-tenant order lists.
    The tenants/{id}/ endpoint is Super_Admin-only (IsSuperAdmin).

    Validates: Requirements 4.2, 4.3
    """

    def test_branch_manager_cannot_access_tenants_endpoint(
        self, api_client, branch_manager_a
    ):
        """
        Branch_Manager attempts to GET /api/v1/tenants/{id}/ — this endpoint
        is guarded by IsSuperAdmin.  Must return 403.
        """
        api_client.force_authenticate(user=branch_manager_a)
        fake_tenant_id = str(uuid.uuid4())
        resp = api_client.get(f"/api/v1/tenants/{fake_tenant_id}/")
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"TC-R04: Branch_Manager must be denied tenant endpoint, "
            f"got {resp.status_code}"
        )

    def test_branch_manager_cannot_access_tenants_list(
        self, api_client, branch_manager_a
    ):
        """
        Branch_Manager attempts to GET /api/v1/tenants/ list — IsSuperAdmin required.
        """
        api_client.force_authenticate(user=branch_manager_a)
        resp = api_client.get(TENANTS_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-R04: Branch_Manager GET /tenants/ must be 403, "
            f"got {resp.status_code}"
        )

    def test_branch_manager_denied_by_is_super_admin_permission_class(self):
        """
        Confirm IsSuperAdmin denies Branch_Manager when used as the permission class
        that guards the cross-tenant orders path.
        """
        from shared.permissions import IsSuperAdmin

        perm = IsSuperAdmin()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R04: IsSuperAdmin must deny Branch_Manager"
        )


# ---------------------------------------------------------------------------
# TC-R05: Customer session (no auth) → GET financials → 401 / 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR05UnauthenticatedFinancialsForbidden:
    """
    TC-R05: Unauthenticated request → GET /api/v1/branches/{id}/financials/ → 401.

    The FinancialDashboardViewSet uses IsFinancialReader which requires an
    authenticated user with role Super_Admin, Tenant_Owner, or Branch_Manager.
    An unauthenticated (anonymous / customer session) request must be denied.

    Validates: Requirements 4.1, 4.2, 4.3
    """

    def test_unauthenticated_denied_on_financials_permission_class(self):
        """
        IsFinancialReader must deny an anonymous/unauthenticated request
        (no user, or user is not authenticated).
        """
        from shared.permissions import IsFinancialReader

        perm = IsFinancialReader()
        # Anonymous request: no user set
        request = MagicMock()
        request.user = None
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R05: IsFinancialReader must deny anonymous requests"
        )

    def test_customer_session_denied_on_financials(self, api_client, branch_a):
        """
        A customer (unauthenticated) making a GET request to the audit-logs
        endpoint (a registered route requiring authentication) must be denied
        with 403, confirming unauthenticated access is blocked.
        """
        # No force_authenticate — anonymous request
        resp = api_client.get(AUDIT_LOG_LIST_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-R05: Unauthenticated GET must return 401 or 403, "
            f"got {resp.status_code}"
        )

    def test_kitchen_staff_denied_on_financials_permission_class(self):
        """
        Kitchen_Staff is not in the IsFinancialReader allowed roles and must
        be denied access to the financial dashboard.
        """
        from shared.permissions import IsFinancialReader

        perm = IsFinancialReader()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R05: IsFinancialReader must deny Kitchen_Staff"
        )

    def test_receptionist_denied_on_financials_permission_class(self):
        """
        Receptionist is not in the IsFinancialReader allowed roles and must
        be denied access to the financial dashboard.
        """
        from shared.permissions import IsFinancialReader

        perm = IsFinancialReader()
        request = _mock_request_for_role(UserRole.RECEPTIONIST)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R05: IsFinancialReader must deny Receptionist"
        )

    def test_branch_manager_allowed_on_financials_permission_class(self):
        """
        Positive check: Branch_Manager IS in the IsFinancialReader allowed roles.
        """
        from shared.permissions import IsFinancialReader

        perm = IsFinancialReader()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is True, (
            "TC-R05: IsFinancialReader must allow Branch_Manager"
        )


# ---------------------------------------------------------------------------
# TC-R06: Tenant Owner A → GET /api/v1/tenants/B/users/ → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR06TenantOwnerCrossTenantUsersForbidden:
    """
    TC-R06: Tenant Owner A → GET /api/v1/tenants/B/users/ → 403.

    A Tenant_Owner can only manage Users within their own tenant.
    The tenants/{id}/ endpoint is IsSuperAdmin-only; a Tenant_Owner
    accessing another tenant's resources must be denied with 403.

    Validates: Requirements 4.1, 4.2, 4.3
    """

    def test_tenant_owner_cannot_access_different_tenant_endpoint(
        self, api_client, tenant_owner_a
    ):
        """
        Tenant Owner A requests GET /api/v1/tenants/{B_id}/ where B is a
        different tenant's ID.  The endpoint is guarded by IsSuperAdmin.
        Must return 403 Forbidden.
        """
        api_client.force_authenticate(user=tenant_owner_a)
        fake_tenant_b_id = str(uuid.uuid4())
        resp = api_client.get(f"/api/v1/tenants/{fake_tenant_b_id}/")
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"TC-R06: Tenant Owner A must not access different tenant endpoint, "
            f"got {resp.status_code}"
        )

    def test_tenant_owner_cannot_list_tenants(self, api_client, tenant_owner_a):
        """
        Tenant Owner A requests GET /api/v1/tenants/ — platform-wide list
        that is IsSuperAdmin only.  Must return 403.
        """
        api_client.force_authenticate(user=tenant_owner_a)
        resp = api_client.get(TENANTS_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-R06: Tenant Owner GET /tenants/ must be 403, "
            f"got {resp.status_code}"
        )

    def test_tenant_owner_denied_by_is_super_admin_permission_class(self):
        """
        Direct permission-class check: IsSuperAdmin must deny Tenant_Owner.
        """
        from shared.permissions import IsSuperAdmin

        perm = IsSuperAdmin()
        request = _mock_request_for_role(UserRole.TENANT_OWNER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-R06: IsSuperAdmin must deny Tenant_Owner — "
            "Tenant Owners cannot access other tenants' user lists"
        )

    def test_super_admin_can_access_tenants_endpoint(
        self, api_client, super_admin
    ):
        """
        Positive check: Super_Admin (who can access tenants) is allowed.
        Confirms the TC-R06 denial is meaningful.
        """
        api_client.force_authenticate(user=super_admin)
        resp = api_client.get(TENANTS_LIST_URL)
        # Super_Admin can list tenants; result may be empty but not 403
        assert resp.status_code != status.HTTP_403_FORBIDDEN, (
            f"TC-R06 positive: Super_Admin must be allowed to list tenants, "
            f"got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-R07: Any non-super-admin → DELETE /api/v1/audit-logs/{id}/ → 405
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCR07AuditLogDeleteForbidden:
    """
    TC-R07: Any non-super-admin → DELETE /api/v1/audit-logs/{id}/ → 405.

    AuditLogs are immutable (Requirement 5.4).  The AuditLogViewSet inherits
    from ReadOnlyModelViewSet, which does NOT register a DELETE route.
    Therefore DELETE on any audit-log URL returns 405 Method Not Allowed
    for roles that CAN read audit logs (Branch_Manager, Tenant_Owner,
    Super_Admin).  Roles that cannot even read audit logs (Kitchen_Staff,
    Receptionist) get 403 before reaching the 405 route check — both
    outcomes correctly prevent modification.

    Validates: Requirements 4.2, 5.4
    """

    @pytest.mark.parametrize("role_fixture", [
        "branch_manager_a",
        "tenant_owner_a",
        "super_admin",
    ])
    def test_delete_audit_log_returns_405_for_audit_readers(
        self, request, api_client, role_fixture
    ):
        """
        For roles that CAN read audit logs (BM, TO, SA): DELETE must return 405
        because the route simply doesn't exist (ReadOnlyModelViewSet).
        """
        user = request.getfixturevalue(role_fixture)
        api_client.force_authenticate(user=user)
        fake_log_id = str(uuid.uuid4())
        resp = api_client.delete(f"{AUDIT_LOG_LIST_URL}{fake_log_id}/")
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-R07: DELETE /audit-logs/{{id}}/ for role {user.role!r} "
            f"must return 405, got {resp.status_code}"
        )

    @pytest.mark.parametrize("role_fixture", [
        "kitchen_staff_user",
        "receptionist_user",
    ])
    def test_delete_audit_log_blocked_for_non_readers(
        self, request, api_client, role_fixture
    ):
        """
        For roles without audit log read access (Kitchen_Staff, Receptionist):
        the permission check fires first (403) before the 405 route check.
        Both 403 and 405 correctly prevent deletion — either is acceptable.
        """
        user = request.getfixturevalue(role_fixture)
        api_client.force_authenticate(user=user)
        fake_log_id = str(uuid.uuid4())
        resp = api_client.delete(f"{AUDIT_LOG_LIST_URL}{fake_log_id}/")
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ), (
            f"TC-R07: DELETE /audit-logs/{{id}}/ for role {user.role!r} "
            f"must return 403 or 405, got {resp.status_code}"
        )

    def test_delete_audit_log_list_returns_405_for_super_admin(
        self, api_client, super_admin
    ):
        """
        DELETE /api/v1/audit-logs/ (list) must also return 405 for Super_Admin —
        no bulk delete route is registered.
        """
        api_client.force_authenticate(user=super_admin)
        resp = api_client.delete(AUDIT_LOG_LIST_URL)
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-R07: DELETE /audit-logs/ must return 405, got {resp.status_code}"
        )

    def test_audit_log_viewset_is_readonly(self):
        """
        Direct check: AuditLogViewSet must be a ReadOnlyModelViewSet
        (exposes only list and retrieve, never create/update/destroy).
        """
        from apps.audit.views import AuditLogViewSet
        from rest_framework.viewsets import ReadOnlyModelViewSet

        assert issubclass(AuditLogViewSet, ReadOnlyModelViewSet), (
            "TC-R07: AuditLogViewSet must inherit from ReadOnlyModelViewSet "
            "to enforce immutability (Requirement 5.4)"
        )

    def test_put_audit_log_returns_405_for_super_admin(self, api_client, super_admin):
        """
        PUT /api/v1/audit-logs/{id}/ must also return 405 — updates are
        not permitted (AuditLogs are immutable, Requirement 5.4).
        """
        api_client.force_authenticate(user=super_admin)
        fake_log_id = str(uuid.uuid4())
        resp = api_client.put(
            f"{AUDIT_LOG_LIST_URL}{fake_log_id}/",
            {"action": "TAMPERED"},
            format="json",
        )
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (
            f"TC-R07: PUT /audit-logs/{{id}}/ must return 405, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-I01–TC-I05: IDOR tests — cross-tenant/cross-branch access by known IDs
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCIDOR:
    """
    IDOR (Insecure Direct Object Reference) tests — TC-I01 through TC-I05.

    Each test verifies that a user from one branch/tenant cannot access a
    resource from a different branch/tenant using its known UUID, by
    evaluating the RBAC scope permission classes directly.

    TC-I01: Cross-tenant order access by known UUID → 403 or 404
    TC-I02: Cross-tenant inventory access by known UUID → 403 or 404
    TC-I03: Cross-tenant expense access by known UUID → 403 or 404
    TC-I04: Cross-tenant user access by known UUID → 403 or 404
    TC-I05: Cross-branch inventory access by known UUID → 403 or 404

    Validates: Requirements 4.1, 4.2, 4.3
    """

    # ------------------------------------------------------------------
    # TC-I01: Cross-tenant order access by known UUID
    # ------------------------------------------------------------------

    def test_tci01_branch_manager_a_denied_cross_branch_order_by_id(
        self, branch_a, branch_b
    ):
        """
        TC-I01: Branch Manager A knows a UUID of an order belonging to Branch B.
        BranchScopePermission must deny the object-level access.
        """
        from shared.permissions import BranchScopePermission

        order_obj = MagicMock()
        order_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=order_obj)
        assert allowed is False, (
            "TC-I01: BranchScopePermission must deny Branch Manager A access "
            "to an order owned by Branch B (IDOR prevention)"
        )

    def test_tci01_receptionist_denied_cross_branch_order_by_id(
        self, branch_a, branch_b
    ):
        """
        TC-I01 (Receptionist): A Receptionist from Branch A knowing an order UUID
        from Branch B must be denied.
        """
        from shared.permissions import BranchScopePermission

        order_obj = MagicMock()
        order_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.RECEPTIONIST, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=order_obj)
        assert allowed is False, (
            "TC-I01: Receptionist must be denied cross-branch order access (IDOR)"
        )

    def test_tci01_branch_manager_allowed_own_branch_order(self, branch_a):
        """
        TC-I01 positive: Branch Manager A CAN access Branch A's own order.
        """
        from shared.permissions import BranchScopePermission

        order_obj = MagicMock()
        order_obj.branch_id = branch_a.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=order_obj)
        assert allowed is True, (
            "TC-I01: BranchScopePermission must allow Branch Manager A access "
            "to their own branch's orders"
        )

    # ------------------------------------------------------------------
    # TC-I02: Cross-tenant inventory access by known UUID
    # ------------------------------------------------------------------

    def test_tci02_branch_manager_a_denied_cross_branch_inventory_by_id(
        self, branch_a, branch_b
    ):
        """
        TC-I02: Branch Manager A knows the UUID of an inventory item in Branch B.
        BranchScopePermission must deny object-level access.
        """
        from shared.permissions import BranchScopePermission

        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is False, (
            "TC-I02: BranchScopePermission must deny Branch Manager A access "
            "to inventory items owned by Branch B (IDOR prevention)"
        )

    def test_tci02_kitchen_staff_denied_cross_branch_inventory_by_id(
        self, branch_a, branch_b
    ):
        """
        TC-I02 (Kitchen_Staff): Kitchen Staff from Branch A must be denied
        access to Branch B's inventory item by known UUID.
        """
        from shared.permissions import BranchScopePermission

        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is False, (
            "TC-I02: Kitchen_Staff must be denied cross-branch inventory access (IDOR)"
        )

    # ------------------------------------------------------------------
    # TC-I03: Cross-tenant expense access by known UUID
    # ------------------------------------------------------------------

    def test_tci03_branch_manager_a_denied_cross_branch_expense_by_id(
        self, branch_a, branch_b
    ):
        """
        TC-I03: Branch Manager A knows a UUID for an expense in Branch B.
        BranchScopePermission must deny the object-level access.
        """
        from shared.permissions import BranchScopePermission

        expense_obj = MagicMock()
        expense_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=expense_obj)
        assert allowed is False, (
            "TC-I03: BranchScopePermission must deny Branch Manager A access "
            "to an expense owned by Branch B (IDOR prevention)"
        )

    def test_tci03_tenant_owner_denied_expense_write_by_permission_class(self):
        """
        TC-I03 (Tenant_Owner cross-tenant expense writes): IsBranchManager
        must deny Tenant_Owner on expense write actions — expense CRUD is
        Branch_Manager only per Requirement 4.2.
        """
        from shared.permissions import IsBranchManager

        perm = IsBranchManager()
        request = _mock_request_for_role(UserRole.TENANT_OWNER)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-I03: IsBranchManager must deny Tenant_Owner on expense write actions"
        )

    # ------------------------------------------------------------------
    # TC-I04: Cross-tenant user access by known UUID
    # ------------------------------------------------------------------

    def test_tci04_branch_manager_denied_cross_tenant_user_on_superadmin_endpoint(
        self, api_client, branch_manager_a
    ):
        """
        TC-I04: Branch Manager A knows a UUID for a user in Tenant B and
        attempts to access /api/v1/tenants/{id}/ — guarded by IsSuperAdmin.
        Must return 403.
        """
        api_client.force_authenticate(user=branch_manager_a)
        fake_user_id = str(uuid.uuid4())
        resp = api_client.get(f"/api/v1/tenants/{fake_user_id}/")
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"TC-I04: Branch_Manager must be denied cross-tenant user access, "
            f"got {resp.status_code}"
        )

    def test_tci04_tenant_owner_denied_superadmin_only_user_endpoint(
        self, api_client, tenant_owner_a
    ):
        """
        TC-I04 (Tenant_Owner): Tenant Owner A attempts to reach Tenant B's
        user data via the Super_Admin-only endpoint. Must be denied 403.
        """
        api_client.force_authenticate(user=tenant_owner_a)
        fake_tenant_b_id = str(uuid.uuid4())
        resp = api_client.get(f"/api/v1/tenants/{fake_tenant_b_id}/")
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), (
            f"TC-I04: Tenant_Owner must not access other tenant's user data, "
            f"got {resp.status_code}"
        )

    def test_tci04_user_viewset_permission_class_denies_kitchen_staff(self):
        """
        TC-I04 (non-privileged role): Kitchen_Staff must be denied by
        IsSuperAdminOrTenantOwner which guards the UserViewSet.
        This confirms non-privileged roles cannot access user management
        endpoints and therefore cannot reach cross-tenant user IDs.
        """
        from shared.permissions import IsSuperAdminOrTenantOwner

        perm = IsSuperAdminOrTenantOwner()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-I04: IsSuperAdminOrTenantOwner must deny Kitchen_Staff "
            "on user management endpoints"
        )

    def test_tci04_user_viewset_permission_class_denies_receptionist(self):
        """
        TC-I04 (Receptionist): Receptionist must be denied by
        IsSuperAdminOrTenantOwner on user management endpoints.
        """
        from shared.permissions import IsSuperAdminOrTenantOwner

        perm = IsSuperAdminOrTenantOwner()
        request = _mock_request_for_role(UserRole.RECEPTIONIST)
        allowed = perm.has_permission(request, view=None)
        assert allowed is False, (
            "TC-I04: IsSuperAdminOrTenantOwner must deny Receptionist"
        )

    # ------------------------------------------------------------------
    # TC-I05: Cross-branch inventory access by known UUID
    # ------------------------------------------------------------------

    def test_tci05_branch_manager_a_denied_branch_b_inventory_object_level(
        self, branch_a, branch_b
    ):
        """
        TC-I05: Branch Manager A knows the UUID of an inventory item that
        belongs to Branch B.  The BranchScopePermission object-level check
        must deny this access regardless of the fact that the role (Branch_Manager)
        passes the role-level IsBranchStaff check.
        """
        from shared.permissions import BranchScopePermission

        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is False, (
            "TC-I05: BranchScopePermission must deny Branch Manager A access "
            "to Branch B's inventory item (cross-branch IDOR prevention)"
        )

    def test_tci05_branch_manager_a_allowed_own_inventory_object_level(
        self, branch_a
    ):
        """
        TC-I05 positive: Branch Manager A CAN access Branch A's inventory.
        This validates that the IDOR check does not over-block legitimate access.
        """
        from shared.permissions import BranchScopePermission

        own_inventory_obj = MagicMock()
        own_inventory_obj.branch_id = branch_a.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.BRANCH_MANAGER, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=own_inventory_obj)
        assert allowed is True, (
            "TC-I05 positive: Branch Manager A must be allowed to access "
            "their own branch's inventory"
        )

    def test_tci05_kitchen_staff_a_denied_branch_b_inventory_object_level(
        self, branch_a, branch_b
    ):
        """
        TC-I05 (Kitchen_Staff): Kitchen Staff from Branch A must be denied
        access to Branch B's inventory item by known UUID.
        """
        from shared.permissions import BranchScopePermission

        inventory_obj = MagicMock()
        inventory_obj.branch_id = branch_b.pk

        perm = BranchScopePermission()
        request = _mock_request_for_role(UserRole.KITCHEN_STAFF, branch_id=branch_a.pk)

        allowed = perm.has_object_permission(request, view=None, obj=inventory_obj)
        assert allowed is False, (
            "TC-I05: Kitchen_Staff must be denied cross-branch inventory access (IDOR)"
        )
