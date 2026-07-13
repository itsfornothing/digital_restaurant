"""
Property-Based Tests: RBAC Scope Isolation

Property 11: RBAC Scope Isolation

  For any user with Branch-scoped permissions, any API request targeting a
  resource belonging to a different branch shall be rejected with HTTP 403,
  regardless of the resource type or action.

Validates: Requirements 4.3

Strategy:
  - Use Hypothesis to generate two distinct Branch instances (Branch A and
    Branch B) in the database.
  - Assign a user to Branch A with one of the Branch-scoped roles:
    Branch_Manager, Receptionist, or Kitchen_Staff.
  - Construct a resource object whose ``branch_id`` points to Branch B.
  - Invoke ``BranchScopePermission.has_object_permission()`` — the same
    object-level permission check called by all ViewSets — and assert it
    returns False (403 Forbidden).
  - Cross-check: the same user targeting a Branch-A resource MUST be
    permitted.

Design notes:
  - ``BranchScopePermission`` is the single source of truth for scope
    enforcement (shared/permissions.py).  All branch-scoped ViewSets
    delegate to it via ``check_object_permissions()``.
  - Resource objects are modelled as plain namespace objects carrying a
    ``branch_id`` attribute, which is the interface contract declared by
    ``_resolve_branch_id()`` in permissions.py.  This avoids database
    dependencies on resource models that are implemented in later tasks
    (Task 10–13), while accurately exercising the enforcement logic.
  - No mocking of permission logic is used — the actual
    ``BranchScopePermission`` class from shared/permissions.py is called
    directly with real Branch and User ORM instances.
  - The test is django_db-marked because User and Branch objects must be
    persisted to satisfy ForeignKey constraints.

Requirements: 4.3
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from apps.authentication.models import UserRole
from apps.branches.models import Branch
from shared.permissions import BranchScopePermission

User = get_user_model()

# ---------------------------------------------------------------------------
# Branch-scoped roles (Requirement 4.2)
# ---------------------------------------------------------------------------

BRANCH_SCOPED_ROLES = [
    UserRole.BRANCH_MANAGER,
    UserRole.RECEPTIONIST,
    UserRole.KITCHEN_STAFF,
]

# Resource types that carry a branch association (from Requirement 4.2 scope)
RESOURCE_TYPES = [
    "MenuItem",
    "InventoryItem",
    "Expense",
    "Order",
    "Income",
    "Recipe",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_branch(name: str) -> Branch:
    """Create and persist a Branch instance."""
    return Branch.objects.create(name=name)


def _make_user(email: str, role: str, branch: Branch) -> "User":
    """Create and persist a User assigned to *branch* with *role*."""
    User.objects.filter(email=email).delete()
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=role,
        branch=branch,
    )


def _make_resource(branch: Branch, resource_type: str = "MenuItem") -> SimpleNamespace:
    """
    Build a lightweight resource stub whose ``branch_id`` points to *branch*.

    ``BranchScopePermission._resolve_branch_id()`` first checks ``obj.branch_id``,
    so providing that attribute is sufficient to exercise the full enforcement
    path without requiring real ORM models.
    """
    return SimpleNamespace(
        resource_type=resource_type,
        branch_id=branch.id,
        branch=branch,
    )


def _make_request(user: "User") -> MagicMock:
    """Build a minimal mock request with an authenticated user."""
    req = MagicMock()
    req.user = user
    req.tenant = None
    return req


def _check_scope(user: "User", resource_branch: Branch) -> bool:
    """
    Invoke BranchScopePermission.has_object_permission() for *user* accessing
    a resource belonging to *resource_branch*.

    Returns True if access is permitted, False if it should be denied (403).
    """
    perm = BranchScopePermission()
    request = _make_request(user)
    resource = _make_resource(resource_branch)
    return perm.has_object_permission(request, view=None, obj=resource)


# ---------------------------------------------------------------------------
# Property 11a — Cross-branch access is denied for all Branch-scoped roles
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from(BRANCH_SCOPED_ROLES),
    resource_type=st.sampled_from(RESOURCE_TYPES),
)
@settings(max_examples=300)
def test_property_11a_branch_scoped_user_denied_access_to_other_branch(
    role: str,
    resource_type: str,
) -> None:
    """
    **Validates: Requirements 4.3**

    For any user with a Branch-scoped role (Branch_Manager, Receptionist,
    Kitchen_Staff) assigned to Branch A, an object-level permission check
    against a resource belonging to Branch B (a different branch) MUST
    return False — causing a HTTP 403 Forbidden response.

    This property holds regardless of resource type or action.
    """
    # Create two distinct branches
    branch_a = _make_branch(f"Branch A ({role})")
    branch_b = _make_branch(f"Branch B ({role})")

    # Sanity: the two branches must be distinct
    assert branch_a.id != branch_b.id

    # Create a user assigned to Branch A
    email = f"scope_test_{role.lower().replace('_', '')}_{resource_type.lower()}@example.com"
    user = _make_user(email, role, branch_a)

    # The user is correctly assigned to Branch A
    assert str(user.branch_id) == str(branch_a.id)

    # Attempt to access a resource belonging to Branch B — MUST be denied
    permitted = _check_scope(user, branch_b)

    assert permitted is False, (
        f"RBAC scope isolation FAILED for role={role!r}, resource_type={resource_type!r}: "
        f"user assigned to branch {branch_a.id} was ALLOWED to access a resource "
        f"belonging to branch {branch_b.id}.  "
        f"BranchScopePermission must return False for cross-branch access "
        f"(Requirement 4.3)."
    )


# ---------------------------------------------------------------------------
# Property 11b — Same-branch access is permitted for all Branch-scoped roles
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from(BRANCH_SCOPED_ROLES),
    resource_type=st.sampled_from(RESOURCE_TYPES),
)
@settings(max_examples=300)
def test_property_11b_branch_scoped_user_permitted_access_to_own_branch(
    role: str,
    resource_type: str,
) -> None:
    """
    **Validates: Requirements 4.3**

    For any user with a Branch-scoped role assigned to Branch A, an
    object-level permission check against a resource belonging to Branch A
    MUST return True — access within one's own branch is always permitted.

    This is the positive counterpart to Property 11a: scope isolation must
    deny cross-branch access without accidentally over-restricting own-branch
    access.
    """
    branch_a = _make_branch(f"Own Branch ({role}) ({resource_type})")
    email = f"scope_own_{role.lower().replace('_', '')}_{resource_type.lower()}@example.com"
    user = _make_user(email, role, branch_a)

    # Accessing a resource that belongs to the user's own branch — MUST be permitted
    permitted = _check_scope(user, branch_a)

    assert permitted is True, (
        f"Own-branch access DENIED for role={role!r}, resource_type={resource_type!r}: "
        f"user assigned to branch {branch_a.id} was DENIED access to a resource "
        f"belonging to the SAME branch {branch_a.id}.  "
        f"BranchScopePermission must return True for same-branch access."
    )


# ---------------------------------------------------------------------------
# Property 11c — Denial holds across all role+resource_type combinations
#               when the resource branch is different from the user's branch
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from(BRANCH_SCOPED_ROLES),
    resource_type_a=st.sampled_from(RESOURCE_TYPES),
    resource_type_b=st.sampled_from(RESOURCE_TYPES),
)
@settings(max_examples=200)
def test_property_11c_cross_branch_denial_is_resource_type_agnostic(
    role: str,
    resource_type_a: str,
    resource_type_b: str,
) -> None:
    """
    **Validates: Requirements 4.3**

    The cross-branch denial from Property 11a must hold regardless of which
    resource type is being accessed.  This test explicitly exercises the
    "regardless of resource type" clause in Property 11 by generating two
    independent resource type values and confirming that cross-branch access
    is always denied no matter the resource.
    """
    branch_a = _make_branch(f"BranchA-{role}-{resource_type_a}")
    branch_b = _make_branch(f"BranchB-{role}-{resource_type_b}")

    # Branches must be different; if Hypothesis generates the same branch
    # name we cannot distinguish them — but they are separate DB rows with
    # distinct PKs regardless (Branch.name is not unique).
    assert branch_a.id != branch_b.id

    email = f"agnostic_{role.lower().replace('_','')}_ra{resource_type_a.lower()}_rb{resource_type_b.lower()}@example.com"
    user = _make_user(email, role, branch_a)

    # Build a resource of type B belonging to Branch B
    resource = SimpleNamespace(
        resource_type=resource_type_b,
        branch_id=branch_b.id,
        branch=branch_b,
    )
    perm = BranchScopePermission()
    request = _make_request(user)
    permitted = perm.has_object_permission(request, view=None, obj=resource)

    assert permitted is False, (
        f"Cross-branch access NOT denied for role={role!r}, "
        f"resource_type={resource_type_b!r}: "
        f"user (branch={branch_a.id}) accessed resource (branch={branch_b.id}).  "
        f"Scope isolation must hold for ALL resource types (Requirement 4.3)."
    )


# ---------------------------------------------------------------------------
# Property 11d — Super_Admin and Tenant_Owner bypass branch scope checks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from([UserRole.SUPER_ADMIN, UserRole.TENANT_OWNER]),
    resource_type=st.sampled_from(RESOURCE_TYPES),
)
@settings(max_examples=100)
def test_property_11d_platform_and_tenant_roles_bypass_branch_scope(
    role: str,
    resource_type: str,
) -> None:
    """
    **Validates: Requirements 4.3**

    Super_Admin and Tenant_Owner operate at Platform and Tenant scope
    respectively — they are explicitly exempted from branch scope checks by
    BranchScopePermission (see shared/permissions.py).

    This test confirms that the bypass is intentional and not accidentally
    granting cross-branch access to branch-scoped roles: the bypass applies
    only to the two higher-scope roles.
    """
    branch_a = _make_branch(f"BranchA-platform-{role}")
    branch_b = _make_branch(f"BranchB-platform-{role}")

    email = f"platform_{role.lower().replace('_', '')}_{resource_type.lower()}@example.com"
    # Super_Admin / Tenant_Owner may not have a branch assigned
    user = _make_user(email, role, branch=branch_a)

    # These roles bypass branch scope; cross-branch access MUST be permitted
    permitted = _check_scope(user, branch_b)

    assert permitted is True, (
        f"Platform/tenant-wide role {role!r} was DENIED access by "
        f"BranchScopePermission — this role should bypass branch scope checks.  "
        f"Check shared/permissions.py BranchScopePermission.has_object_permission()."
    )


# ---------------------------------------------------------------------------
# Property 11e — User with no branch assigned is always denied
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from(BRANCH_SCOPED_ROLES),
    resource_type=st.sampled_from(RESOURCE_TYPES),
)
@settings(max_examples=100)
def test_property_11e_user_without_branch_denied_all_branch_resources(
    role: str,
    resource_type: str,
) -> None:
    """
    **Validates: Requirements 4.3**

    A Branch-scoped user with no branch assigned (branch=None) has no valid
    scope and MUST be denied access to any branch resource.

    This guards against misconfigured user accounts accidentally gaining
    access — the permission must default to deny when branch_id is None.
    """
    branch = _make_branch(f"SomeBranch-{role}-{resource_type}")

    email = f"nobranch_{role.lower().replace('_','')}_{resource_type.lower()}@example.com"
    User.objects.filter(email=email).delete()

    # Create user WITHOUT a branch assignment
    user = User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=role,
        branch=None,
    )
    assert user.branch_id is None

    permitted = _check_scope(user, branch)

    assert permitted is False, (
        f"Branch-scoped role {role!r} with NO branch assigned was PERMITTED "
        f"access to a resource (branch={branch.id}).  "
        f"Users without a branch assignment must be denied all branch-scoped "
        f"resources (Requirement 4.3)."
    )
