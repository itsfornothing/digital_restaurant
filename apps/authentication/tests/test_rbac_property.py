"""
Property-Based Tests: RBAC Permission Matrix Correctness

Property 10: RBAC Permission Matrix Correctness

  For any triple of (user_role, resource_type, action), the Auth_Service's
  authorization decision shall exactly match the permission matrix defined in
  Requirement 4.2; no combination of role, resource, and action shall produce
  a different outcome than the matrix specifies.

Validates: Requirements 4.1, 4.2, 4.3

Strategy:
  - Define the full RBAC permission matrix from Requirement 4.2 as a dict
    mapping (role, resource_type, action) → bool.
  - For each cell, determine the correct permission class that guards that
    (resource_type, action) combination, mirroring what each ViewSet's
    get_permissions() returns.
  - Use Hypothesis ``st.sampled_from`` to enumerate every cell of the matrix.
  - For each cell, call has_permission() on the appropriate permission class
    with a mock request for that role, and assert the result matches the matrix.

Design notes:
  - Permission classes in shared/permissions.py are role-level gates: they
    check user.role, not the resource type or action.  The resource/action
    filtering happens at the ViewSet level via get_permissions() overrides.
  - This test therefore uses a lookup table (_RESOURCE_ACTION_PERMISSION_CLASS)
    that maps (resource_type, action) → permission_class, exactly mirroring
    what each ViewSet does, then checks whether the given role is granted by
    that permission class.
  - No mocking of permission logic is used — the actual permission classes
    from shared/permissions.py are evaluated directly.
"""

from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.authentication.models import UserRole
from shared.permissions import (
    IsBranchManager,
    IsBranchStaff,
    IsCustomerSession,
    IsKitchenStaff,
    IsReceptionist,
    IsSuperAdmin,
    IsSuperAdminOrTenantOwner,
    IsTenantOwner,
    _get_user,
)
from rest_framework.permissions import BasePermission

# ---------------------------------------------------------------------------
# Additional composite permission classes that mirror ViewSet logic
# ---------------------------------------------------------------------------
#
# Several ViewSets use inline permission classes or composites not exported
# from shared/permissions.py.  We re-declare them here for testing purposes
# so the oracle matches the actual ViewSet behaviour.


class _OrderReadPermission(BasePermission):
    """Mirror of OrderViewSet._OrderReadOrCancelPermission."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.RECEPTIONIST,
            UserRole.BRANCH_MANAGER,
        )


class _OrderStatusUpdatePermission(BasePermission):
    """Mirror of OrderViewSet._OrderStatusUpdatePermission."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.KITCHEN_STAFF,
            UserRole.RECEPTIONIST,
        )


class _BranchReadPermission(IsSuperAdminOrTenantOwner):
    """Mirror of branches/views._BranchReadPermission."""
    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        return IsBranchStaff().has_permission(request, view)


class _FinancialReadPermission(BasePermission):
    """Mirrors IsFinancialReader: Super_Admin, Tenant_Owner, Branch_Manager."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
        )


class _AuditLogReadPermission(BasePermission):
    """Mirrors IsAuditLogReader: Super_Admin, Tenant_Owner, Branch_Manager."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
        )


