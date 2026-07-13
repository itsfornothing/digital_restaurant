"""
Property-Based Tests: Session Independence

# Feature: restaurant-platform, Property 31: Session Independence

Property 31: Session Independence
  Any two QR scan sessions created for the same table have distinct session IDs
  and no shared state — modifying data in one session does not affect another.

Validates: Requirements 15.5

Requirement 15.5:
  WHEN a new QR scan creates a new Session, THE Platform SHALL create a fully
  independent session with no linkage to any previous session for that table
  or device.

Strategy:
  Two test functions cover this property at different layers:

  Level 1 — Service/session layer (unit):
    Create N Django session objects directly, store customer_session data in
    each, verify all session keys are distinct, and verify mutating one session
    does not bleed into any other session.

  Level 2 — API layer (integration):
    POST to /api/v1/customer/session/ N times with the same valid QR token
    (same table) using separate APIClient instances (each with its own cookie
    jar).  Assert every response carries a distinct session_id.  Then verify
    that customer_session data written into session A cannot be read from
    session B.
"""

import json
import uuid
from itertools import combinations

import pytest
from django.test import Client
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.branches.models import Branch, Table
from apps.qr.models import QRCode

# ---------------------------------------------------------------------------
# URL constant
# ---------------------------------------------------------------------------

SESSION_URL = "/api/v1/customer/session/"


# ---------------------------------------------------------------------------
# Helpers: create test infrastructure
# ---------------------------------------------------------------------------


def _make_branch() -> Branch:
    """Create and return a unique Branch instance."""
    return Branch.objects.create(
        name=f"Test Branch {uuid.uuid4().hex[:8]}",
        address="1 Test Street, Addis Ababa",
        phone="0900000000",
        email=f"branch-{uuid.uuid4().hex[:8]}@test.com",
    )


def _make_table(branch: Branch, number: str = "1") -> Table:
    """Create and return a Table linked to the given Branch."""
    return Table.objects.create(
        branch=branch,
        number=number,
        seat_count=4,
    )


def _make_active_qr(table: Table) -> QRCode:
    """Create and return an active QRCode for the given Table."""
    return QRCode.objects.create(
        table=table,
        token=uuid.uuid4(),
        is_active=True,
        image_url="",
    )


# ---------------------------------------------------------------------------
# Level 1 — Service/session layer
# Property 31: Session Independence (unit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(n=st.integers(min_value=2, max_value=10))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_31_session_independence_unit(n: int) -> None:
    """
    **Validates: Requirements 15.5**

    Level 1 — Service/session layer.

    For any N ∈ [2, 10] sessions created for the same table:
      1. All N session keys are distinct.
      2. Writing a key into session_i does NOT appear in session_j (i ≠ j).
      3. The customer_session data set per session is scoped only to that
         session — it does not propagate to any other session.

    Sessions are created directly via the Django database session store so the
    test is a fast, isolated unit test that does not involve HTTP.
    """
    from django.contrib.sessions.backends.db import SessionStore

    # ------------------------------------------------------------------
    # Arrange: DB infrastructure (branch + table + qr, for realistic data)
    # ------------------------------------------------------------------
    branch = _make_branch()
    table = _make_table(branch)

    # ------------------------------------------------------------------
    # Act: create N independent sessions, each mimicking what
    # CustomerSessionView does: store customer_session data and save.
    # ------------------------------------------------------------------
    sessions = []
    for i in range(n):
        s = SessionStore()
        s["customer_session"] = {
            "branch_id": str(branch.id),
            "table_id": str(table.id),
            "table_number": table.number,
            # A per-session marker so we can verify isolation later
            "scan_index": i,
        }
        s.modified = True
        s.create()          # persists + generates session_key
        sessions.append(s)

    try:
        # ------------------------------------------------------------------
        # Assert 1: all session keys are distinct
        # ------------------------------------------------------------------
        keys = [s.session_key for s in sessions]
        unique_keys = set(keys)

        assert len(unique_keys) == n, (
            f"Expected {n} distinct session keys, but only {len(unique_keys)} "
            f"were unique. Keys: {keys}"
        )

        # ------------------------------------------------------------------
        # Assert 2: no shared state — writing a novel key into session_i
        # must NOT appear in any session_j (j ≠ i).
        # ------------------------------------------------------------------
        for i, sess_i in enumerate(sessions):
            novel_key = f"isolation_probe_{uuid.uuid4().hex}"
            novel_val = f"value_from_session_{i}"
            sess_i[novel_key] = novel_val
            sess_i.save()

            for j, sess_j in enumerate(sessions):
                if i == j:
                    continue
                # Re-load session_j from the DB to pick up any accidental
                # cross-contamination.
                fresh_j = SessionStore(session_key=sess_j.session_key)
                assert novel_key not in fresh_j, (
                    f"Session isolation violated: key '{novel_key}' written "
                    f"into session[{i}] (key={sess_i.session_key}) was "
                    f"unexpectedly found in session[{j}] "
                    f"(key={sess_j.session_key})."
                )

        # ------------------------------------------------------------------
        # Assert 3: customer_session data is independently scoped —
        # each session's scan_index matches only its own value.
        # ------------------------------------------------------------------
        for i, sess_i in enumerate(sessions):
            fresh = SessionStore(session_key=sess_i.session_key)
            customer_data = fresh.get("customer_session", {})
            assert customer_data.get("scan_index") == i, (
                f"Session[{i}] (key={sess_i.session_key}) has unexpected "
                f"customer_session data: {customer_data}. "
                f"Expected scan_index={i}."
            )
            assert str(customer_data.get("branch_id")) == str(branch.id), (
                f"Session[{i}] branch_id mismatch: "
                f"{customer_data.get('branch_id')} != {branch.id}"
            )
            assert str(customer_data.get("table_id")) == str(table.id), (
                f"Session[{i}] table_id mismatch: "
                f"{customer_data.get('table_id')} != {table.id}"
            )

    finally:
        # ------------------------------------------------------------------
        # Teardown: delete all created sessions and DB records
        # ------------------------------------------------------------------
        for s in sessions:
            try:
                s.delete()
            except Exception:
                pass
        branch.delete()


