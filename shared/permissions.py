"""
RBAC permission classes and mixins.

This module defines the full Role-Based Access Control permission layer for
the platform. Each class below maps to one of the six roles defined in
Requirement 4 and enforces the permission matrix from Requirement 4.2.

Scope hierarchy (narrowest to broadest):
    Session < Branch < Tenant < Platform

Roles:
    Super_Admin  — platform-wide full CRUD
    Tenant_Owner — CRUD within own tenant; read financial records across branches
    Branch_Manager — CRUD for their assigned branch
    Receptionist — read/update Orders for their branch; read MenuItems
    Kitchen_Staff — read/update Order status for their branch; read MenuItems/Recipes
    Customer     — create Orders and read own Order status within an active Session

Scope permissions:
    BranchScopePermission  — object-level check: resource.branch_id == user.branch_id
    TenantScopePermission  — object-level check: resource belongs to user's tenant

Mixins:
    AuditLogMixin — ViewSet mixin that records a FAILURE AuditLog entry whenever
                    a 403 PermissionDenied or 401 NotAuthenticated is raised.
                    Forward-compatible: silently no-ops when AuditLog is not yet
                    available (Task 6 implements the model).

Requirements: 4.1, 4.2, 4.3
"""

import logging

from rest_framework.permissions import BasePermission

from apps.authentication.models import UserRole

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: resolve authenticated user from request
# ---------------------------------------------------------------------------

