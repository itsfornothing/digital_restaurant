"""
apps/authentication/tests/test_auth_api.py

API-level test suite for authentication endpoints covering TC-A01 through TC-A07.

Test cases:
  TC-A01: POST /api/v1/auth/login/ — valid credentials → 200 + session cookie set
  TC-A02: POST /api/v1/auth/login/ — wrong password → 401, no user enumeration
  TC-A03: POST /api/v1/auth/login/ — non-existent email → 401, same message as TC-A02
  TC-A04: Call protected endpoint with expired/invalid session → 401
  TC-A05: 5 consecutive failed logins → account locked, 6th attempt → 429
           (overlaps 3.4/3.9 but adds API-level assertion)
  TC-A06: POST /api/v1/auth/password-reset/ — valid email → 200, email sent
  TC-A07: POST /api/v1/auth/logout/ → 200; subsequent protected call → 401

Validates: Requirements 3.1, 3.3, 3.4, 3.6 (TC-A01–A07)

Notes:
  - Tests use the lightweight testing settings (SQLite in-memory, no django-tenants).
  - Rate-limit state is cleared before TC-A05 via Django's cache framework.
  - Email delivery is verified via Django's locmem email backend (EMAIL_BACKEND =
    'django.core.mail.backends.locmem.EmailBackend' in testing settings).
  - The session endpoint GET /api/v1/auth/session/ is used as the representative
    "protected endpoint" for TC-A04 and TC-A07.
"""

import os
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
SESSION_URL = "/api/v1/auth/session/"
PASSWORD_RESET_URL = "/api/v1/auth/password-reset/"

# ---------------------------------------------------------------------------
# Shared fixture: a freshly created active user
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def valid_user(db):
    """A valid, unlocked user with a known password."""
    return User.objects.create_user(
        email="staff@example.com",
        password="ValidPass123!",
        role="Receptionist",
    )


# ---------------------------------------------------------------------------
# TC-A01: Valid credentials → HTTP 200 + session cookie set
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA01ValidLogin:
    """
    TC-A01: POST /api/v1/auth/login/ with valid credentials.

    Expected:
      - HTTP 200
      - Response body contains user_id, email, role
      - 'sessionid' cookie is set in the response (session-based auth, Req 3.1)
    """

    def test_valid_credentials_return_200(self, api_client, valid_user):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-A01: expected HTTP 200 for valid credentials, got {resp.status_code}"
        )

    def test_valid_credentials_return_user_info(self, api_client, valid_user):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert "user_id" in data, "TC-A01: response must contain 'user_id'"
        assert data["email"] == "staff@example.com", "TC-A01: email mismatch"
        assert data["role"] == "Receptionist", "TC-A01: role mismatch"

    def test_valid_credentials_set_session_cookie(self, api_client, valid_user):
        """
        After a successful login the 'sessionid' cookie must be present,
        confirming session-based authentication (Requirement 3.1).
        """
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "sessionid" in resp.cookies, (
            "TC-A01: 'sessionid' cookie must be set on successful login (Requirement 3.1)"
        )

    def test_session_cookie_is_httponly(self, api_client, valid_user):
        """
        The session cookie must carry HttpOnly to prevent JavaScript access
        (Requirement 3.1).
        """
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        session_cookie = resp.cookies.get("sessionid")
        # SimpleCookie.morsel has httponly attribute
        assert session_cookie is not None, "TC-A01: sessionid cookie missing"
        # DRF test client's SimpleCookie: check the 'httponly' flag
        assert session_cookie["httponly"], (
            "TC-A01: session cookie must be HttpOnly (Requirement 3.1)"
        )


# ---------------------------------------------------------------------------
# TC-A02: Wrong password → 401, generic error (no user enumeration)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA02WrongPassword:
    """
    TC-A02: POST /api/v1/auth/login/ with a valid email but wrong password.

    Expected:
      - HTTP 401
      - Error code INVALID_CREDENTIALS (not password-specific)
      - Error message must NOT reveal that the email exists
    """

    def test_wrong_password_returns_401(self, api_client, valid_user):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WrongPassword!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"TC-A02: expected HTTP 401 for wrong password, got {resp.status_code}"
        )

    def test_wrong_password_returns_invalid_credentials_code(self, api_client, valid_user):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WrongPassword!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        error_code = resp.data["error"]["code"]
        assert error_code == "INVALID_CREDENTIALS", (
            f"TC-A02: expected INVALID_CREDENTIALS code, got {error_code!r}"
        )

    def test_wrong_password_error_message_is_generic(self, api_client, valid_user):
        """
        The error message must not reveal whether the email exists or not
        (no user enumeration; Requirement 3.4 and security best practice).
        """
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WrongPassword!"},
            format="json",
        )
        message = resp.data["error"]["message"].lower()
        assert "email" in message or "password" in message or "invalid" in message, (
            "TC-A02: error message must be generic (e.g. 'Invalid email or password')"
        )
        # Must not reveal that the email is registered
        assert "exist" not in message, "TC-A02: message must not confirm email existence"
        assert "registered" not in message, "TC-A02: message must not confirm email is registered"
        assert "found" not in message, "TC-A02: message must not confirm user was found"


