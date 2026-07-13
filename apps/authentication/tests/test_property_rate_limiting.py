"""
Property-Based Tests: Rate Limiting Enforcement

Property 9: Rate Limiting Enforcement

  (a) After exactly 10 authentication attempts within a 60-second window from
      one IP address, the 11th attempt returns HTTP 429 with the
      RATE_LIMIT_EXCEEDED error code.
  (b) After the rate-limit window resets (cache cleared), attempts from that
      IP are permitted again (HTTP 200 or any non-429 status).

Validates: Requirements 3.6

Strategy:
  - 9a: For any prefix count p in [0, 9], fire p + (10 - p) = 10 requests to
        exhaust the window, then send one more — the 11th must return 429.
        This ensures the property holds regardless of "how we arrived at 10"
        (all at once, or built up from a prior count).
  - 9b: For any p in [0, 10], exhaust the window from that point, hit the
        limit (verify 429), then clear the Django cache to simulate window
        expiry and confirm the next attempt is no longer rate-limited (not 429).

No mocking is used — the rate-limit logic is exercised end-to-end through the
same RateLimitMixin code path that production requests use.  The Django
locmem cache (used in the testing settings) stores and increments the counter
exactly as the Redis-backed production cache would, but without requiring a
live Redis instance.

Notes on cache isolation between test iterations:
  django-ratelimit stores counts in the Django default cache under a key that
  encodes the group name, the IP address, the rate, and the current time
  window.  Each Hypothesis example runs in a fresh database transaction, but
  the locmem cache is process-global.  We therefore explicitly clear the
  cache in the test body before making any requests, so that counter state
  from a prior iteration (or earlier sub-tests) cannot bleed into the current
  one.
"""

import pytest
from django.core.cache import cache
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
_PASSWORD = "TestPass123!"
_EMAIL = "ratelimit_test@example.com"

# django-ratelimit allows exactly 10 requests before blocking (count > 10)
_RATE_LIMIT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_user(email: str = _EMAIL, password: str = _PASSWORD) -> User:
    """Return (or create) a non-locked user for use in rate-limit tests."""
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password=password, role="Receptionist")


def _post_login(client: APIClient, email: str = _EMAIL, password: str = _PASSWORD) -> int:
    """POST to the login endpoint; return the HTTP status code."""
    resp = client.post(
        LOGIN_URL,
        {"email": email, "password": password},
        format="json",
    )
    return resp.status_code


def _post_login_full(client: APIClient, email: str = _EMAIL, password: str = _PASSWORD):
    """POST to the login endpoint; return the full response."""
    return client.post(
        LOGIN_URL,
        {"email": email, "password": password},
        format="json",
    )


def _fire_n_requests(client: APIClient, n: int, email: str = _EMAIL) -> None:
    """Send *n* login requests (with the correct password) to the login endpoint."""
    for _ in range(n):
        _post_login(client, email)


# ---------------------------------------------------------------------------
# Property 9a — After 10 attempts in 60s, the 11th returns 429
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(prefix=st.integers(min_value=0, max_value=9))
@settings(max_examples=200)
def test_property_9a_eleventh_request_returns_429(prefix: int) -> None:
    """
    **Validates: Requirements 3.6**

    For any number of prior attempts `prefix` in [0, 9], once the total
    number of attempts from the same IP reaches 10 (filling the window),
    the very next (11th) attempt MUST be rejected with HTTP 429 and the
    RATE_LIMIT_EXCEEDED error code.

    The test clears the cache before each iteration so that window state
    from a previous example cannot carry over and corrupt the count.
    """
    # Start with a clean rate-limit counter for this IP / window
    cache.clear()

    # Ensure a valid user exists so rejected attempts aren't due to 401/403
    _ensure_user()

    client = APIClient()

    # Fire `prefix` requests to represent prior activity in the window
    _fire_n_requests(client, prefix)

    # Fire the remaining requests to reach exactly 10 total
    remaining = _RATE_LIMIT - prefix
    _fire_n_requests(client, remaining)

    # The 11th request MUST be rate-limited (429)
    resp = _post_login_full(client)

    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS, (
        f"Expected HTTP 429 on the 11th request (prefix={prefix}), "
        f"got HTTP {resp.status_code}. "
        f"The rate limit of {_RATE_LIMIT} attempts per 60s must be enforced."
    )
    data = resp.json()
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED", (
        f"Expected error code RATE_LIMIT_EXCEEDED on 11th request, "
        f"got {data['error']['code']!r} (prefix={prefix})."
    )


# ---------------------------------------------------------------------------
# Property 9b — After window reset, requests are permitted again
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(extra_over_limit=st.integers(min_value=0, max_value=5))
@settings(max_examples=200)
def test_property_9b_after_window_reset_requests_are_permitted(extra_over_limit: int) -> None:
    """
    **Validates: Requirements 3.6**

    After the rate-limit window is exhausted (at least 11 requests sent) and
    the window subsequently resets (simulated by clearing the cache), requests
    from the same IP MUST be permitted again — i.e. the response MUST NOT be
    HTTP 429.

    `extra_over_limit` represents how many *additional* requests beyond the
    11th were sent before the window reset, verifying that the post-reset
    behaviour is independent of how far over the limit the client went.
    """
    # Start with a clean counter
    cache.clear()

    # Ensure a valid user exists
    _ensure_user()

    client = APIClient()

    # Exhaust the window: send 11 + extra_over_limit requests
    requests_to_send = _RATE_LIMIT + 1 + extra_over_limit
    _fire_n_requests(client, requests_to_send)

    # Confirm we are currently rate-limited (sanity check)
    resp_while_limited = _post_login_full(client)
    assert resp_while_limited.status_code == status.HTTP_429_TOO_MANY_REQUESTS, (
        f"Sanity check failed: expected 429 after {requests_to_send} requests, "
        f"got {resp_while_limited.status_code}."
    )

    # Simulate window expiry by clearing the cache (equivalent to the 60s
    # window rolling over so the counter key expires in production)
    cache.clear()

    # After the window resets, the next request MUST NOT be rate-limited
    resp_after_reset = _post_login_full(client)

    assert resp_after_reset.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
        f"Expected a non-429 response after window reset "
        f"(extra_over_limit={extra_over_limit}), "
        f"got HTTP {resp_after_reset.status_code}. "
        f"Rate limiting must not persist beyond the 60-second window."
    )
