"""
Property-Based Tests: Password Reset Token Uniqueness and Expiry

Property 8: Password Reset Token Uniqueness and Expiry

  (a) Only the most-recently issued token is valid; all prior tokens for that
      user are invalidated (is_used=True) when a new reset is requested.
  (b) A token older than 1 hour is expired (is_expired returns True).
  (c) A token younger than 1 hour is not expired (is_expired returns False).
  (d) Round-trip: after a successful password reset confirm, the token is
      marked is_used=True; a second use of the same token returns 400
      INVALID_TOKEN.

Validates: Requirements 3.4

Strategy:
  - 8a: For n in [2, 5] consecutive reset requests, only the last token
        has is_used=False; all prior tokens have is_used=True.
  - 8b: For any age >= 1 hour, is_expired is True; the confirm endpoint
        returns 400 TOKEN_EXPIRED.
  - 8c: For any age in [0, 59.9) minutes, is_expired is False.
  - 8d: Full HTTP round-trip — POST password-reset → POST confirm → token
        is_used=True; a second confirm returns 400 INVALID_TOKEN.
"""

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from hypothesis import given, settings
from hypothesis import strategies as st
from rest_framework import status
from rest_framework.test import APIClient

from apps.authentication.models import PasswordResetToken

User = get_user_model()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PASSWORD_RESET_URL = "/api/v1/auth/password-reset/"
PASSWORD_RESET_CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
_DEFAULT_PASSWORD = "Pass123!"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fresh_user(email: str, password: str = _DEFAULT_PASSWORD) -> User:
    """Delete any existing user with this email and create a fresh one."""
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password=password, role="Receptionist")


def _issue_token(user: User) -> PasswordResetToken:
    """Replicate the exact token-issuance logic from PasswordResetRequestView."""
    PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
    return PasswordResetToken.objects.create(user=user)


# ---------------------------------------------------------------------------
# Property 8a — Only the most recent token is valid
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(n=st.integers(min_value=2, max_value=5))
@settings(max_examples=200)
def test_property_8a_only_most_recent_token_is_valid(n: int) -> None:
    """
    **Validates: Requirements 3.4**

    For any n >= 2 consecutive password reset requests, only the most-recently
    issued token shall have is_used=False.  All prior (n-1) tokens shall have
    is_used=True immediately after the n-th request is processed.
    """
    email = f"reset_8a_n{n}@example.com"
    user = _make_fresh_user(email)

    tokens = []
    for _ in range(n):
        token = _issue_token(user)
        tokens.append(token)

    # Refresh all tokens from the DB
    for t in tokens:
        t.refresh_from_db()

    # Only the last token should be unused
    final_token = tokens[-1]
    prior_tokens = tokens[:-1]

    assert not final_token.is_used, (
        f"The most-recently issued token (pk={final_token.pk}) must have "
        f"is_used=False after {n} consecutive reset requests."
    )

    for i, prior in enumerate(prior_tokens):
        assert prior.is_used, (
            f"Prior token #{i + 1} (pk={prior.pk}) must have is_used=True after "
            f"a newer reset was requested (n={n} total requests)."
        )