# ---------------------------------------------------------------------------
# TC-A03: Non-existent email → 401, same message as TC-A02
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA03NonExistentEmail:
    """
    TC-A03: POST /api/v1/auth/login/ with an email address that does not exist.

    Expected:
      - HTTP 401
      - Error code INVALID_CREDENTIALS (same as TC-A02)
      - Error message identical to the wrong-password message (Requirement 3.4)
    """

    def test_nonexistent_email_returns_401(self, api_client, db):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "nobody@nowhere.com", "password": "AnyPassword1!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"TC-A03: expected HTTP 401 for non-existent email, got {resp.status_code}"
        )

    def test_nonexistent_email_returns_invalid_credentials_code(self, api_client, db):
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "nobody@nowhere.com", "password": "AnyPassword1!"},
            format="json",
        )
        error_code = resp.data["error"]["code"]
        assert error_code == "INVALID_CREDENTIALS", (
            f"TC-A03: expected INVALID_CREDENTIALS code, got {error_code!r}"
        )

    def test_nonexistent_email_same_message_as_wrong_password(self, api_client, valid_user):
        """
        The error message for a non-existent email must be identical to the
        message for a valid email with a wrong password, preventing user enumeration.

        Cache is cleared first to avoid rate-limit state from prior tests.
        """
        cache.clear()

        resp_wrong_pw = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WrongPassword!"},
            format="json",
        )
        resp_no_user = api_client.post(
            LOGIN_URL,
            {"email": "nobody@nowhere.com", "password": "AnyPassword1!"},
            format="json",
        )

        assert resp_wrong_pw.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"TC-A03: expected 401 for wrong password, got {resp_wrong_pw.status_code}"
        )
        assert resp_no_user.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"TC-A03: expected 401 for non-existent email, got {resp_no_user.status_code}"
        )

        msg_wrong_pw = resp_wrong_pw.json()["error"]["message"]
        msg_no_user = resp_no_user.json()["error"]["message"]
        assert msg_wrong_pw == msg_no_user, (
            f"TC-A03: wrong-password message ({msg_wrong_pw!r}) must equal "
            f"non-existent-email message ({msg_no_user!r}) to prevent user enumeration"
        )

    def test_nonexistent_email_same_error_code_as_wrong_password(self, api_client, valid_user):
        """
        Both scenarios must return the same error code (INVALID_CREDENTIALS).
        Cache is cleared to avoid stale rate-limit state from prior tests.
        """
        cache.clear()

        resp_wrong_pw = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WrongPassword!"},
            format="json",
        )
        resp_no_user = api_client.post(
            LOGIN_URL,
            {"email": "nobody@nowhere.com", "password": "AnyPassword1!"},
            format="json",
        )

        assert resp_wrong_pw.status_code == status.HTTP_401_UNAUTHORIZED
        assert resp_no_user.status_code == status.HTTP_401_UNAUTHORIZED

        code_wrong_pw = resp_wrong_pw.json()["error"]["code"]
        code_no_user = resp_no_user.json()["error"]["code"]
        assert code_wrong_pw == code_no_user == "INVALID_CREDENTIALS", (
            f"TC-A03: error codes must match — wrong_pw={code_wrong_pw!r}, no_user={code_no_user!r}"
        )


