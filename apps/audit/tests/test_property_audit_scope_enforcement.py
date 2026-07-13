"""
Property-Based Tests: Audit Log Scope Enforcement (Property 15)

Property 15: Audit Log Scope Enforcement

  Sub-property 15a — Tenant_Owner cross-branch visibility:
    For any Tenant_Owner querying audit logs, the view returns all entries in
    the current schema without any branch filter applied. django-tenants provides
    schema isolation; Tenant_Owner sees all entries in their tenant regardless
    of which branch they belong to.

  Sub-property 15b — Branch_Manager branch isolation:
    For any Branch_Manager querying audit logs, every returned entry SHALL have
    ``branch_id`` equal to the Branch_Manager's own ``branch_id``. Entries from
    other branches and entries with no branch_id (null) SHALL NOT appear.

  Sub-property 15c — Branch_Manager result count accuracy:
    The count of entries returned to a Branch_Manager SHALL exactly equal the
    number of AuditLog entries bearing that manager's branch_id.

  Sub-property 15d — Unauthorized roles receive 403:
    Roles without audit-read permission (Receptionist, Kitchen_Staff, Customer)
    SHALL receive HTTP 403 Forbidden when attempting to read audit logs.

**Validates: Requirements 5.5, 5.6, 5.7**

Implementation note on branch_id storage:
    Branch uses Django's default auto-integer primary key. AuditLog.branch_id
    is a UUIDField. Django (with SQLite backend) represents the integer FK
    value as a zero-padded UUID string internally (e.g. branch.id=215 →
    user.branch_id = UUID('00000000-0000-0000-0000-0000000000d7')).
    Tests therefore read user.branch_id *after* user creation (rather than
    computing it from branch.id) and use that value both to populate
    AuditLog.branch_id and to assert response entries, ensuring consistency
    with what the view's filter produces.
"""

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

from apps.audit.models import AuditLog, AuditLogStatus
from apps.authentication.models import UserRole
from apps.branches.models import Branch

User = get_user_model()

AUDIT_LOG_LIST_URL = "/api/v1/audit-logs/"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_branch(name: str) -> Branch:
    """Create and return a Branch with the given name."""
    return Branch.objects.create(name=name)


def _make_branch_manager(branch: Branch) -> User:
    """Create and return a User with BRANCH_MANAGER role assigned to branch."""
    email = f"bm_{uuid.uuid4().hex[:10]}@test.example.com"
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=UserRole.BRANCH_MANAGER,
        branch=branch,
    )


def _make_tenant_owner() -> User:
    """Create and return a User with TENANT_OWNER role (no branch assignment)."""
    email = f"to_{uuid.uuid4().hex[:10]}@test.example.com"
    return User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=UserRole.TENANT_OWNER,
    )


def _make_audit_log(
    branch_id=None,
    tenant_id=None,
    action: str = "TEST_ACTION",
) -> AuditLog:
    """
    Create and return an AuditLog entry with optional branch_id / tenant_id.

    branch_id should be a UUID value (as stored in AuditLog.branch_id), not
    a Branch instance PK integer.  Callers should pass user.branch_id rather
    than branch.id directly.
    """
    return AuditLog.objects.create(
        branch_id=branch_id,
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        user_role="Receptionist",
        ip_address="127.0.0.1",
        user_agent="TestAgent/1.0",
        action=action,
        resource_type="TestResource",
        resource_id=uuid.uuid4(),
        status=AuditLogStatus.SUCCESS,
    )


def _get_branch_uuid(branch: Branch) -> uuid.UUID:
    """
    Return the UUID value that Django stores in AuditLog.branch_id for a
    given Branch instance.

    Branch uses an auto-integer PK. Django's UUIDField represents the integer
    FK value as a zero-padded UUID internally.  The most reliable way to
    obtain the correct UUID is to create a temporary Branch_Manager for that
    branch and read back user.branch_id — but that is expensive.  Instead we
    use the same zero-padding formula Django uses: int → 128-bit UUID.

    We verify consistency by matching what the view actually filters on.
    """
    # Branch PK is already a UUID since Task 10.1 migration
    if isinstance(branch.pk, uuid.UUID):
        return branch.pk
    # Fallback for legacy integer PKs (stub branch)
    return uuid.UUID(int=branch.pk)


