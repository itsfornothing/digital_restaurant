"""
Property-Based Tests: Privilege Escalation Prevention

Property 12: Privilege Escalation Prevention

  For any user, any attempt to assign themselves or another user a role
  whose permission set is a strict superset of the requester's current role
  shall fail with HTTP 403.

  Equivalently: a user may only assign roles at or below their own privilege
  level; attempting to assign a strictly higher-privilege role is denied.

Validates: Requirements 4.5

Strategy:
  - Define the canonical role privilege hierarchy (lowest to highest):
      Customer < Kitchen_Staff < Receptionist < Branch_Manager <
      Tenant_Owner < Super_Admin
  - Use Hypothesis ``st.sampled_from`` to generate all (current_role,
    target_role) pairs for both self-promotion and promoting-another scenarios.
  - For each pair where target_role has strictly higher privileges than
    current_role, assert that ``can_assign_role()`` returns False and that
    ``PrivilegeEscalationPermission.has_permission()`` returns False — which
    maps to HTTP 403 at the ViewSet level.
  - For each pair where target_role has equal or lower privileges, assert
    the inverse: the assignment is permitted.
  - Additionally, generate arbitrary (current_role, target_role) pairs via
    independent sampling to allow Hypothesis to shrink edge cases freely.

Design notes:
  - ``can_assign_role()`` and ``PrivilegeEscalationPermission`` in
    shared/permissions.py are the single source of truth for privilege
    escalation checks (Requirement 4.5).
  - No database access is needed for the core permission logic: the
    privilege level comparison is a pure function.  The ``@pytest.mark.django_db``
    marker is only applied to tests that create real User instances to verify
    the end-to-end integration.
  - No mocking of the permission logic is used — actual classes from
    shared/permissions.py are exercised directly.

Requirements: 4.5
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.authentication.models import UserRole
from shared.permissions import (
    ROLE_PRIVILEGE_LEVELS,
    PrivilegeEscalationPermission,
    can_assign_role,
    get_role_privilege,
)

# ---------------------------------------------------------------------------
# All defined roles, ordered by ascending privilege for reference
# ---------------------------------------------------------------------------

ALL_ROLES = [
    UserRole.CUSTOMER,
    UserRole.KITCHEN_STAFF,
    UserRole.RECEPTIONIST,
    UserRole.BRANCH_MANAGER,
    UserRole.TENANT_OWNER,
    UserRole.SUPER_ADMIN,
]

# All (current_role, target_role) pairs — 36 combinations
ALL_ROLE_PAIRS = [(r1, r2) for r1 in ALL_ROLES for r2 in ALL_ROLES]

# Pairs where escalation SHOULD be denied (target > current)
ESCALATION_PAIRS = [
    (current, target)
    for current, target in ALL_ROLE_PAIRS
    if ROLE_PRIVILEGE_LEVELS[target] > ROLE_PRIVILEGE_LEVELS[current]
]

# Pairs where assignment SHOULD be permitted (target <= current)
PERMITTED_PAIRS = [
    (current, target)
    for current, target in ALL_ROLE_PAIRS
    if ROLE_PRIVILEGE_LEVELS[target] <= ROLE_PRIVILEGE_LEVELS[current]
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user_mock(role: str, is_active: bool = True) -> MagicMock:
    """Build a lightweight mock User for permission-class evaluation."""
    user = MagicMock()
    user.role = role
    user.is_active = is_active
    user.is_authenticated = True
    return user


def _make_request(role: str, target_role: str) -> MagicMock:
    """
    Build a mock request whose authenticated user has *role* and whose
    body requests assignment of *target_role*.
    """
    req = MagicMock()
    req.user = _make_user_mock(role)
    req.data = {"role": target_role}
    req.META = {"REMOTE_ADDR": "127.0.0.1"}
    return req


def _check_escalation_permission(requester_role: str, target_role: str) -> bool:
    """
    Invoke PrivilegeEscalationPermission.has_permission() with a mock
    request for the given (requester_role, target_role) pair.

    Returns True if the assignment is permitted, False if it should be
    blocked (403).
    """
    perm = PrivilegeEscalationPermission()
    request = _make_request(requester_role, target_role)
    return perm.has_permission(request, view=None)


# ---------------------------------------------------------------------------
# Property 12a — Escalation pairs are always denied
#
# For any pair where target_role has strictly higher privilege than the
# current_role, both can_assign_role() and PrivilegeEscalationPermission
# must return False (deny / 403).
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(pair=st.sampled_from(ESCALATION_PAIRS))
@settings(max_examples=200)
def test_property_12a_privilege_escalation_is_denied(
    pair: tuple[str, str],
) -> None:
    """
    **Validates: Requirements 4.5**

    For any (current_role, target_role) pair where target_role has strictly
    higher privileges than current_role, the role assignment SHALL be denied.

    This covers both self-promotion (requester changes their own role) and
    the attempt to promote another user to a higher-privilege role — in both
    cases the requester's current role is compared against the target role.

    Specifically:
      - can_assign_role(current_role, target_role) must return False
      - PrivilegeEscalationPermission.has_permission() must return False (→ HTTP 403)
    """
    current_role, target_role = pair

    current_level = get_role_privilege(current_role)
    target_level = get_role_privilege(target_role)

    # Pre-condition: this is a genuine escalation attempt
    assert target_level > current_level, (
        f"Test setup error: ({current_role!r}, {target_role!r}) is not an "
        f"escalation pair (current={current_level}, target={target_level})."
    )

    # can_assign_role() must deny
    assert can_assign_role(current_role, target_role) is False, (
        f"can_assign_role({current_role!r}, {target_role!r}) returned True — "
        f"privilege escalation was NOT prevented. "
        f"current_level={current_level}, target_level={target_level}. "
        f"Requirement 4.5: users must not be able to escalate beyond their "
        f"current role."
    )

    # PrivilegeEscalationPermission must also deny (→ HTTP 403)
    permitted = _check_escalation_permission(current_role, target_role)
    assert permitted is False, (
        f"PrivilegeEscalationPermission permitted escalation from "
        f"{current_role!r} (level {current_level}) to "
        f"{target_role!r} (level {target_level}). "
        f"Expected HTTP 403 (denied). Requirement 4.5."
    )


# ---------------------------------------------------------------------------
# Property 12b — Same-level and downward assignments are permitted
#
# For any pair where target_role has equal or lower privilege, the assignment
# must succeed.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(pair=st.sampled_from(PERMITTED_PAIRS))
@settings(max_examples=200)
def test_property_12b_non_escalation_assignments_are_permitted(
    pair: tuple[str, str],
) -> None:
    """
    **Validates: Requirements 4.5**

    For any (current_role, target_role) pair where target_role has equal or
    lower privilege than current_role, the assignment SHALL be permitted.

    This is the positive counterpart: privilege escalation prevention must
    not accidentally block legitimate assignments (same-level or demotion).

    Specifically:
      - can_assign_role(current_role, target_role) must return True
      - PrivilegeEscalationPermission.has_permission() must return True
    """
    current_role, target_role = pair

    current_level = get_role_privilege(current_role)
    target_level = get_role_privilege(target_role)

    # Pre-condition: target is not a higher-privilege escalation
    assert target_level <= current_level, (
        f"Test setup error: ({current_role!r}, {target_role!r}) is an "
        f"escalation pair, not a permitted one."
    )

    # can_assign_role() must permit
    assert can_assign_role(current_role, target_role) is True, (
        f"can_assign_role({current_role!r}, {target_role!r}) returned False — "
        f"a legitimate same-level or downward role assignment was incorrectly "
        f"blocked. current_level={current_level}, target_level={target_level}. "
        f"Non-escalation assignments must be permitted (Requirement 4.5)."
    )

    # PrivilegeEscalationPermission must also permit
    permitted = _check_escalation_permission(current_role, target_role)
    assert permitted is True, (
        f"PrivilegeEscalationPermission denied a non-escalation assignment "
        f"from {current_role!r} (level {current_level}) to "
        f"{target_role!r} (level {target_level}). "
        f"Expected: permitted. Requirement 4.5."
    )


# ---------------------------------------------------------------------------
# Property 12c — Exhaustive free-form generation across all role pairs
#
# Uses independent Hypothesis generation to give the library maximum freedom
# to explore the (current_role, target_role) space and discover shrinkable
# counterexamples.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    current_role=st.sampled_from(ALL_ROLES),
    target_role=st.sampled_from(ALL_ROLES),
)
@settings(max_examples=200)
def test_property_12c_escalation_rule_holds_for_arbitrary_role_pairs(
    current_role: str,
    target_role: str,
) -> None:
    """
    **Validates: Requirements 4.5**

    For any arbitrary (current_role, target_role) pair generated by
    Hypothesis, the privilege escalation prevention rule must hold:

      - If target_level > current_level → assignment is DENIED (403)
      - If target_level <= current_level → assignment is PERMITTED

    Both can_assign_role() and PrivilegeEscalationPermission are checked
    consistently to ensure the two implementations agree.

    This test uses fully independent sampling to allow Hypothesis maximum
    freedom to find counterexamples via shrinkage.
    """
    current_level = get_role_privilege(current_role)
    target_level = get_role_privilege(target_role)
    is_escalation = target_level > current_level

    # Assert can_assign_role() is consistent with the privilege levels
    result = can_assign_role(current_role, target_role)

    if is_escalation:
        assert result is False, (
            f"can_assign_role({current_role!r}, {target_role!r}) returned True "
            f"for an escalation attempt. "
            f"current_level={current_level}, target_level={target_level}. "
            f"Requirement 4.5 violated."
        )
    else:
        assert result is True, (
            f"can_assign_role({current_role!r}, {target_role!r}) returned False "
            f"for a non-escalation assignment. "
            f"current_level={current_level}, target_level={target_level}. "
            f"Requirement 4.5: non-escalation assignments must succeed."
        )

    # Assert PrivilegeEscalationPermission is consistent with can_assign_role()
    permitted = _check_escalation_permission(current_role, target_role)
    assert permitted == result, (
        f"PrivilegeEscalationPermission and can_assign_role() disagree for "
        f"({current_role!r}, {target_role!r}): "
        f"can_assign_role={result}, has_permission={permitted}. "
        f"The permission class must implement the same logic as can_assign_role()."
    )


# ---------------------------------------------------------------------------
# Property 12d — Self-promotion is denied for all roles except Super_Admin
#
# A user cannot promote themselves to any strictly higher-privilege role.
# Only Super_Admin (highest privilege) can never escalate because there is
# no role above them.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    current_role=st.sampled_from(ALL_ROLES),
    target_role=st.sampled_from(ALL_ROLES),
)
@settings(max_examples=200)
def test_property_12d_self_promotion_denied_for_higher_roles(
    current_role: str,
    target_role: str,
) -> None:
    """
    **Validates: Requirements 4.5**

    Self-promotion — a user attempting to change their own role to a
    strictly higher-privilege role — must be denied.

    The same PrivilegeEscalationPermission gate applies whether the
    requester is changing their own role or another user's role: the
    check compares the requester's current privilege against the requested
    target privilege.

    This test explicitly models the self-promotion case by confirming that
    any attempt by a user to self-assign a role with strictly greater
    privilege is denied.
    """
    current_level = get_role_privilege(current_role)
    target_level = get_role_privilege(target_role)

    is_self_escalation = target_level > current_level

    # Use the same permission infrastructure regardless of self vs. other
    # (the permission class compares requester's role, not the subject's role)
    permitted = _check_escalation_permission(current_role, target_role)

    if is_self_escalation:
        assert permitted is False, (
            f"Self-promotion from {current_role!r} (level {current_level}) "
            f"to {target_role!r} (level {target_level}) was permitted. "
            f"Requirement 4.5: users must not be able to self-escalate."
        )
    else:
        assert permitted is True, (
            f"Non-escalating self-assignment from {current_role!r} "
            f"(level {current_level}) to {target_role!r} (level {target_level}) "
            f"was incorrectly denied."
        )


# ---------------------------------------------------------------------------
# Property 12e — Privilege ordering is total and consistent
#
# The ROLE_PRIVILEGE_LEVELS mapping defines a total order on roles:
#   for any two roles A and B, exactly one of the following holds:
#   level(A) < level(B), level(A) == level(B), or level(A) > level(B).
#
# This property confirms the ordering is sane: can_assign_role is
# consistent with the levels, and the levels respect transitivity.
# ---------------------------------------------------------------------------

@given(
    role_a=st.sampled_from(ALL_ROLES),
    role_b=st.sampled_from(ALL_ROLES),
    role_c=st.sampled_from(ALL_ROLES),
)
@settings(max_examples=200)
def test_property_12e_privilege_ordering_is_transitive(
    role_a: str,
    role_b: str,
    role_c: str,
) -> None:
    """
    **Validates: Requirements 4.5**

    The privilege hierarchy must be a consistent total order.

    Transitivity: if level(A) <= level(B) and level(B) <= level(C), then
    level(A) <= level(C).

    This ensures there are no cycles in the privilege graph that could allow
    a chain of legitimate-looking assignments to produce net escalation.
    """
    level_a = get_role_privilege(role_a)
    level_b = get_role_privilege(role_b)
    level_c = get_role_privilege(role_c)

    # Transitivity: A≤B and B≤C → A≤C
    if level_a <= level_b and level_b <= level_c:
        assert level_a <= level_c, (
            f"Transitivity violated: level({role_a!r})={level_a} ≤ "
            f"level({role_b!r})={level_b} ≤ level({role_c!r})={level_c} "
            f"but level({role_a!r}) > level({role_c!r}). "
            f"The privilege hierarchy must be a total order."
        )

    # can_assign_role transitivity: if A can assign B, and B can assign C,
    # then A can assign C (no transitivity loophole).
    if can_assign_role(role_a, role_b) and can_assign_role(role_b, role_c):
        assert can_assign_role(role_a, role_c), (
            f"Privilege transitivity loophole: {role_a!r} can assign {role_b!r}, "
            f"and {role_b!r} can assign {role_c!r}, but {role_a!r} cannot directly "
            f"assign {role_c!r}. This is inconsistent (Requirement 4.5)."
        )


# ---------------------------------------------------------------------------
# Property 12f — All roles are covered by the privilege mapping
#
# Every value in UserRole must appear in ROLE_PRIVILEGE_LEVELS with a unique
# integer level.  Missing or duplicate levels would create gaps or ambiguity
# in escalation detection.
# ---------------------------------------------------------------------------

def test_property_12f_all_roles_have_unique_privilege_levels() -> None:
    """
    **Validates: Requirements 4.5**

    Every UserRole value must be present in ROLE_PRIVILEGE_LEVELS, and each
    role must have a unique integer privilege level.

    This ensures:
      (a) No role is accidentally omitted from escalation checks.
      (b) There are no ties that could allow unexpected assignments.
    """
    role_values = [choice[0] for choice in UserRole.choices]

    for role in role_values:
        assert role in ROLE_PRIVILEGE_LEVELS, (
            f"Role {role!r} is defined in UserRole.choices but is missing from "
            f"ROLE_PRIVILEGE_LEVELS in shared/permissions.py.  "
            f"All roles must have a privilege level for escalation checks."
        )

    # Levels must be unique (no two roles share the same level)
    levels = [ROLE_PRIVILEGE_LEVELS[r] for r in role_values]
    assert len(levels) == len(set(levels)), (
        f"Duplicate privilege levels detected in ROLE_PRIVILEGE_LEVELS: "
        f"{sorted(zip(levels, role_values))}. "
        f"Each role must have a distinct privilege level."
    )