# ---------------------------------------------------------------------------
# Property 8b — A token >= 1 hour old is expired
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(hours_old=st.floats(min_value=1.0, max_value=72.0))
@settings(max_examples=200)
def test_property_8b_token_older_than_one_hour_is_expired(hours_old: float) -> None:
    """
    **Validates: Requirements 3.4**

    For any token whose created_at is >= 1 hour in the past, is_expired must
    return True and the confirm endpoint must return 400 TOKEN_EXPIRED.
    """
    email = f"reset_8b_{abs(int(hours_old * 1000))}@example.com"
    user = _make_fresh_user(email)

    token = PasswordResetToken.objects.create(user=user)

    # Backdate created_at by the given number of hours (bypass auto_now_add)
    PasswordResetToken.objects.filter(pk=token.pk).update(
        created_at=timezone.now() - timedelta(hours=hours_old)
    )
    token.refresh_from_db()

    # Model-level check
    assert token.is_expired, (
        f"Token created {hours_old:.4f} hours ago must have is_expired=True "
        f"(any age >= 1 hour should be expired). created_at={token.created_at}"
    )

    # HTTP endpoint check — confirm must return 400 TOKEN_EXPIRED
    client = APIClient()
    resp = client.post(
        PASSWORD_RESET_CONFIRM_URL,
        {"token": str(token.token), "new_password": "NewPass456!"},
        format="json",
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST, (
        f"Expected HTTP 400 for an expired token ({hours_old:.4f} hours old), "
        f"got HTTP {resp.status_code}."
    )
    assert resp.data["error"]["code"] == "TOKEN_EXPIRED", (
        f"Expected error code TOKEN_EXPIRED for an expired token, "
        f"got {resp.data['error']['code']!r}."
    )


# ---------------------------------------------------------------------------
# Property 8c — A fresh token (< 1 hour old) is not expired
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(minutes_old=st.floats(min_value=0.0, max_value=59.9))
@settings(max_examples=200)
def test_property_8c_token_younger_than_one_hour_is_not_expired(minutes_old: float) -> None:
    """
    **Validates: Requirements 3.4**

    For any token whose created_at is less than 1 hour in the past,
    is_expired must return False.
    """
    email = f"reset_8c_{abs(int(minutes_old * 1000))}@example.com"
    user = _make_fresh_user(email)

    token = PasswordResetToken.objects.create(user=user)

    # Backdate created_at by the given number of minutes
    PasswordResetToken.objects.filter(pk=token.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_old)
    )
    token.refresh_from_db()

    assert not token.is_expired, (
        f"Token created {minutes_old:.4f} minutes ago must have is_expired=False "
        f"(< 60 minutes old is within the valid window). created_at={token.created_at}"
    )


# ---------------------------------------------------------------------------
# Property 8d — Round-trip: successful reset marks token used; reuse fails
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@given(st.just(True))
@settings(max_examples=50)
def test_property_8d_roundtrip_token_marked_used_after_confirm(flag: bool) -> None:
    """
    **Validates: Requirements 3.4**

    Full HTTP round-trip test:
      1. POST /api/v1/auth/password-reset/ → issues a fresh token.
      2. POST /api/v1/auth/password-reset/confirm/ with that token → 200.
      3. Token is_used becomes True in the database.
      4. A second POST to /confirm/ with the same token → 400 INVALID_TOKEN.
    """
    email = "reset_8d_roundtrip@example.com"
    user = _make_fresh_user(email)

    client = APIClient()

    # Step 1: Request a password reset (mock email sending)
    with patch("apps.authentication.views.send_mail"):
        resp = client.post(
            PASSWORD_RESET_URL,
            {"email": email},
            format="json",
        )

    assert resp.status_code == status.HTTP_200_OK, (
        f"Expected HTTP 200 from password-reset request, got {resp.status_code}."
    )

    # Retrieve the token that was just created
    reset_token = (
        PasswordResetToken.objects.filter(user=user, is_used=False)
        .order_by("-created_at")
        .first()
    )
    assert reset_token is not None, "A valid (is_used=False) token must exist after the reset request."

    token_value = str(reset_token.token)

    # Step 2: Confirm the reset with the fresh token
    resp = client.post(
        PASSWORD_RESET_CONFIRM_URL,
        {"token": token_value, "new_password": "NewSecurePass789!"},
        format="json",
    )

    assert resp.status_code == status.HTTP_200_OK, (
        f"Expected HTTP 200 from password-reset confirm, got {resp.status_code}. "
        f"Response: {resp.data}"
    )

    # Step 3: Token must now be marked as used
    reset_token.refresh_from_db()
    assert reset_token.is_used, (
        f"Token (pk={reset_token.pk}) must have is_used=True after a successful confirm."
    )

    # Step 4: A second use of the same token must be rejected
    resp2 = client.post(
        PASSWORD_RESET_CONFIRM_URL,
        {"token": token_value, "new_password": "AnotherPass999!"},
        format="json",
    )

    assert resp2.status_code == status.HTTP_400_BAD_REQUEST, (
        f"Expected HTTP 400 on second use of already-used token, got {resp2.status_code}."
    )
    assert resp2.data["error"]["code"] == "INVALID_TOKEN", (
        f"Expected error code INVALID_TOKEN on second use, "
        f"got {resp2.data['error']['code']!r}."
    )