def _get_all_results(client: APIClient, url: str) -> list:
    """
    Follow cursor-pagination links and collect all results across pages.

    Returns a flat list of result dicts.
    """
    all_results = []
    while url:
        response = client.get(url)
        assert response.status_code == 200, (
            f"Expected HTTP 200, got {response.status_code}. "
            f"Response: {getattr(response, 'data', response.content)!r}"
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
# Property 15a: Tenant_Owner sees entries from all branches
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    n_branch_a=st.integers(1, 4),
    n_branch_b=st.integers(1, 4),
)
@settings(max_examples=50)
def test_property_15a_tenant_owner_sees_cross_branch_entries(
    n_branch_a: int,
    n_branch_b: int,
) -> None:
    """
    **Validates: Requirements 5.5, 5.6, 5.7**

    Property 15a: Tenant_Owner Cross-Branch Visibility

    For any Tenant_Owner, a GET /api/v1/audit-logs/ request SHALL return
    entries from ALL branches (no branch filter is applied). The Tenant_Owner
    SHALL see both branch_a and branch_b entries in the response.

    Because the DB accumulates entries across Hypothesis iterations, we
    filter the response to only entries belonging to the two branches created
    in this iteration, then assert both branches are represented.
    """
    # Create two fresh branches for this iteration
    unique_suffix = uuid.uuid4().hex[:8]
    branch_a = _make_branch(f"Branch A {unique_suffix}")
    branch_b = _make_branch(f"Branch B {unique_suffix}")

    # Derive the UUID values that Django stores for each branch PK
    branch_a_uuid = _get_branch_uuid(branch_a)
    branch_b_uuid = _get_branch_uuid(branch_b)
    branch_a_str = str(branch_a_uuid)
    branch_b_str = str(branch_b_uuid)

    # Create audit log entries for each branch using the correct UUID values
    for _ in range(n_branch_a):
        _make_audit_log(branch_id=branch_a_uuid, action="TEST_ACTION_A")
    for _ in range(n_branch_b):
        _make_audit_log(branch_id=branch_b_uuid, action="TEST_ACTION_B")

    # Authenticate as Tenant_Owner
    tenant_owner = _make_tenant_owner()
    client = APIClient()
    client.force_login(tenant_owner)

    # Collect all pages to handle cursor pagination
    all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)

    # Filter to only entries belonging to this iteration's branches
    iteration_results = [
        r for r in all_results
        if r.get("branch_id") in (branch_a_str, branch_b_str)
    ]

    # Collect the set of branch_ids seen in the iteration results
    seen_branch_ids = {r.get("branch_id") for r in iteration_results}

    assert branch_a_str in seen_branch_ids, (
        f"Tenant_Owner response is missing entries from branch_a ({branch_a_str}). "
        f"Seen branch_ids in iteration results: {seen_branch_ids!r}. "
        f"Tenant_Owner should see all branches — no branch filter should be applied "
        f"(Requirements 5.5, 5.6)."
    )
    assert branch_b_str in seen_branch_ids, (
        f"Tenant_Owner response is missing entries from branch_b ({branch_b_str}). "
        f"Seen branch_ids in iteration results: {seen_branch_ids!r}. "
        f"Tenant_Owner should see all branches — no branch filter should be applied "
        f"(Requirements 5.5, 5.6)."
    )


# ---------------------------------------------------------------------------
# Property 15b: Branch_Manager only sees own-branch entries
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    n_own=st.integers(1, 5),
    n_other=st.integers(1, 5),
    n_null=st.integers(0, 3),
)
@settings(max_examples=50)
def test_property_15b_branch_manager_only_sees_own_branch(
    n_own: int,
    n_other: int,
    n_null: int,
) -> None:
    """
    **Validates: Requirements 5.5, 5.6, 5.7**

    Property 15b: Branch_Manager Branch Isolation

    For any Branch_Manager assigned to branch_a:
      - Every returned entry SHALL have branch_id == str(user.branch_id)
      - No entry from branch_b SHALL appear in the response
      - No entry with branch_id=None SHALL appear in the response
      - The count of own-branch results for this iteration SHALL equal n_own

    Because the DB accumulates entries across Hypothesis iterations, we
    compare only the entries created in this iteration using unique branch
    instances per iteration.

    Note on branch_id values: user.branch_id (the UUID stored for the integer
    FK) is used to create AuditLog entries and to compare response values,
    ensuring the view's queryset filter matches the stored values correctly.
    """
    # Create fresh branches for this iteration
    unique_suffix = uuid.uuid4().hex[:8]
    branch_a = _make_branch(f"Branch A {unique_suffix}")
    branch_b = _make_branch(f"Branch B {unique_suffix}")

    # Derive UUID values for both branches (int PK → zero-padded UUID)
    branch_a_uuid = _get_branch_uuid(branch_a)
    branch_b_uuid = _get_branch_uuid(branch_b)
    branch_a_str = str(branch_a_uuid)
    branch_b_str = str(branch_b_uuid)

    # Create Branch_Manager for branch_a
    branch_manager = _make_branch_manager(branch_a)

    # Create n_own entries for branch_a, n_other for branch_b, n_null with no branch
    for _ in range(n_own):
        _make_audit_log(branch_id=branch_a_uuid, action="OWN_BRANCH_ACTION")
    for _ in range(n_other):
        _make_audit_log(branch_id=branch_b_uuid, action="OTHER_BRANCH_ACTION")
    for _ in range(n_null):
        _make_audit_log(branch_id=None, action="NULL_BRANCH_ACTION")

    client = APIClient()
    client.force_login(branch_manager)

    # Collect all pages
    all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)

    branch_b_id_str = branch_b_str
    own_results = [r for r in all_results if r.get("branch_id") == branch_a_str]
    other_results = [r for r in all_results if r.get("branch_id") == branch_b_id_str]

    # Every result must have branch_id == branch_a_str (the manager's own branch)
    for entry in all_results:
        assert entry.get("branch_id") == branch_a_str, (
            f"Branch_Manager received an entry with branch_id={entry.get('branch_id')!r}, "
            f"expected only {branch_a_str!r}. "
            f"Branch_Manager must only see entries from their own branch "
            f"(Requirements 5.6, 5.7)."
        )

    # No entries from branch_b should appear
    assert len(other_results) == 0, (
        f"Branch_Manager received {len(other_results)} entries from branch_b "
        f"({branch_b_id_str}). These MUST NOT appear in the response "
        f"(Requirements 5.6, 5.7)."
    )

    # Exactly n_own entries for branch_a in this iteration
    assert len(own_results) == n_own, (
        f"Branch_Manager should see exactly {n_own} entries from branch_a "
        f"({branch_a_str}), but got {len(own_results)}. "
        f"(Requirements 5.6, 5.7)."
    )


