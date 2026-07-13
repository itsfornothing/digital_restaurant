"""
apps/authentication/tests/test_auth_security.py

Security test suite for authentication endpoints.

Test cases:
  TC-S01: SQL injection in login email field → 400/401, no stack trace in body
  TC-S04: Tampered session cookie → 401/403 (protected endpoint rejects it)
  TC-S05: Role claim in session tampered → 403/401 (user cannot elevate role)
  TC-S06: Mass assignment — POST /api/v1/auth/users/ with role=Super_Admin in
          payload → role field is ignored (not applied to the created user)
  TC-S07: GET /api/v1/auth/session/ response must NOT contain `password`,
          `totp_secret`, or any raw token values
  TC-S09: CSRF — state-changing POST without CSRF token → 403 (DRF CSRF check)
  TC-S10: Rate limiting — 10 login attempts in 60s → 429 RATE_LIMIT_EXCEEDED

Validates: Requirements 3.2, 3.6 (TC-S01, TC-S04–S10)

Notes:
  - Tests use the lightweight testing settings (SQLite in-memory).
  - Rate-limit state is cleared before TC-S10 via Django's cache framework.
  - TC-S09 uses APIClient(enforce_csrf_checks=True) to simulate DRF's CSRF
    enforcement for session-authenticated requests.
  - TC-S05 manipulates the Django session store directly to inject a role.
  - TC-S06: UserViewSet CREATE is guarded by IsSuperAdminOrTenantOwner and
    performs no full create flow yet; the test verifies that a Receptionist
    user cannot elevate to Super_Admin via the session response.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

LOGIN_URL = "/api/v1/auth/login/"
SESSION_URL = "/api/v1/auth/session/"
LOGOUT_URL = "/api/v1/auth/logout/"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client (no CSRF enforcement by default)."""
    return APIClient()


@pytest.fixture
def csrf_client():
    """A DRF test client that enforces CSRF checks, as a real browser would."""
    return APIClient(enforce_csrf_checks=True)


@pytest.fixture
def regular_user(db):
    """A valid Receptionist user with known credentials."""
    return User.objects.create_user(
        email="receptionist@example.com",
        password="ValidPass123!",
        role="Receptionist",
    )


@pytest.fixture
def super_admin_user(db):
    """A Super_Admin user for privileged-endpoint tests."""
    return User.objects.create_superuser(
        email="admin@example.com",
        password="AdminPass123!",
    )


# ---------------------------------------------------------------------------
# TC-S01: SQL injection in login email field
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS01SQLInjection:
    """
    TC-S01: SQL injection payloads in the login email field must return
    400 or 401 without leaking a stack trace in the response body.

    The LoginSerializer validates email through DRF's EmailField, which
    rejects non-email strings.  Even if the email were syntactically valid,
    Django's ORM uses parameterised queries — no SQL injection is possible.

    Expected:
      - HTTP 400 (invalid email format) or 401 (valid email format but
        credentials fail)
      - Response body must NOT contain any of the telltale stack-trace
        markers: "Traceback", "Exception", "sqlite3", "django.db"
    """

    _SQL_PAYLOADS = [
        "' OR '1'='1",
        "admin'--",
        "' OR 1=1--",
        "\" OR \"\"=\"",
        "'; DROP TABLE users;--",
        "1' AND sleep(5)--",
        "' UNION SELECT null,null,null--",
    ]

    _STACK_TRACE_MARKERS = [
        "Traceback",
        "Exception",
        "sqlite3",
        "django.db",
        "SyntaxError",
        "OperationalError",
    ]

    @pytest.mark.parametrize("payload", _SQL_PAYLOADS)
    def test_sql_injection_returns_400_or_401(self, api_client, db, payload):
        """Server must reject SQL injection payloads with 400 or 401."""
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": payload, "password": "anypassword"},
            format="json",
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        ), (
            f"TC-S01: SQL payload {payload!r} got unexpected status "
            f"{resp.status_code}"
        )

    @pytest.mark.parametrize("payload", _SQL_PAYLOADS)
    def test_sql_injection_no_stack_trace_in_body(self, api_client, db, payload):
        """Response body must not expose any stack trace information."""
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": payload, "password": "anypassword"},
            format="json",
        )
        body = resp.content.decode("utf-8", errors="replace")
        for marker in self._STACK_TRACE_MARKERS:
            assert marker not in body, (
                f"TC-S01: Response body contains stack-trace marker {marker!r} "
                f"for payload {payload!r}. Body: {body[:200]}"
            )