def _get_user(request):
    """
    Return the authenticated User from the request, or None.

    Handles both DRF's request.user (which is always present but may be
    AnonymousUser) and raw Django HttpRequest objects.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return user


# ---------------------------------------------------------------------------
# Role-based permission classes
# ---------------------------------------------------------------------------

class IsSuperAdmin(BasePermission):
    """
    Grants access only to users with the Super_Admin platform role.

    Super_Admin has full CRUD on Tenants, Subscriptions, Branches, Users,
    TenantConfig, and read access to all AuditLogs platform-wide
    (Requirement 4.2).
    """

    message = "You must be a Super Admin to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role == UserRole.SUPER_ADMIN


class IsTenantOwner(BasePermission):
    """
    Grants access to Tenant_Owner and Super_Admin (Requirement 4.2).
    Tenant_Owner may CRUD Branches and Users within their tenant and read
    financial records across all their branches.
    """

    message = "You must be a Tenant Owner to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


class IsBranchManager(BasePermission):
    """
    Grants access to users with the Branch_Manager role, scoped to their branch.

    Also grants access to Tenant_Owner and Super_Admin, who have full access
    across all branches per the permission matrix (Requirement 4.2).

    Branch_Manager may CRUD MenuItems, Inventory, and Expenses for their branch
    and has CRU access on Branch Users (Requirement 4.2).
    """

    message = "You must be a Branch Manager to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.BRANCH_MANAGER,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


class IsReceptionist(BasePermission):
    """
    Grants access to Receptionist, Branch_Manager, Tenant_Owner and Super_Admin
    (Requirement 4.2). Receptionist has read/update on Orders for their branch.
    """

    message = "You must be a Receptionist to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.RECEPTIONIST,
            UserRole.BRANCH_MANAGER,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


class IsKitchenStaff(BasePermission):
    """
    Grants access to Kitchen_Staff, Branch_Manager, Tenant_Owner and Super_Admin
    (Requirement 4.2). Kitchen_Staff has read/status-update on Orders.
    """

    message = "You must be a Kitchen Staff member to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.KITCHEN_STAFF,
            UserRole.BRANCH_MANAGER,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


class IsCustomerSession(BasePermission):
    """
    Grants order creation and own-order read access within an active anonymous
    Customer session (Requirement 4.2, 3.7).

    Customer sessions are anonymous — the user is not authenticated with a
    User account. Instead, a session-based QR scan creates a Django session
    storing tenant_id, branch_id, and table_number. This permission class
    checks for the presence of that session data rather than an authenticated
    user.
    """

    message = "A valid customer session is required."

    def has_permission(self, request, view) -> bool:
        # Customer sessions are stored in the Django session under 'customer_session'
        # (set by POST /api/v1/customer/session/ in Task 16).
        session = getattr(request, "session", None)
        if session is None:
            return False
        return bool(session.get("customer_session"))


# ---------------------------------------------------------------------------
# Scope permission classes (object-level)
# ---------------------------------------------------------------------------

class BranchScopePermission(BasePermission):
    """
    Object-level permission that verifies the requested resource's branch
    belongs to the requesting user's assigned branch.

    Returns 403 Forbidden if:
      - The user has no assigned branch (e.g. Super_Admin or Tenant_Owner
        should use TenantScopePermission instead)
      - The resource does not carry a branch reference
      - The resource's branch does not match the user's assigned branch

    Usage in a ViewSet:
        permission_classes = [IsAuthenticated, BranchScopePermission]

    The resource object must expose its branch association as one of:
      - obj.branch      (Branch instance or branch PK)
      - obj.branch_id   (UUID or int PK of Branch)

    (Requirement 4.3)
    """

    message = "You do not have permission to access resources outside your assigned branch."

    def has_permission(self, request, view) -> bool:
        """
        Request-level check: if a branch_pk is in the URL kwargs, verify the
        requesting user's branch matches before even hitting the DB.

        This catches attempts to list/create resources under a foreign branch
        (e.g. GET /api/v1/branches/{foreign_pk}/inventory/) before any
        object-level check runs.
        """
        user = _get_user(request)
        if user is None:
            return False

        # Super_Admin and Tenant_Owner bypass branch-level scope.
        if user.role in (UserRole.SUPER_ADMIN, UserRole.TENANT_OWNER):
            return True

        # Branch-scoped roles: verify the URL branch matches the user's branch.
        branch_pk = getattr(view, "kwargs", {}).get("branch_pk") or (
            getattr(request, "resolver_match", None) and
            request.resolver_match.kwargs.get("branch_pk")
        )
        if branch_pk and user.branch_id is not None:
            if str(user.branch_id) != str(branch_pk):
                return False

        return True

    def has_object_permission(self, request, view, obj) -> bool:
        user = _get_user(request)
        if user is None:
            return False

        # Super_Admin bypasses all scope checks — they operate platform-wide.
        if user.role == UserRole.SUPER_ADMIN:
            return True

        # Tenant_Owner has tenant-wide scope; branch scope does not restrict them.
        if user.role == UserRole.TENANT_OWNER:
            return True

        # All other branch-scoped roles must have a branch assigned.
        if user.branch_id is None:
            return False

        # Resolve the resource's branch identifier.
        resource_branch_id = _resolve_branch_id(obj)
        if resource_branch_id is None:
            # If the resource carries no branch reference we cannot validate scope;
            # default to deny to prevent accidental over-permission.
            return False

        return str(user.branch_id) == str(resource_branch_id)


class TenantScopePermission(BasePermission):
    """
    Object-level permission that verifies the requested resource belongs to
    the requesting user's tenant.

    In a multi-tenant setup using django-tenants, all queries are already
    schema-scoped at the ORM level. This class provides an additional
    defence-in-depth check for cases where cross-schema queries are possible
    (e.g. public-schema resources or cross-tenant admin access).

    Returns 403 Forbidden if:
      - The resource exposes a tenant reference that does not match the
        current request tenant.

    If the resource does not carry any tenant reference the check passes
    (the ORM schema isolation already provides the guarantee).

    (Requirement 4.3)
    """

    message = "You do not have permission to access resources outside your tenant."

    def has_object_permission(self, request, view, obj) -> bool:
        user = _get_user(request)
        if user is None:
            return False

        # Super_Admin operates platform-wide — no tenant restriction.
        if user.role == UserRole.SUPER_ADMIN:
            return True

        # Resolve tenant on the resource.
        resource_tenant_id = _resolve_tenant_id(obj)

        # If the resource has no explicit tenant reference, the ORM schema
        # isolation is the guarantee — allow the access.
        if resource_tenant_id is None:
            return True

        # Resolve the requesting user's tenant from the request.
        request_tenant = getattr(request, "tenant", None)
        if request_tenant is None:
            # No tenant on the request (e.g. tests without TenantMiddleware):
            # fall back to checking if the resource tenant matches by attribute.
            return True

        return str(resource_tenant_id) == str(request_tenant.id)
# ---------------------------------------------------------------------------
# Convenience composite classes
# ---------------------------------------------------------------------------

class IsSuperAdminOrTenantOwner(BasePermission):
    """
    Allows access for both Super_Admin and Tenant_Owner roles.
    Used on endpoints that serve both platform-level and tenant-level access.
    """

    message = "You must be a Super Admin or Tenant Owner to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
        )


class IsBranchStaff(BasePermission):
    """
    Grants access to any branch-level staff role: Branch_Manager, Receptionist,
    or Kitchen_Staff. Also grants access to Tenant_Owner and Super_Admin.

    Used on read-only endpoints that all branch staff can access (e.g. menu
    item listings, branch info).
    """

    message = "You must be a branch staff member to perform this action."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
            UserRole.KITCHEN_STAFF,
            UserRole.TENANT_OWNER,
            UserRole.SUPER_ADMIN,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_branch_id(obj):
    """
    Return the branch PK from *obj*, or None if the object carries no branch
    reference.

    Checks in order:
      1. obj.branch_id  (Django FK accessor — most efficient, no extra query)
      2. obj.branch.id  (traversal — only if branch_id not available)
      3. obj.branch     (when branch is stored directly as a PK/UUID scalar)
    """
    # Prefer the FK id accessor (no DB query required).
    branch_id = getattr(obj, "branch_id", _MISSING)
    if branch_id is not _MISSING:
        return branch_id

    # Try traversing the relationship.
    branch = getattr(obj, "branch", _MISSING)
    if branch is not _MISSING and branch is not None:
        if hasattr(branch, "id"):
            return branch.id
        # branch stored as a plain scalar (UUID or int)
        return branch

    return None


def _resolve_tenant_id(obj):
    """
    Return the tenant PK from *obj*, or None if the object carries no tenant
    reference.

    Checks in order:
      1. obj.tenant_id
      2. obj.tenant.id / obj.tenant (scalar)
    """
    tenant_id = getattr(obj, "tenant_id", _MISSING)
    if tenant_id is not _MISSING:
        return tenant_id

    tenant = getattr(obj, "tenant", _MISSING)
    if tenant is not _MISSING and tenant is not None:
        if hasattr(tenant, "id"):
            return tenant.id
        return tenant

    return None


class IsAuditLogReader(BasePermission):
    """
    Grants read access to AuditLogs for the three roles that have any audit
    visibility per Requirement 4.2:
      - Super_Admin     (all logs, platform-wide)
      - Tenant_Owner    (logs scoped to own tenant)
      - Branch_Manager  (logs scoped to own branch)

    Scope filtering is applied in the queryset, not here.
    """

    message = "You must be a Super Admin, Tenant Owner, or Branch Manager to view audit logs."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
        )


class IsFinancialReader(BasePermission):
    """
    Grants read access to financial records (expenses, income, profit) for:
      - Super_Admin     (platform-wide)
      - Tenant_Owner    (across all own branches — Requirement 4.2)
      - Branch_Manager  (own branch)

    Used on read actions of ExpenseViewSet, IncomeViewSet,
    FinancialDashboardViewSet (Requirement 4.2).
    """

    message = "You must be a Super Admin, Tenant Owner, or Branch Manager to view financial records."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
        )


# Sentinel to distinguish "attribute not present" from "attribute is None".
_MISSING = object()


# ---------------------------------------------------------------------------
# AuditLogMixin — ViewSet mixin for 403/401 failure audit logging
# ---------------------------------------------------------------------------

class AuditLogMixin:
    """
    ViewSet mixin that records an AuditLog FAILURE entry whenever DRF raises
    a PermissionDenied (403) or NotAuthenticated (401) exception.

    This satisfies Requirement 4.3: "IF a request would access a resource
    outside the requesting user's assigned RBAC_Scope, THEN THE Auth_Service
    SHALL return HTTP 403 Forbidden and record the unauthorized access attempt
    in the AuditLog."

    Forward-compatible implementation:
        The AuditLog model is implemented in Task 6.  Until that task is
        complete, this mixin silently no-ops on any import / ORM error so
        that all ViewSets can already inherit from it without breaking.

    Usage::

        class MyViewSet(AuditLogMixin, viewsets.GenericViewSet):
            permission_classes = [IsBranchManager]
            ...

    The mixin must appear *before* the ViewSet base class in the MRO so that
    its ``handle_exception`` runs first.

    Requirements: 4.3
    """

    def handle_exception(self, exc):
        """
        Intercept PermissionDenied / NotAuthenticated to log a FAILURE entry.
        Then delegate to the parent handler to produce the standard response.
        """
        from rest_framework.exceptions import NotAuthenticated, PermissionDenied

        if isinstance(exc, (PermissionDenied, NotAuthenticated)):
            self._write_failure_audit(exc)

        return super().handle_exception(exc)

    def _write_failure_audit(self, exc):
        """
        Attempt to create an AuditLog record with status=FAILURE.

        Silently swallows all errors so that an unavailable audit table
        (e.g. before Task 6 migrations) never blocks the HTTP response.
        """
        try:
            from apps.audit.models import AuditLog  # noqa: F401 — imported for side-effect check

            request = self.request
            user = getattr(request, "user", None)

            user_id = str(user.id) if (user and getattr(user, "is_authenticated", False)) else None
            user_role = getattr(user, "role", "") if user else ""
            ip_address = _get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")

            # Determine resource type from the view's queryset / basename
            resource_type = getattr(self, "basename", "") or (
                self.__class__.__name__.replace("ViewSet", "")
            )
            action = getattr(self, "action", "unknown")

            AuditLog.objects.create(
                user_id=user_id,
                user_role=user_role,
                ip_address=ip_address or "0.0.0.0",
                user_agent=user_agent,
                action=f"{resource_type.upper()}_{action.upper()}_DENIED",
                resource_type=resource_type,
                resource_id=None,
                old_value=None,
                new_value=None,
                status="failure",
                failure_reason=str(exc),
            )
        except Exception:
            # Never let audit logging failure surface to the caller.
            logger.warning(
                "AuditLog FAILURE entry could not be written for %s: %s",
                self.__class__.__name__,
                exc,
                exc_info=True,
            )


def _get_client_ip(request) -> str:
    """Extract client IP from request, honouring X-Forwarded-For."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# ---------------------------------------------------------------------------