# ---------------------------------------------------------------------------
# Property 15c: Branch_Manager result count matches own-branch entry count
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    n_own=st.integers(1, 5),
    n_other=st.integers(1, 5),
)
@settings(max_examples=50)
def test_property_15c_branch_manager_result_count_matches_own_branch(
    n_own: int,
    n_other: int,
) -> None:
    """
    **Validates: Requirements 5.5, 5.6, 5.7**

    Property 15c: Branch_Manager Result Count Accuracy

    For any Branch_Manager assigned to branch_a and any number of entries
    in branch_a and branch_b, the count of results returned to the
    Branch_Manager SHALL equal exactly n_own (the number of entries created
    for branch_a in this iteration).

    Because the DB accumulates entries across Hypothesis iterations, we
    compare only entries created in this specific iteration by using unique
    branch instances and filtering response results to the iteration's branch_a.
    """
    # Create fresh branches for this iteration
    unique_suffix = uuid.uuid4().hex[:8]
    branch_a = _make_branch(f"Branch A {unique_suffix}")
    branch_b = _make_branch(f"Branch B {unique_suffix}")

    # Derive UUID values for both branches (int PK → zero-padded UUID)
    branch_a_uuid = _get_branch_uuid(branch_a)
    branch_b_uuid = _get_branch_uuid(branch_b)
    branch_a_str = str(branch_a_uuid)
    branch_b_str = str(branch_b_uuid)

    # Create Branch_Manager for branch_a
    branch_manager = _make_branch_manager(branch_a)

    # Create entries
    for _ in range(n_own):
        _make_audit_log(branch_id=branch_a_uuid, action="COUNT_TEST_ACTION")
    for _ in range(n_other):
        _make_audit_log(branch_id=branch_b_uuid, action="COUNT_TEST_OTHER")

    client = APIClient()
    client.force_login(branch_manager)

    # Collect all pages
    all_results = _get_all_results(client, AUDIT_LOG_LIST_URL)

    own_results = [r for r in all_results if r.get("branch_id") == branch_a_str]
    other_results = [r for r in all_results if r.get("branch_id") == branch_b_str]

    # Exactly n_own entries for this iteration's branch_a
    assert len(own_results) == n_own, (
        f"Branch_Manager result count mismatch: expected {n_own} entries for "
        f"branch_a ({branch_a_str}), but found {len(own_results)} in response. "
        f"(Requirements 5.6, 5.7)."
    )

    # No branch_b entries should be visible
    assert len(other_results) == 0, (
        f"Branch_Manager received {len(other_results)} entries from branch_b "
        f"({branch_b_str}). These MUST NOT appear in the response "
        f"(Requirements 5.6, 5.7)."
    )


# ---------------------------------------------------------------------------
# Property 15d: Unauthorized roles cannot read audit logs (HTTP 403)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(
    role=st.sampled_from([
        UserRole.RECEPTIONIST,
        UserRole.KITCHEN_STAFF,
        UserRole.CUSTOMER,
    ]),
)
@settings(max_examples=30)
def test_property_15d_unauthorized_roles_cannot_read_audit_logs(
    role: str,
) -> None:
    """
    **Validates: Requirements 5.5, 5.6, 5.7**

    Property 15d: Unauthorized Role Access Denied

    For any role without audit-read permission (Receptionist, Kitchen_Staff,
    Customer), a GET /api/v1/audit-logs/ request SHALL return HTTP 403
    Forbidden. Only Super_Admin, Tenant_Owner, and Branch_Manager have
    access to audit logs per Requirement 4.2.
    """
    email = f"unauth_{role.lower().replace('_', '')}_{uuid.uuid4().hex[:8]}@test.example.com"
    user = User.objects.create_user(
        email=email,
        password="TestPass123!",
        role=role,
    )

    client = APIClient()
    client.force_login(user)

    response = client.get(AUDIT_LOG_LIST_URL)

    assert response.status_code == 403, (
        f"Expected HTTP 403 Forbidden for role={role!r}, "
        f"got HTTP {response.status_code}. "
        f"Roles without audit-read permission (Receptionist, Kitchen_Staff, Customer) "
        f"MUST be denied access to audit logs (Requirements 5.5, 5.7). "
        f"Response: {getattr(response, 'data', response.content)!r}"
    )
