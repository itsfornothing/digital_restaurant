"""
Property-Based Tests: Account Lockout After Exactly 5 Failures

Property 7: Account Lockout After Exactly 5 Failures

  (a) After exactly 5 consecutive failed login attempts, a subsequent login
      with the CORRECT password is rejected (account is locked).
  (b) After fewer than 5 failed attempts (0–4), a login with the CORRECT
      password succeeds.

Validates: Requirements 3.3

The tests exercise the full authentication stack via LoginSerializer and the
User model's lockout helpers.  No mocking is used — the lockout logic is
exercised end-to-end through the same code path as real login requests.

Strategy:
  - n in [0, 4]: simulate n wrong-password attempts, then attempt with the
    correct password — must succeed (HTTP 200 / no ACCOUNT_LOCKED error).
  - m = 5: simulate 5 wrong-password attempts, then attempt with the correct
    password — must be rejected (ACCOUNT_LOCKED).
"""

import pytest
from django.contrib.auth import get_user_model
from hypothesis import given, settings
from hypothesis import strategies as st
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGIN_URL = "/api/v1/auth/login/"
_CORRECT_PASSWORD = "CorrectPass123!"
_WRONG_PASSWORD = "WrongPass000!"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fresh_user(db_marker, email: str, password: str = _CORRECT_PASSWORD) -> User:
    """Create a brand-new user with zeroed lockout counters."""
    # Delete any lingering user from a prior test iteration
    User.objects.filter(email=email).delete()
    return User.objects.create_user(
        email=email,
        password=password,
        role="Receptionist",
    )


def _attempt_login(client: APIClient, email: str, password: str) -> int:
    """POST to the login endpoint and return the HTTP status code."""
    resp = client.post(
        LOGIN_URL,
        {"email": email, "password": password},
        format="json",
    )
    return resp.status_code


def _simulate_failures(client: APIClient, email: str, n: int) -> None:
    """Perform *n* failed login attempts against *email*."""
    for _ in range(n):
        _attempt_login(client, email, _WRONG_PASSWORD)


# ---------------------------------------------------------------------------
# Property 7a — Fewer than 5 failures → correct password succeeds
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(n=st.integers(min_value=0, max_value=4))
@settings(max_examples=500)
def test_property_7a_fewer_than_5_failures_allows_correct_login(n: int) -> None:
    """
    **Validates: Requirements 3.3**

    For any number of failures n in [0, 4], a subsequent login with the
    correct password MUST succeed (HTTP 200).  The account must not be locked.
    """
    # Use a deterministic but unique email per n so Hypothesis can replay
    # counter-examples consistently.
    email = f"lockout_test_n{n}@example.com"

    # Ensure a clean user exists (re-create if leftover from a prior run)
    _make_fresh_user(None, email)

    client = APIClient()

    # Simulate n wrong-password attempts
    _simulate_failures(client, email, n)

    # A correct-password attempt must succeed
    result_status = _attempt_login(client, email, _CORRECT_PASSWORD)

    assert result_status == status.HTTP_200_OK, (
        f"Expected HTTP 200 after {n} failure(s) followed by correct password, "
        f"got HTTP {result_status}.  Account should NOT be locked after < 5 failures."
    )


# ---------------------------------------------------------------------------
# Property 7b — Exactly 5 failures → correct password is also rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(m=st.just(5))
@settings(max_examples=500)
def test_property_7b_exactly_5_failures_locks_account(m: int) -> None:
    """
    **Validates: Requirements 3.3**

    After exactly 5 consecutive failed login attempts, a subsequent login
    with the CORRECT password MUST be rejected with ACCOUNT_LOCKED (HTTP 403).

    The lockout check runs BEFORE password verification so that even a
    correct password cannot unlock the account.
    """
    email = "lockout_test_m5@example.com"

    _make_fresh_user(None, email)

    client = APIClient()

    # Simulate exactly 5 wrong-password attempts
    _simulate_failures(client, email, m)

    # Even the correct password must now be rejected
    resp = client.post(
        LOGIN_URL,
        {"email": email, "password": _CORRECT_PASSWORD},
        format="json",
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN, (
        f"Expected HTTP 403 (ACCOUNT_LOCKED) after {m} failure(s) + correct password, "
        f"got HTTP {resp.status_code}."
    )
    assert resp.data["error"]["code"] == "ACCOUNT_LOCKED", (
        f"Expected error code ACCOUNT_LOCKED, got {resp.data['error']['code']!r}"
    )