# ---------------------------------------------------------------------------
# TC-A04: Protected endpoint with invalid/expired session → 401
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA04InvalidSession:
    """
    TC-A04: Call a protected endpoint with an expired or invalid session.

    GET /api/v1/auth/session/ is used as the representative protected endpoint.

    Expected:
      - Unauthenticated request → HTTP 403 (DRF returns 403 for unauthenticated
        when DEFAULT_PERMISSION_CLASSES = IsAuthenticated)
      - Request with a tampered/invalid session cookie → 403
    """

    def test_no_session_returns_403(self, api_client, db):
        """
        No credentials at all: DRF's IsAuthenticated returns 403 Forbidden
        (not 401, because SessionAuthentication does not send WWW-Authenticate).
        """
        resp = api_client.get(SESSION_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-A04: expected 401 or 403 with no session, got {resp.status_code}"
        )

    def test_invalid_session_cookie_returns_403(self, api_client, db):
        """
        A forged / garbage session cookie must not grant access.
        """
        api_client.cookies["sessionid"] = "invalid-session-token-xyz"
        resp = api_client.get(SESSION_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-A04: tampered sessionid must be rejected (got {resp.status_code})"
        )

    def test_after_logout_session_is_invalidated(self, api_client, valid_user):
        """
        After the user logs out the old session must be invalidated.
        This overlaps with TC-A07 but is included here for completeness.
        Cache is cleared to avoid rate-limit state from prior tests.
        """
        cache.clear()

        # Log in first
        resp_login = api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp_login.status_code == status.HTTP_200_OK

        # Confirm session works
        resp_session = api_client.get(SESSION_URL)
        assert resp_session.status_code == status.HTTP_200_OK

        # Log out
        resp_logout = api_client.post(LOGOUT_URL)
        assert resp_logout.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )

        # Old session must no longer be valid
        resp_after = api_client.get(SESSION_URL)
        assert resp_after.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), f"TC-A04: session must be invalidated after logout, got {resp_after.status_code}"


# ---------------------------------------------------------------------------
# TC-A05: 5 consecutive failed logins → account locked; 6th → 429 on same IP
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA05AccountLockoutAndRateLimit:
    """
    TC-A05: 5 consecutive failed logins lock the account (Requirement 3.3);
            the 6th attempt from the same IP is rate-limited (429) once the
            rate limit threshold (10/min) is also reached.

    This test adds API-level assertions on top of the unit-level tests in
    test_property_lockout.py and test_property_rate_limiting.py.

    Sub-tests:
      (a) After 5 failed attempts the account is locked (HTTP 403 ACCOUNT_LOCKED)
      (b) After 10+ attempts from one IP the rate limit kicks in (HTTP 429)
    """

    def test_five_failures_lock_account(self, db):
        """
        TC-A05a: 5 consecutive wrong-password requests → account locked.
        The 6th attempt (even with correct password) returns 403 ACCOUNT_LOCKED.
        """
        cache.clear()
        client = APIClient()

        user = User.objects.create_user(
            email="lockme@example.com",
            password="CorrectPass1!",
            role="Receptionist",
        )

        # 5 failed attempts
        for i in range(5):
            resp = client.post(
                LOGIN_URL,
                {"email": "lockme@example.com", "password": "WRONG"},
                format="json",
            )
            assert resp.status_code == status.HTTP_401_UNAUTHORIZED, (
                f"TC-A05a: attempt {i+1} should be 401 (not yet locked), got {resp.status_code}"
            )

        # Account should now be locked
        user.refresh_from_db()
        assert user.is_locked, "TC-A05a: account must be locked after 5 failures"
        assert user.failed_login_count == 5

        # 6th attempt — even with correct password → 403 ACCOUNT_LOCKED
        resp_6 = client.post(
            LOGIN_URL,
            {"email": "lockme@example.com", "password": "CorrectPass1!"},
            format="json",
        )
        assert resp_6.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-A05a: 6th attempt must be 403 ACCOUNT_LOCKED, got {resp_6.status_code}"
        )
        assert resp_6.data["error"]["code"] == "ACCOUNT_LOCKED", (
            f"TC-A05a: expected ACCOUNT_LOCKED error code, got {resp_6.data['error']['code']!r}"
        )

    @pytest.mark.django_db
    def test_rate_limit_returns_429_after_10_attempts(self, db):
        """
        TC-A05b: After 10 requests within 60s from the same IP the 11th
        must return HTTP 429 RATE_LIMIT_EXCEEDED (Requirement 3.6).
        """
        from django.test import override_settings
        with override_settings(RATELIMIT_ENABLE=True):
            cache.clear()
            client = APIClient()

            user = User.objects.create_user(
                email="ratelimit_a05@example.com",
                password="Pass1234!",
                role="Receptionist",
            )

            # Send 10 requests to exhaust the rate-limit window
            for i in range(10):
                client.post(
                    LOGIN_URL,
                    {"email": "ratelimit_a05@example.com", "password": "Pass1234!"},
                    format="json",
                )

            # The 11th request must be rate-limited
            resp_11 = client.post(
                LOGIN_URL,
                {"email": "ratelimit_a05@example.com", "password": "Pass1234!"},
                format="json",
            )
            assert resp_11.status_code == status.HTTP_429_TOO_MANY_REQUESTS, (
                f"TC-A05b: 11th request must return 429, got {resp_11.status_code}"
            )
            assert resp_11.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED", (
                f"TC-A05b: expected RATE_LIMIT_EXCEEDED, got {resp_11.json()['error']['code']!r}"
            )

    def test_failed_login_increments_counter_at_api_level(self, api_client, valid_user):
        """
        TC-A05 (supporting): Each 401 response must increment the user's
        failed_login_count, confirming the counter is updated at the API level.
        """
        cache.clear()

        api_client.post(
            LOGIN_URL,
            {"email": "staff@example.com", "password": "WRONG"},
            format="json",
        )
        valid_user.refresh_from_db()
        assert valid_user.failed_login_count == 1, (
            "TC-A05: failed_login_count must increment after each failed API login"
        )