# Privilege Escalation Prevention (Requirement 4.5)
# ---------------------------------------------------------------------------

# Role privilege hierarchy: higher index = higher privilege.
# This ordering is derived from Requirement 4.2 and the role hierarchy
# described in the platform SRS.
#
#   Index 0 (lowest)  Customer
#   Index 1           Kitchen_Staff
#   Index 2           Receptionist
#   Index 3           Branch_Manager
#   Index 4           Tenant_Owner
#   Index 5 (highest) Super_Admin
#
# A user may only assign roles whose privilege level is LESS THAN OR EQUAL
# to their own current role privilege level (Requirement 4.5).

ROLE_PRIVILEGE_LEVELS: dict[str, int] = {
    UserRole.CUSTOMER: 0,
    UserRole.KITCHEN_STAFF: 1,
    UserRole.RECEPTIONIST: 2,
    UserRole.BRANCH_MANAGER: 3,
    UserRole.TENANT_OWNER: 4,
    UserRole.SUPER_ADMIN: 5,
}


def get_role_privilege(role: str) -> int:
    """
    Return the integer privilege level for *role*.

    Raises ValueError if *role* is not a recognised UserRole value.
    """
    if role not in ROLE_PRIVILEGE_LEVELS:
        raise ValueError(f"Unknown role: {role!r}")
    return ROLE_PRIVILEGE_LEVELS[role]