# ---------------------------------------------------------------------------
# Level 2 — API layer
# Property 31: Session Independence (integration)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@given(n=st.integers(min_value=2, max_value=10))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_31_session_independence_api(n: int) -> None:
    """
    **Validates: Requirements 15.5**

    Level 2 — API layer.

    For any N ∈ [2, 10] POST requests to /api/v1/customer/session/ using the
    same QR token (same table):
      1. Each response returns a distinct session_id.
      2. No two responses share the same session_id.
      3. Session data written into one session is not readable via another
         session's key — the sessions are fully independent stores.

    Each POST is performed with a fresh APIClient (its own cookie jar) to
    simulate N independent customer devices scanning the same QR code.
    """
    from django.contrib.sessions.backends.db import SessionStore

    # ------------------------------------------------------------------
    # Arrange: create Branch, Table, and one active QRCode
    # ------------------------------------------------------------------
    branch = _make_branch()
    table = _make_table(branch)
    qr = _make_active_qr(table)
    token = str(qr.token)

    session_ids = []
    clients = []

    try:
        # ------------------------------------------------------------------
        # Act: make N independent POST requests with separate clients.
        # Use Django's test Client (not DRF APIClient) so that the full
        # SessionMiddleware pipeline runs — this ensures the session is
        # saved and session_key is set before the view builds its response.
        # ------------------------------------------------------------------
        for _ in range(n):
            client = Client()
            resp = client.post(
                SESSION_URL,
                data=json.dumps({"token": token}),
                content_type="application/json",
            )

            assert resp.status_code == 200, (
                f"Expected 200 from {SESSION_URL} with token {token}, "
                f"got {resp.status_code}: {resp.content}"
            )

            body = json.loads(resp.content)
            session_id = body.get("session_id")
            assert session_id is not None, (
                f"Response did not include 'session_id'. Response body: {body}"
            )

            session_ids.append(session_id)
            clients.append(client)

        # ------------------------------------------------------------------
        # Assert 1 & 2: all N session_ids are distinct
        # ------------------------------------------------------------------
        unique_ids = set(session_ids)
        assert len(unique_ids) == n, (
            f"Expected {n} distinct session_ids from {n} QR scans, "
            f"but only {len(unique_ids)} were unique. IDs: {session_ids}"
        )

        # ------------------------------------------------------------------
        # Assert 3: no shared state across sessions —
        # for every pair (i, j), verify that the session key from scan i
        # does NOT contain customer_session data from scan j.
        # ------------------------------------------------------------------
        for i, j in combinations(range(n), 2):
            key_i = session_ids[i]
            key_j = session_ids[j]

            store_i = SessionStore(session_key=key_i)
            store_j = SessionStore(session_key=key_j)

            data_i = store_i.get("customer_session", {})
            data_j = store_j.get("customer_session", {})

            # Both sessions must carry valid, independent customer_session data
            assert data_i, (
                f"Session[{i}] (key={key_i}) has no customer_session data."
            )
            assert data_j, (
                f"Session[{j}] (key={key_j}) has no customer_session data."
            )

            # Verify the sessions are distinct objects — they should hold the
            # same branch/table (same QR), but they are NOT the same session.
            assert key_i != key_j, (
                f"Sessions {i} and {j} share the same session_key '{key_i}'."
            )

            # Write a probe value into session_i and confirm it doesn't appear
            # in session_j (cross-contamination check).
            probe_key = f"probe_{uuid.uuid4().hex}"
            probe_val = f"written_by_scan_{i}"
            store_i[probe_key] = probe_val
            store_i.save()

            fresh_j = SessionStore(session_key=key_j)
            assert probe_key not in fresh_j, (
                f"Session isolation violated: key '{probe_key}' written "
                f"into session[{i}] (key={key_i}) leaked into "
                f"session[{j}] (key={key_j})."
            )

    finally:
        # ------------------------------------------------------------------
        # Teardown
        # ------------------------------------------------------------------
        branch.delete()