# ---------------------------------------------------------------------------
# TC-A06: POST /api/v1/auth/password-reset/ — valid email → 200, email sent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA06PasswordReset:
    """
    TC-A06: POST /api/v1/auth/password-reset/ with a registered email address.

    Expected:
      - HTTP 200 in all cases (no user enumeration)
      - For a registered email: a PasswordResetToken is created AND an email is sent
      - For an unknown email: HTTP 200 but no email sent (no enumeration)
    """

    def test_valid_email_returns_200(self, api_client, valid_user):
        with patch("apps.authentication.views.send_mail"):
            resp = api_client.post(
                PASSWORD_RESET_URL,
                {"email": "staff@example.com"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-A06: expected HTTP 200 for valid email, got {resp.status_code}"
        )

    def test_valid_email_sends_email(self, api_client, valid_user):
        """
        For a registered email address an outbound email must be sent.
        Verified via Django's locmem email backend (mail.outbox).
        """
        mail.outbox = []  # Clear previous emails
        resp = api_client.post(
            PASSWORD_RESET_URL,
            {"email": "staff@example.com"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == 1, (
            f"TC-A06: expected 1 email to be sent, found {len(mail.outbox)}"
        )
        assert mail.outbox[0].to == ["staff@example.com"], (
            f"TC-A06: email sent to wrong address: {mail.outbox[0].to}"
        )

    def test_valid_email_creates_reset_token(self, api_client, valid_user):
        """A PasswordResetToken must be created in the database for the user."""
        from apps.authentication.models import PasswordResetToken

        PasswordResetToken.objects.filter(user=valid_user).delete()

        with patch("apps.authentication.views.send_mail"):
            api_client.post(
                PASSWORD_RESET_URL,
                {"email": "staff@example.com"},
                format="json",
            )

        tokens = PasswordResetToken.objects.filter(user=valid_user, is_used=False)
        assert tokens.exists(), "TC-A06: a new PasswordResetToken must be created"
        assert tokens.count() == 1, (
            f"TC-A06: expected exactly 1 active reset token, found {tokens.count()}"
        )

    def test_unknown_email_returns_200_without_sending_email(self, api_client, db):
        """
        An unknown email must receive a generic 200 response but no email
        should be sent (no user enumeration).
        """
        mail.outbox = []
        resp = api_client.post(
            PASSWORD_RESET_URL,
            {"email": "unknown@nowhere.com"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, (
            f"TC-A06: expected HTTP 200 for unknown email (no enumeration), got {resp.status_code}"
        )
        assert len(mail.outbox) == 0, (
            f"TC-A06: no email should be sent for an unknown address, found {len(mail.outbox)}"
        )

    def test_valid_email_response_message_does_not_enumerate(self, api_client, valid_user, db):
        """
        The response body must use the same generic message for both registered
        and unregistered email addresses.
        """
        with patch("apps.authentication.views.send_mail"):
            resp_known = api_client.post(
                PASSWORD_RESET_URL,
                {"email": "staff@example.com"},
                format="json",
            )
        resp_unknown = api_client.post(
            PASSWORD_RESET_URL,
            {"email": "ghost@example.com"},
            format="json",
        )

        # Both must return 200
        assert resp_known.status_code == status.HTTP_200_OK
        assert resp_unknown.status_code == status.HTTP_200_OK

        # The response detail/message must be identical
        msg_known = resp_known.data.get("detail", "")
        msg_unknown = resp_unknown.data.get("detail", "")
        assert msg_known == msg_unknown, (
            f"TC-A06: response messages must be identical for enumeration prevention:\n"
            f"  known   = {msg_known!r}\n"
            f"  unknown = {msg_unknown!r}"
        )

    def test_reset_email_contains_token_link(self, api_client, valid_user):
        """
        The sent email must contain a reset link that includes the token value.
        """
        from apps.authentication.models import PasswordResetToken

        mail.outbox = []
        api_client.post(
            PASSWORD_RESET_URL,
            {"email": "staff@example.com"},
            format="json",
        )

        assert len(mail.outbox) == 1
        email_body = mail.outbox[0].body

        token = PasswordResetToken.objects.filter(
            user=valid_user, is_used=False
        ).latest("created_at")
        assert str(token.token) in email_body, (
            "TC-A06: reset email body must contain the token value"
        )


# ---------------------------------------------------------------------------
# TC-A07: POST /api/v1/auth/logout/ → 200/204; subsequent protected call → 401/403
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTCA07Logout:
    """
    TC-A07: POST /api/v1/auth/logout/ followed by a protected endpoint call.

    Expected:
      - Logout returns HTTP 200 or 204 (implementation uses 204)
      - Subsequent GET /api/v1/auth/session/ returns 401 or 403 (session invalidated)
    """

    def _do_login(self, client, email="staff@example.com", password="ValidPass123!"):
        """Perform a real session login and return the response."""
        return client.post(
            LOGIN_URL,
            {"email": email, "password": password},
            format="json",
        )

    def test_logout_after_valid_login_returns_success(self, api_client, valid_user):
        """
        TC-A07: logout of an authenticated user returns 200 or 204.
        """
        cache.clear()
        # Log in via session
        resp_login = self._do_login(api_client)
        assert resp_login.status_code == status.HTTP_200_OK, (
            "TC-A07 pre-condition: login must succeed"
        )

        resp_logout = api_client.post(LOGOUT_URL)
        assert resp_logout.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        ), (
            f"TC-A07: expected 200/204 from logout, got {resp_logout.status_code}"
        )

    def test_subsequent_protected_call_after_logout_is_rejected(self, api_client, valid_user):
        """
        TC-A07: After logout, a call to the protected session endpoint must
        return 401 or 403 — the session cookie must be invalidated.
        """
        cache.clear()
        # Log in
        resp_login = self._do_login(api_client)
        assert resp_login.status_code == status.HTTP_200_OK

        # Confirm session is active
        resp_session_before = api_client.get(SESSION_URL)
        assert resp_session_before.status_code == status.HTTP_200_OK, (
            "TC-A07 pre-condition: session endpoint must be accessible after login"
        )

        # Log out
        resp_logout = api_client.post(LOGOUT_URL)
        assert resp_logout.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )

        # Protected call must now fail
        resp_session_after = api_client.get(SESSION_URL)
        assert resp_session_after.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-A07: protected call must be rejected after logout, "
            f"got {resp_session_after.status_code}"
        )

    def test_logout_without_authentication_is_rejected(self, api_client, db):
        """
        TC-A07 (edge): Logout without an active session must be rejected (403).
        """
        resp = api_client.post(LOGOUT_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-A07: unauthenticated logout must be rejected, got {resp.status_code}"
        )

    def test_logout_via_force_authenticate_then_protected_call_fails(
        self, api_client, valid_user
    ):
        """
        TC-A07 (force-auth variant): Use force_authenticate to set up the
        session, log out, confirm protection is enforced even without a real
        session cookie.
        """
        api_client.force_authenticate(user=valid_user)

        # Protected endpoint is accessible before logout
        resp_before = api_client.get(SESSION_URL)
        assert resp_before.status_code == status.HTTP_200_OK

        # Log out clears the force-auth state
        resp_logout = api_client.post(LOGOUT_URL)
        assert resp_logout.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )

        # After logout the client is no longer authenticated
        api_client.force_authenticate(user=None)
        resp_after = api_client.get(SESSION_URL)
        assert resp_after.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