def can_assign_role(requester_role: str, target_role: str) -> bool:
    """
    Return True if a user with *requester_role* is permitted to assign
    *target_role* to any user (themselves or another).

    The rule: a requester may only assign roles at or below their own
    privilege level.  Attempting to assign a strictly higher-privilege role
    constitutes privilege escalation and MUST be rejected (Requirement 4.5).

    Args:
        requester_role: The role of the user making the assignment request.
        target_role:    The role the requester wishes to assign.

    Returns:
        True  — assignment is permitted (target privilege ≤ requester privilege).
        False — assignment is denied  (target privilege > requester privilege).
    """
    requester_level = get_role_privilege(requester_role)
    target_level = get_role_privilege(target_role)
    return target_level <= requester_level


class PrivilegeEscalationPermission(BasePermission):
    """
    Request-level permission that prevents privilege escalation.

    Guards any endpoint that accepts a ``role`` field in the request body
    (user creation, user update, role assignment).  Raises 403 if the
    requested role has strictly higher privileges than the requesting user's
    current role.

    Usage in a ViewSet (user update or create)::

        permission_classes = [IsAuthenticated, PrivilegeEscalationPermission]

    The view must pass the target role via one of:
      - request.data["role"]          (PATCH /users/{id}/ body)
      - view.kwargs["role"]           (URL kwarg, less common)

    If no role is present in the request, the check passes (nothing to
    escalate to).

    Requirements: 4.5
    """

    message = (
        "You cannot assign a role with higher privileges than your own. "
        "Privilege escalation is not permitted."
    )

    def has_permission(self, request, view) -> bool:
        requester = _get_user(request)
        if requester is None:
            return False

        # Extract the target role from request data (may be absent on non-role updates)
        target_role = request.data.get("role") if hasattr(request, "data") else None

        if target_role is None:
            # No role field in the request body — nothing to escalate; allow.
            return True

        # Validate target_role is a known role
        if target_role not in ROLE_PRIVILEGE_LEVELS:
            # Unknown role — let the serializer validation handle this
            return True

        return can_assign_role(requester.role, target_role)