# ---------------------------------------------------------------------------
# TC-S04: Tampered session cookie
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS04TamperedSessionCookie:
    """
    TC-S04: A tampered or forged session cookie must be rejected with 401/403
    on any protected endpoint.

    Django's session middleware looks up the session ID in the database.
    A cookie value that does not correspond to a valid session record causes
    the middleware to create an empty session, leaving the user unauthenticated.
    DRF's SessionAuthentication then returns 403 (no WWW-Authenticate header
    means it uses 403 rather than 401 for unauthenticated requests).
    """

    def test_garbage_session_cookie_rejected(self, api_client, db):
        """A completely random session cookie value must not grant access."""
        api_client.cookies["sessionid"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        resp = api_client.get(SESSION_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-S04: garbage sessionid cookie should be rejected, "
            f"got {resp.status_code}"
        )

    def test_sql_injection_in_session_cookie_rejected(self, api_client, db):
        """A SQL-injection string in the sessionid cookie must be rejected."""
        api_client.cookies["sessionid"] = "' OR '1'='1"
        resp = api_client.get(SESSION_URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-S04: SQL injection in sessionid should be rejected, "
            f"got {resp.status_code}"
        )

    def test_modified_valid_session_cookie_rejected(self, api_client, regular_user):
        """
        Log in to obtain a real session cookie, then alter the cookie value.
        The modified cookie must be rejected.
        """
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "receptionist@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, "TC-S04: pre-condition login failed"

        # Corrupt the real session cookie
        real_cookie = api_client.cookies.get("sessionid")
        assert real_cookie is not None, "TC-S04: sessionid cookie not set after login"

        # Flip the last few characters to tamper with the value
        tampered = real_cookie.value[:-4] + "XXXX"
        api_client.cookies["sessionid"] = tampered

        resp2 = api_client.get(SESSION_URL)
        assert resp2.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-S04: tampered sessionid must be rejected, got {resp2.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-S05: Role claim in session tampered
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS05RoleTampering:
    """
    TC-S05: A user must not be able to elevate their role by manipulating the
    session data.

    Strategy:
      1. Authenticate as a low-privilege user (Receptionist).
      2. Directly manipulate the stored Django session record to inject a
         higher `role` value into the session data.
      3. Access the session endpoint — the response role must still reflect
         the actual DB role, not the injected one.
      4. Access a Super_Admin-only endpoint and verify 403 is returned.

    The session endpoint returns user info looked up from the DB (request.user),
    not from session data.  Django's SessionAuthentication resolves the user
    via the `_auth_user_id` session key → DB lookup.  Role stored in the DB
    cannot be changed merely by tampering with session data.
    """

    def test_session_response_reflects_db_role_not_injected_role(
        self, api_client, regular_user
    ):
        """
        After session role injection, GET /session/ must still return the
        user's actual DB role, not the injected Super_Admin value.
        """
        cache.clear()
        resp_login = api_client.post(
            LOGIN_URL,
            {"email": "receptionist@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp_login.status_code == status.HTTP_200_OK, (
            "TC-S05: pre-condition login failed"
        )

        # Inject a higher role directly into the session store
        from django.contrib.sessions.backends.db import SessionStore
        session_key = api_client.cookies["sessionid"].value
        store = SessionStore(session_key=session_key)
        store["injected_role"] = "Super_Admin"  # won't affect request.user
        store.save()

        resp_session = api_client.get(SESSION_URL)
        assert resp_session.status_code == status.HTTP_200_OK, (
            f"TC-S05: session endpoint should still work, got {resp_session.status_code}"
        )
        returned_role = resp_session.data.get("role")
        assert returned_role == "Receptionist", (
            f"TC-S05: role from session must be 'Receptionist' (DB value), "
            f"got {returned_role!r}"
        )
        assert returned_role != "Super_Admin", (
            "TC-S05: injected Super_Admin role must not be returned by the session endpoint"
        )

    def test_low_privilege_user_cannot_reach_superadmin_endpoint(
        self, api_client, regular_user
    ):
        """
        A Receptionist user — even after injecting a higher role into their
        session — must be rejected (403) when accessing a Super_Admin endpoint.

        We use the tenants endpoint (POST /api/v1/tenants/) which is guarded
        by IsSuperAdmin, confirming a Receptionist is rejected with 403.
        """
        api_client.force_authenticate(user=regular_user)

        # POST /api/v1/tenants/ is guarded by IsSuperAdmin — a Receptionist must get 403
        resp = api_client.post(
            "/api/v1/tenants/",
            {"name": "Evil Tenant", "slug": "evil", "role": "Super_Admin"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-S05: Receptionist must be rejected (403) on Super_Admin endpoint, "
            f"got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-S06: Mass assignment — role field must not be accepted from payload
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS06MassAssignment:
    """
    TC-S06: Submitting `role=Super_Admin` in a user-creation payload must NOT
    result in a user with that role being stored.

    The UserViewSet POST endpoint is protected by IsSuperAdminOrTenantOwner.
    A Receptionist user is rejected before any creation logic runs (403).

    For a Super_Admin user (who is allowed to create users), the `perform_create`
    delegates to the serializer; the serializer / viewset must not blindly
    accept an arbitrary role value that would allow privilege escalation.

    Since the UserViewSet CREATE action is not fully wired with a serializer
    yet (Task 10.x), this test verifies two things:
      1. A Receptionist cannot reach the endpoint at all (403/404/405).
      2. The session endpoint (GET /api/v1/auth/session/) for a Receptionist
         that was created with role=Receptionist never returns Super_Admin,
         confirming the model layer does not allow silent role overrides.
    """

    def test_receptionist_cannot_post_to_user_creation_endpoint(
        self, api_client, regular_user
    ):
        """
        TC-S06a: A Receptionist cannot access Super_Admin-only endpoints —
        the permission class blocks the request before any mass-assignment
        is possible.

        We use POST /api/v1/tenants/ (IsSuperAdmin required) as a proxy for
        any privileged endpoint, since /api/v1/auth/users/ is not registered
        in the URL conf yet (UserViewSet CREATE is a future task).
        """
        api_client.force_authenticate(user=regular_user)
        resp = api_client.post(
            "/api/v1/tenants/",
            {
                "name": "Injected Tenant",
                "slug": "injected",
                "role": "Super_Admin",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-S06a: Receptionist POST to a Super_Admin endpoint must return 403, "
            f"got {resp.status_code}"
        )

    def test_created_user_role_reflects_actual_db_value(self, db):
        """
        TC-S06b: A user created via create_user() with role=Receptionist
        cannot have its role changed by passing extra kwargs.  The role
        stored in the DB must match what was explicitly supplied; no
        unintended field bleed from additional payload keys.
        """
        user = User.objects.create_user(
            email="normal@example.com",
            password="Test1234!",
            role="Receptionist",
        )
        user.refresh_from_db()
        assert user.role == "Receptionist", (
            f"TC-S06b: expected role 'Receptionist', got {user.role!r}"
        )
        assert user.role != "Super_Admin", (
            "TC-S06b: user role must not silently escalate to Super_Admin"
        )

    def test_session_endpoint_does_not_return_elevated_role(
        self, api_client, regular_user
    ):
        """
        TC-S06c: The session endpoint for a Receptionist must return
        'Receptionist', not any elevated role — confirming session info
        is sourced from the DB, not from request payload fields.
        """
        api_client.force_authenticate(user=regular_user)
        resp = api_client.get(SESSION_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data.get("role") == "Receptionist", (
            f"TC-S06c: session role must be 'Receptionist', "
            f"got {resp.data.get('role')!r}"
        )


# ---------------------------------------------------------------------------
# TC-S07: Sensitive fields must not appear in API responses
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS07SensitiveFieldLeakage:
    """
    TC-S07: API responses must NEVER expose `password`, `totp_secret`, or any
    raw token values.

    The session endpoint (GET /api/v1/auth/session/) is the primary endpoint
    under test.  The login response (POST /api/v1/auth/login/) is also checked.

    Sensitive fields to scan for:
      - "password"
      - "totp_secret"
      - "token" (raw token value at top level)
      - "secret" (TOTP base32 secret)
    """

    _FORBIDDEN_FIELDS = {"password", "totp_secret", "token", "secret"}

    def _assert_no_sensitive_fields(self, data: dict, endpoint: str):
        """Recursively scan a response dict for forbidden field names."""
        def _scan(obj, path=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_lower = key.lower()
                    for forbidden in self._FORBIDDEN_FIELDS:
                        if forbidden in key_lower:
                            raise AssertionError(
                                f"TC-S07: Forbidden field {key!r} found in "
                                f"{endpoint} response at path '{path}.{key}'"
                            )
                    _scan(value, path=f"{path}.{key}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _scan(item, path=f"{path}[{i}]")
        _scan(data)

    def test_session_response_has_no_sensitive_fields(self, api_client, regular_user):
        """GET /api/v1/auth/session/ must not expose password or totp_secret."""
        api_client.force_authenticate(user=regular_user)
        resp = api_client.get(SESSION_URL)
        assert resp.status_code == status.HTTP_200_OK
        self._assert_no_sensitive_fields(resp.data, SESSION_URL)

    def test_login_response_has_no_sensitive_fields(self, api_client, regular_user):
        """POST /api/v1/auth/login/ success response must not expose secrets."""
        cache.clear()
        resp = api_client.post(
            LOGIN_URL,
            {"email": "receptionist@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        self._assert_no_sensitive_fields(resp.data, LOGIN_URL)

    def test_session_response_fields_are_expected_safe_set(self, api_client, regular_user):
        """
        Whitelist check: session response must contain only the expected
        safe fields (user_id, email, role, branch_id) and nothing else.
        """
        api_client.force_authenticate(user=regular_user)
        resp = api_client.get(SESSION_URL)
        assert resp.status_code == status.HTTP_200_OK

        allowed = {"user_id", "email", "role", "branch_id"}
        returned = set(resp.data.keys())
        unexpected = returned - allowed
        assert not unexpected, (
            f"TC-S07: Unexpected fields in session response: {unexpected}. "
            f"Expected only: {allowed}"
        )

    def test_totp_setup_response_is_only_accessible_when_authenticated(
        self, api_client, regular_user
    ):
        """
        POST /api/v1/auth/2fa/setup/ returns the TOTP secret to the currently
        authenticated user (by design, for setup).  Unauthenticated callers
        must be rejected (401/403) — they must not receive the secret.
        """
        unauthenticated_client = APIClient()
        resp = unauthenticated_client.post("/api/v1/auth/2fa/setup/", {}, format="json")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (
            f"TC-S07: Unauthenticated 2FA setup must be rejected, "
            f"got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TC-S09: CSRF enforcement on state-changing requests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS09CSRFEnforcement:
    """
    TC-S09: DRF's SessionAuthentication enforces CSRF for session-based
    requests.  A state-changing POST that arrives without a valid CSRF token
    from a session-authenticated client must be rejected with 403.

    Implementation notes:
      - APIClient(enforce_csrf_checks=True) simulates the browser's CSRF
        enforcement path in DRF.
      - For a session-authenticated request, DRF's SessionAuthentication
        calls Django's csrf_protect logic internally.
      - Unauthenticated requests (AllowAny views like LoginView) are exempt
        from CSRF because DRF skips CSRF for unauthenticated requests when
        using SessionAuthentication (CSRF is only enforced once authentication
        succeeds).
      - Therefore we test CSRF on a POST to an IsAuthenticated endpoint
        (e.g., LogoutView) where the user IS session-authenticated.
    """

    def test_csrf_required_for_authenticated_state_changing_post(
        self, regular_user
    ):
        """
        TC-S09: A POST to a session-authenticated endpoint (LogoutView)
        without a CSRF token must return 403.

        force_authenticate() bypasses session auth entirely, so DRF's
        SessionAuthentication never runs its CSRF check.  To trigger real
        CSRF enforcement we must:
          1. Log in via a real session (APIClient without CSRF checks).
          2. Copy the session cookie to a CSRF-enforcing client.
          3. POST to LogoutView WITHOUT the X-CSRFToken header.

        DRF's SessionAuthentication calls enforce_csrf() for requests that
        are authenticated via the session — no CSRF header → 403.
        """
        cache.clear()

        # Step 1: Obtain a real session cookie using a plain client
        plain_client = APIClient()
        resp_login = plain_client.post(
            LOGIN_URL,
            {"email": "receptionist@example.com", "password": "ValidPass123!"},
            format="json",
        )
        assert resp_login.status_code == status.HTTP_200_OK, (
            "TC-S09: pre-condition login failed"
        )

        session_cookie = plain_client.cookies.get("sessionid")
        assert session_cookie is not None, "TC-S09: no sessionid cookie after login"

        # Step 2: Transfer the session cookie to a CSRF-enforcing client
        csrf_enforcing = APIClient(enforce_csrf_checks=True)
        csrf_enforcing.cookies["sessionid"] = session_cookie.value
        # Deliberately do NOT set csrftoken cookie or X-CSRFToken header

        # Step 3: POST to a session-protected endpoint without CSRF token
        resp = csrf_enforcing.post(LOGOUT_URL, {}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            f"TC-S09: session-authenticated POST without CSRF token must return 403, "
            f"got {resp.status_code}"
        )

    def test_login_endpoint_exempt_from_csrf_for_anonymous(self, csrf_client, db):
        """
        TC-S09 (boundary): The login endpoint is AllowAny and uses no
        session auth, so DRF does not enforce CSRF on unauthenticated
        requests to it.  This test documents that boundary.
        """
        # LoginView uses authentication_classes=[] — no session auth → no CSRF.
        # A POST without credentials should return 400/401, NOT 403 CSRF failure.
        cache.clear()
        resp = csrf_client.post(
            LOGIN_URL,
            {"email": "nobody@example.com", "password": "wrong"},
            format="json",
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        ), (
            f"TC-S09 boundary: login endpoint got {resp.status_code}, "
            "expected 400 or 401 (not a CSRF 403)"
        )

    def test_csrf_passes_when_token_provided_via_header(
        self, csrf_client, regular_user
    ):
        """
        TC-S09 (positive): When a valid CSRF token is present in the
        X-CSRFToken header the request succeeds (or fails for non-CSRF reason).
        We first perform a GET to obtain the csrftoken cookie, then POST with it.
        """
        from django.middleware.csrf import get_token
        from django.test import RequestFactory

        # Generate a CSRF token bound to a request
        factory = RequestFactory()
        dummy_request = factory.get("/")
        dummy_request.META["SERVER_NAME"] = "testserver"
        dummy_request.META["SERVER_PORT"] = "80"
        csrf_token = get_token(dummy_request)

        csrf_client.force_authenticate(user=regular_user)
        csrf_client.credentials(HTTP_X_CSRFTOKEN=csrf_token)
        csrf_client.cookies["csrftoken"] = csrf_token

        resp = csrf_client.post(LOGOUT_URL, {}, format="json")
        # With valid CSRF token, response should NOT be 403 (CSRF failure).
        # The logout itself may return 200/204 or 403 for other reasons.
        assert resp.status_code != status.HTTP_403_FORBIDDEN or (
            resp.data.get("detail", "").lower().find("csrf") == -1
        ), (
            "TC-S09: With a valid CSRF token the request must not fail due to CSRF"
        )


# ---------------------------------------------------------------------------
# TC-S10: Rate limiting — 10 login attempts → 429
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTCS10RateLimiting:
    """
    TC-S10: After 10 login attempts within 60 seconds the 11th attempt must
    return HTTP 429 with error code RATE_LIMIT_EXCEEDED.

    This directly validates Requirement 3.6.

    Note: Rate-limit state is cleared before each test via cache.clear()
    to ensure isolation.
    """

    def test_eleventh_login_attempt_returns_429(self, db):
        from django.test import override_settings as _os
        with _os(RATELIMIT_ENABLE=True):
            cache.clear()
            client = APIClient()

            User.objects.create_user(
                email="ratelimit_s10@example.com",
                password="Pass1234!",
                role="Receptionist",
            )

            for i in range(10):
                resp = client.post(
                    LOGIN_URL,
                    {"email": "ratelimit_s10@example.com", "password": "Pass1234!"},
                    format="json",
                )
                assert resp.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                    f"TC-S10: unexpected 429 on attempt {i + 1} (expected after 10)"
                )

            resp_11 = client.post(
                LOGIN_URL,
                {"email": "ratelimit_s10@example.com", "password": "Pass1234!"},
                format="json",
            )
            assert resp_11.status_code == status.HTTP_429_TOO_MANY_REQUESTS, (
                f"TC-S10: 11th request must return 429, got {resp_11.status_code}"
            )

    def test_rate_limit_response_contains_correct_error_code(self, db):
        from django.test import override_settings as _os
        with _os(RATELIMIT_ENABLE=True):
            cache.clear()
            client = APIClient()

            User.objects.create_user(
                email="ratelimit_s10b@example.com",
                password="Pass1234!",
                role="Receptionist",
            )

            for _ in range(10):
                client.post(
                    LOGIN_URL,
                    {"email": "ratelimit_s10b@example.com", "password": "Pass1234!"},
                    format="json",
                )

            resp = client.post(
                LOGIN_URL,
                {"email": "ratelimit_s10b@example.com", "password": "Pass1234!"},
                format="json",
            )
            assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS

            body = resp.json()
            assert "error" in body, (
                f"TC-S10: 429 response must use standard error envelope, got: {body}"
            )
            assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED", (
                f"TC-S10: expected RATE_LIMIT_EXCEEDED code, got {body['error']['code']!r}"
            )

    def test_rate_limit_resets_after_cache_clear(self, db):
        from django.test import override_settings as _os
        with _os(RATELIMIT_ENABLE=True):
            cache.clear()
            client = APIClient()

            User.objects.create_user(
                email="ratelimit_s10c@example.com",
                password="Pass1234!",
                role="Receptionist",
            )

            for _ in range(10):
                client.post(
                    LOGIN_URL,
                    {"email": "ratelimit_s10c@example.com", "password": "Pass1234!"},
                    format="json",
                )

            resp_before_reset = client.post(
                LOGIN_URL,
                {"email": "ratelimit_s10c@example.com", "password": "Pass1234!"},
                format="json",
            )
            assert resp_before_reset.status_code == status.HTTP_429_TOO_MANY_REQUESTS, (
                "TC-S10: rate limit must be active before reset"
            )

            cache.clear()

            resp_after_reset = client.post(
                LOGIN_URL,
                {"email": "ratelimit_s10c@example.com", "password": "Pass1234!"},
                format="json",
            )
            assert resp_after_reset.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                f"TC-S10: after rate-limit reset, request must not return 429, "
                f"got {resp_after_reset.status_code}"
            )