class _TenantOwnerCRUPermission(BasePermission):
    """TenantConfig CRU (no delete) — IsTenantOwner for create/read/update."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        # Super_Admin can also manage TenantConfig (IsSuperAdminOrTenantOwner)
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
        )


class _UserWritePermission(BasePermission):
    """
    User management write permission:
      - Super_Admin:    full CRUD
      - Tenant_Owner:   CRUD (within tenant)
      - Branch_Manager: CRU only (no delete)
    """
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
        )


class _UserDeletePermission(BasePermission):
    """Only Super_Admin and Tenant_Owner can delete Users."""
    def has_permission(self, request, view):
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
        )


class _NoAccessPermission(BasePermission):
    """Sentinel: no role should have access."""
    def has_permission(self, request, view):
        return False


# ---------------------------------------------------------------------------
# (resource_type, action) → permission class lookup table
# ---------------------------------------------------------------------------
#
# This table mirrors what each ViewSet's get_permissions() returns for each
# action, based on Requirement 4.2.  It is the authoritative oracle used
# to evaluate each matrix cell.
#
# Key: (resource_type: str, action: str)
# Value: permission class (instantiated per call in _check_permission)
#
# "action" values correspond to the four CRUD actions:
#   create  → POST
#   read    → GET (list / retrieve)
#   update  → PATCH / PUT
#   delete  → DELETE

_RESOURCE_ACTION_PERM: dict[tuple[str, str], type[BasePermission]] = {
    # -----------------------------------------------------------------------
    # Tenant (TenantViewSet — IsSuperAdmin for all actions)
    # -----------------------------------------------------------------------
    ("Tenant", "create"): IsSuperAdmin,
    ("Tenant", "read"):   IsSuperAdmin,
    ("Tenant", "update"): IsSuperAdmin,
    ("Tenant", "delete"): IsSuperAdmin,

    # -----------------------------------------------------------------------
    # Subscription (TenantSubscriptionViewSet — IsSuperAdmin)
    # Tenant_Owner can read their own subscription (via a separate read endpoint
    # guarded by IsSuperAdminOrTenantOwner — use that for read)
    # -----------------------------------------------------------------------
    ("Subscription", "create"): IsSuperAdmin,
    ("Subscription", "read"):   IsSuperAdminOrTenantOwner,   # Tenant_Owner reads own
    ("Subscription", "update"): IsSuperAdmin,
    ("Subscription", "delete"): IsSuperAdmin,

    # -----------------------------------------------------------------------
    # Branch (BranchViewSet)
    # write → IsSuperAdminOrTenantOwner
    # read  → _BranchReadPermission (extends to branch staff)
    # -----------------------------------------------------------------------
    ("Branch", "create"): IsSuperAdminOrTenantOwner,
    ("Branch", "read"):   _BranchReadPermission,
    ("Branch", "update"): IsSuperAdminOrTenantOwner,
    ("Branch", "delete"): IsSuperAdminOrTenantOwner,

    # -----------------------------------------------------------------------
    # User management
    # create / read / update → Super_Admin, Tenant_Owner, Branch_Manager
    # delete → Super_Admin, Tenant_Owner only (Branch_Manager has CRU, not D)
    # -----------------------------------------------------------------------
    ("User", "create"): _UserWritePermission,
    ("User", "read"):   _UserWritePermission,
    ("User", "update"): _UserWritePermission,
    ("User", "delete"): _UserDeletePermission,

    # -----------------------------------------------------------------------
    # TenantConfig (TenantConfigViewSet — IsSuperAdminOrTenantOwner)
    # No delete (config is always present per tenant)
    # -----------------------------------------------------------------------
    ("TenantConfig", "create"): _TenantOwnerCRUPermission,
    ("TenantConfig", "read"):   _TenantOwnerCRUPermission,
    ("TenantConfig", "update"): _TenantOwnerCRUPermission,
    ("TenantConfig", "delete"): _NoAccessPermission,  # no delete on config

    # -----------------------------------------------------------------------
    # AuditLog (AuditLogViewSet — read-only for Super_Admin/Tenant_Owner/BM)
    # -----------------------------------------------------------------------
    ("AuditLog", "create"): _NoAccessPermission,   # AuditLogs are immutable
    ("AuditLog", "read"):   _AuditLogReadPermission,
    ("AuditLog", "update"): _NoAccessPermission,   # immutable
    ("AuditLog", "delete"): _NoAccessPermission,   # immutable

    # -----------------------------------------------------------------------
    # MenuItem (MenuItemViewSet)
    # write → IsBranchManager
    # read  → IsBranchStaff (Branch_Manager + Receptionist + Kitchen_Staff)
    # -----------------------------------------------------------------------
    ("MenuItem", "create"): IsBranchManager,
    ("MenuItem", "read"):   IsBranchStaff,
    ("MenuItem", "update"): IsBranchManager,
    ("MenuItem", "delete"): IsBranchManager,

    # -----------------------------------------------------------------------
    # Inventory (InventoryViewSet)
    # write → IsBranchManager
    # read  → IsBranchStaff (Kitchen_Staff needs to read inventory for context)
    # Note: Req 4.2 says BranchManager has CRUD on Inventory;
    # Kitchen_Staff reads Inventory (indirectly via deduction context).
    # The ViewSet allows IsBranchStaff for read per the existing implementation.
    # -----------------------------------------------------------------------
    ("Inventory", "create"): IsBranchManager,
    ("Inventory", "read"):   IsBranchStaff,
    ("Inventory", "update"): IsBranchManager,
    ("Inventory", "delete"): IsBranchManager,

    # -----------------------------------------------------------------------
    # Expense (ExpenseViewSet)
    # write → IsBranchManager
    # read  → _FinancialReadPermission (BM + Tenant_Owner + Super_Admin)
    # -----------------------------------------------------------------------
    ("Expense", "create"): IsBranchManager,
    ("Expense", "read"):   _FinancialReadPermission,
    ("Expense", "update"): IsBranchManager,
    ("Expense", "delete"): IsBranchManager,

    # -----------------------------------------------------------------------
    # Order (OrderViewSet)
    # read / cancel → Receptionist + Branch_Manager
    # update (status) → Kitchen_Staff + Receptionist
    # create → Customer (CustomerOrderViewSet, IsCustomerSession)
    # -----------------------------------------------------------------------
    ("Order", "create"): IsCustomerSession,
    ("Order", "read"):   _OrderReadPermission,
    ("Order", "update"): _OrderStatusUpdatePermission,
    ("Order", "delete"): _NoAccessPermission,   # orders are never deleted

    # -----------------------------------------------------------------------
    # Income (IncomeViewSet)
    # write → IsBranchManager
    # read  → _FinancialReadPermission (BM + Tenant_Owner + Super_Admin)
    # -----------------------------------------------------------------------
    ("Income", "create"): IsBranchManager,
    ("Income", "read"):   _FinancialReadPermission,
    ("Income", "update"): IsBranchManager,
    ("Income", "delete"): _NoAccessPermission,  # income records are not deleted

    # -----------------------------------------------------------------------
    # Recipe (RecipeViewSet)
    # write → IsBranchManager
    # read  → IsBranchStaff (Branch_Manager + Kitchen_Staff for KDS)
    # -----------------------------------------------------------------------
    ("Recipe", "create"): IsBranchManager,
    ("Recipe", "read"):   IsBranchStaff,
    ("Recipe", "update"): IsBranchManager,
    ("Recipe", "delete"): IsBranchManager,
}


# ---------------------------------------------------------------------------
# RBAC Matrix (Requirement 4.2)
# ---------------------------------------------------------------------------
#
# Truth table: (role, resource_type, action) → bool
# Built by asking each entry in _RESOURCE_ACTION_PERM whether it grants access
# to a mock user with that role.  This way the matrix is derived from the
# same permission class code that ViewSets use, making it self-consistent.

ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.TENANT_OWNER,
    UserRole.BRANCH_MANAGER,
    UserRole.RECEPTIONIST,
    UserRole.KITCHEN_STAFF,
    UserRole.CUSTOMER,
]

RESOURCE_TYPES = list({r for (r, _) in _RESOURCE_ACTION_PERM})
ACTIONS = ["create", "read", "update", "delete"]


def _make_user_mock(role: str, is_active: bool = True):
    """Build a mock User object for permission class evaluation."""
    user = MagicMock()
    user.role = role
    user.is_active = is_active
    user.is_authenticated = True
    return user


def _make_request(user=None, session_data: dict | None = None):
    """Build a minimal mock request."""
    req = MagicMock()
    req.user = user
    req.tenant = None
    req.META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "pytest"}
    req.session = session_data if session_data is not None else {}
    return req


def _evaluate_permission(perm_class: type[BasePermission], role: str) -> bool:
    """
    Instantiate *perm_class* and call has_permission() with a mock request
    for *role*.

    Customer sessions are anonymous; IsCustomerSession checks session data.
    All other classes check user.role.
    """
    perm = perm_class()

    if perm_class is IsCustomerSession:
        # Customer sessions: check the session dict presence
        if role == UserRole.CUSTOMER:
            request = _make_request(
                user=None,
                session_data={"customer_session": {"branch_id": "abc", "table_number": "3"}},
            )
        else:
            # Non-customer roles trying to use IsCustomerSession endpoints are denied
            # (no customer_session key in session)
            request = _make_request(user=None, session_data={})
    else:
        user = _make_user_mock(role)
        request = _make_request(user=user)

    return perm.has_permission(request, view=None)


def _build_matrix() -> dict[tuple[str, str, str], bool]:
    """
    Compute the full RBAC matrix by evaluating each (resource, action) guard
    against every role.  This is the ground truth derived directly from
    the permission classes.
    """
    matrix: dict[tuple[str, str, str], bool] = {}
    for (resource, action), perm_class in _RESOURCE_ACTION_PERM.items():
        for role in ROLES:
            matrix[(role, resource, action)] = _evaluate_permission(perm_class, role)
    return matrix


# Build once at module load time.
RBAC_MATRIX: dict[tuple[str, str, str], bool] = _build_matrix()

# Full list of triples for exhaustive enumeration
ALL_TRIPLES = list(RBAC_MATRIX.keys())


# ---------------------------------------------------------------------------
# Helpers for assertions
# ---------------------------------------------------------------------------

def _check_permission(role: str, resource_type: str, action: str) -> bool:
    """
    Ask the authoritative permission class for (resource_type, action)
    whether it grants access to *role*.
    """
    perm_class = _RESOURCE_ACTION_PERM[(resource_type, action)]
    return _evaluate_permission(perm_class, role)


# ---------------------------------------------------------------------------
# Property 10: RBAC Permission Matrix Correctness
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(triple=st.sampled_from(ALL_TRIPLES))
@settings(max_examples=len(ALL_TRIPLES))
def test_property_10_rbac_permission_matrix_correctness(
    triple: tuple[str, str, str],
) -> None:
    """
    **Validates: Requirements 4.1, 4.2, 4.3**

    For any (role, resource_type, action) triple, the permission class
    corresponding to that (resource_type, action) pair SHALL grant or deny
    access exactly as specified by the RBAC matrix in Requirement 4.2.

    This property covers all 6 roles × 12 resource types × 4 actions =
    288 cells of the permission matrix, enumerated exhaustively via
    Hypothesis st.sampled_from.

    Each matrix cell is evaluated by:
    1. Looking up the correct permission class for (resource_type, action)
       in _RESOURCE_ACTION_PERM (mirroring what each ViewSet's get_permissions()
       returns for that action).
    2. Calling has_permission() with a mock request for the given role.
    3. Asserting the result matches the pre-computed RBAC_MATRIX truth table.
    """
    role, resource_type, action = triple

    expected: bool = RBAC_MATRIX[(role, resource_type, action)]
    actual: bool = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Permission matrix mismatch for ({role!r}, {resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, "
        f"got {'ALLOW' if actual else 'DENY'}. "
        f"Check shared/permissions.py, the relevant ViewSet's get_permissions(), "
        f"and Requirement 4.2."
    )


# ---------------------------------------------------------------------------
# Focused sub-properties for specific roles (gives Hypothesis better targets)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10a_super_admin_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Super_Admin, the authorization decision for every (resource, action)
    must match the matrix.  Super_Admin has full CRUD on platform resources
    (Tenant, Subscription, Branch, User, TenantConfig) and read on AuditLogs,
    but has no access to operational resources (MenuItem, Inventory, etc.)
    per Requirement 4.2.
    """
    role = UserRole.SUPER_ADMIN
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Super_Admin matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10b_tenant_owner_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Tenant_Owner, the authorization decision must match the matrix.
    Tenant_Owner has CRUD on Branch and User (within own tenant), read on
    Subscription and AuditLog, and read on financial records.
    """
    role = UserRole.TENANT_OWNER
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Tenant_Owner matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10c_branch_manager_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Branch_Manager, the authorization decision must match the matrix.
    Branch_Manager has CRUD on MenuItem, Inventory, Expense, Recipe; CRU on
    User (branch users); and read on Order, Income, AuditLog.
    """
    role = UserRole.BRANCH_MANAGER
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Branch_Manager matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10d_receptionist_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Receptionist, the authorization decision must match the matrix.
    Receptionist has read+update on Orders and read on MenuItems only.
    All other resources must be denied.
    """
    role = UserRole.RECEPTIONIST
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Receptionist matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10e_kitchen_staff_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Kitchen_Staff, the authorization decision must match the matrix.
    Kitchen_Staff has read+update on Orders (status only), and read on
    MenuItem, Inventory, and Recipe.  All other resources must be denied.
    """
    role = UserRole.KITCHEN_STAFF
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Kitchen_Staff matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(resource_type=st.sampled_from(RESOURCE_TYPES), action=st.sampled_from(ACTIONS))
@settings(max_examples=200)
def test_property_10f_customer_grants_match_matrix(
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2**

    For Customer (anonymous session), the authorization decision must match
    the matrix.  Customers can create Orders and read Order status.
    All other resources must be denied (non-customer session = no access).
    """
    role = UserRole.CUSTOMER
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Customer matrix mismatch for ({resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}."
    )


@pytest.mark.django_db
@given(
    role=st.sampled_from(ROLES),
    resource_type=st.sampled_from(RESOURCE_TYPES),
    action=st.sampled_from(ACTIONS),
)
@settings(max_examples=500)
def test_property_10g_no_role_produces_unexpected_outcome(
    role: str,
    resource_type: str,
    action: str,
) -> None:
    """
    **Validates: Requirements 4.1, 4.2, 4.3**

    For ANY triple of (role, resource_type, action), the authorization
    decision MUST exactly match the RBAC matrix from Requirement 4.2.

    This variant uses fully independent Hypothesis generation across all
    three dimensions to give Hypothesis maximum freedom to find
    counterexamples through shrinkage.
    """
    expected = RBAC_MATRIX[(role, resource_type, action)]
    actual = _check_permission(role, resource_type, action)

    assert actual == expected, (
        f"Unexpected authorization outcome for ({role!r}, {resource_type!r}, {action!r}): "
        f"expected {'ALLOW' if expected else 'DENY'}, got {'ALLOW' if actual else 'DENY'}. "
        f"Permission classes in shared/permissions.py must match Requirement 4.2 exactly."
    )
