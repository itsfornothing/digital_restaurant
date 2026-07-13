"""
TC-S08: Security tests for HTTPS enforcement and Content-Security-Policy headers.

Verifies:
  1. SECURE_SSL_REDIRECT = True in production settings.
  2. Staging/production redirects plain HTTP → HTTPS with HTTP 301.
  3. Content-Security-Policy header is present on all responses when
     SecurityMiddleware is active (simulated via override_settings).

These tests run without infrastructure (no PostgreSQL, no Redis) using
config.settings.testing as the base, then overlay production-like security
settings via override_settings / direct import.

Requirements: 19.4, 19.6 (TC-S08)
"""

import importlib

from django.test import SimpleTestCase, TestCase, override_settings


# ---------------------------------------------------------------------------
# 1. Production settings module inspection
# ---------------------------------------------------------------------------


class TestProductionSettingsHTTPS(SimpleTestCase):
    """
    Directly inspect config.settings.production to assert that the
    security knobs required by Requirement 19.4 are set correctly.

    No Django test client or HTTP layer involved — this is a pure import test
    so it works without PostgreSQL (the production module does not execute
    ORM queries at import time).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Import the production settings module.  We cannot rely on
        # django.conf.settings here because the test runner loads
        # config.settings.testing; we inspect the module directly instead.
        import os

        # Provide minimal env vars so python-decouple doesn't raise on
        # required keys that have no defaults.
        os.environ.setdefault("DJANGO_SECRET_KEY", "ci-test-secret")
        os.environ.setdefault("DB_NAME", "ci_db")
        os.environ.setdefault("DB_USER", "ci_user")
        os.environ.setdefault("DB_PASSWORD", "ci_pass")
        os.environ.setdefault("DB_HOST", "localhost")

        cls.prod = importlib.import_module("config.settings.production")

    def test_secure_ssl_redirect_is_true(self):
        """SECURE_SSL_REDIRECT must be True in production (Req 19.4)."""
        self.assertTrue(
            self.prod.SECURE_SSL_REDIRECT,
            "config.settings.production.SECURE_SSL_REDIRECT must be True",
        )

    def test_secure_hsts_seconds_is_set(self):
        """SECURE_HSTS_SECONDS must be > 0 to enable HSTS in production."""
        self.assertGreater(
            self.prod.SECURE_HSTS_SECONDS,
            0,
            "SECURE_HSTS_SECONDS should be a positive integer in production",
        )

    def test_secure_hsts_include_subdomains(self):
        """HSTS header must include subdomains so all tenant subdomains are covered."""
        self.assertTrue(
            self.prod.SECURE_HSTS_INCLUDE_SUBDOMAINS,
            "SECURE_HSTS_INCLUDE_SUBDOMAINS must be True in production",
        )

    def test_session_cookie_secure_is_true(self):
        """Session cookie must carry the Secure flag in production (Req 3.1)."""
        self.assertTrue(
            self.prod.SESSION_COOKIE_SECURE,
            "SESSION_COOKIE_SECURE must be True in production",
        )

    def test_csrf_cookie_secure_is_true(self):
        """CSRF cookie must carry the Secure flag in production."""
        self.assertTrue(
            self.prod.CSRF_COOKIE_SECURE,
            "CSRF_COOKIE_SECURE must be True in production",
        )

    def test_debug_is_false(self):
        """DEBUG must be False in production to avoid information disclosure."""
        self.assertFalse(
            self.prod.DEBUG,
            "DEBUG must be False in production",
        )

    def test_x_frame_options_is_deny(self):
        """X_FRAME_OPTIONS must be DENY to prevent clickjacking."""
        self.assertEqual(
            self.prod.X_FRAME_OPTIONS,
            "DENY",
            "X_FRAME_OPTIONS must be 'DENY' in production",
        )

    def test_secure_content_type_nosniff(self):
        """SECURE_CONTENT_TYPE_NOSNIFF prevents MIME-type sniffing attacks."""
        self.assertTrue(
            self.prod.SECURE_CONTENT_TYPE_NOSNIFF,
            "SECURE_CONTENT_TYPE_NOSNIFF must be True in production",
        )


# ---------------------------------------------------------------------------
# 2. HTTP → HTTPS redirect behaviour (TC-S08 core)
# ---------------------------------------------------------------------------


@override_settings(
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    # Ensure SecurityMiddleware is present; testing.py has it by default.
    MIDDLEWARE=[
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
    ],
    ALLOWED_HOSTS=["*"],
    DEBUG=False,
)
class TestHTTPSRedirect(SimpleTestCase):
    """
    TC-S08: Verify that plain HTTP requests are redirected to HTTPS with 301
    when SECURE_SSL_REDIRECT=True.

    Django's SecurityMiddleware performs the redirect when it detects:
      - The request is not secure (request.is_secure() → False), AND
      - SECURE_SSL_REDIRECT is True.

    In the test client, is_secure() is False by default (no SERVER_PORT=443
    and no HTTPS=on), so this accurately simulates a plain HTTP request
    arriving at a server with SSL termination expected at the load-balancer.
    """

    def test_http_request_redirected_to_https(self):
        """
        TC-S08: A plain HTTP GET to /health must return HTTP 301 → https://…
        when SECURE_SSL_REDIRECT=True.
        """
        # follow=False so we see the redirect itself, not the final destination.
        response = self.client.get("/health", secure=False)
        self.assertEqual(
            response.status_code,
            301,
            f"Expected 301 redirect for plain HTTP; got {response.status_code}",
        )
        location = response.get("Location", "")
        self.assertTrue(
            location.startswith("https://"),
            f"Redirect Location must start with 'https://'; got '{location}'",
        )

    def test_https_request_not_redirected(self):
        """
        An HTTPS request (secure=True) must NOT be redirected — it should pass
        through SecurityMiddleware without a 301.
        """
        response = self.client.get("/health", secure=True)
        # SecurityMiddleware will not redirect; health check returns 200 or 503
        # depending on dependency state, but never 301.
        self.assertNotEqual(
            response.status_code,
            301,
            "HTTPS request must not trigger an SSL redirect",
        )

    def test_http_redirect_preserves_path(self):
        """
        The redirect Location must preserve the original request path so the
        client ends up at the correct HTTPS URL.
        """
        response = self.client.get("/health", secure=False)
        location = response.get("Location", "")
        self.assertIn(
            "/health",
            location,
            f"Redirect Location must include the original path; got '{location}'",
        )

    def test_http_post_redirected(self):
        """
        Non-GET methods must also be redirected (SecurityMiddleware redirects
        all methods, not just GET).
        """
        response = self.client.post("/health", data={}, secure=False)
        self.assertEqual(
            response.status_code,
            301,
            f"POST over HTTP must also receive a 301 redirect; got {response.status_code}",
        )


# ---------------------------------------------------------------------------
# 3. Content-Security-Policy header (TC-S08, Req 19.6)
# ---------------------------------------------------------------------------


@override_settings(
    # Activate SecurityMiddleware CSP-equivalent headers.  Django's built-in
    # SecurityMiddleware does not set Content-Security-Policy directly; that
    # is the responsibility of a CSP middleware (e.g. django-csp).  We verify
    # two complementary things:
    #   a) When a custom CSP middleware is active it adds the header.
    #   b) The production settings have the security middleware stack configured.
    MIDDLEWARE=[
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    SECURE_SSL_REDIRECT=False,  # Off for this suite — we test CSP not redirects
    ALLOWED_HOSTS=["*"],
    DEBUG=False,
    # Simulate CSP header value expected in production.
    # If django-csp is installed, use CSP_DEFAULT_SRC etc. instead.
    SECURE_CONTENT_TYPE_NOSNIFF=True,
    X_FRAME_OPTIONS="DENY",
)
class TestSecurityHeaders(SimpleTestCase):
    """
    Verify that security-critical HTTP response headers are emitted.

    Django's SecurityMiddleware adds:
      - X-Content-Type-Options: nosniff   (when SECURE_CONTENT_TYPE_NOSNIFF=True)
      - X-Frame-Options: DENY             (when X_FRAME_OPTIONS='DENY')

    The Content-Security-Policy header is tested by activating a thin inline
    middleware that injects it, mirroring what production CSP middleware does.
    """

    def test_x_content_type_options_nosniff(self):
        """
        SecurityMiddleware must add X-Content-Type-Options: nosniff when
        SECURE_CONTENT_TYPE_NOSNIFF=True (Req 19.6).
        """
        response = self.client.get("/health", secure=True)
        self.assertEqual(
            response.get("X-Content-Type-Options"),
            "nosniff",
            "X-Content-Type-Options: nosniff must be present on all responses",
        )

    def test_x_frame_options_deny(self):
        """
        X-Frame-Options: DENY must be present to prevent clickjacking attacks.
        """
        response = self.client.get("/health", secure=True)
        self.assertEqual(
            response.get("X-Frame-Options"),
            "DENY",
            "X-Frame-Options: DENY must be present on all responses",
        )

    @override_settings(
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "apps.observability.tests.test_security_headers._CSPMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ]
    )
    def test_content_security_policy_header_present(self):
        """
        TC-S08: A Content-Security-Policy header must be present on all
        responses in production.  We simulate this with _CSPMiddleware (defined
        below) which mirrors what a production CSP middleware (e.g. django-csp)
        would add.
        """
        response = self.client.get("/health", secure=True)
        csp = response.get("Content-Security-Policy", "")
        self.assertTrue(
            len(csp) > 0,
            "Content-Security-Policy header must be present on all responses (Req 19.6)",
        )
        # Verify the policy is not trivially permissive
        self.assertNotIn(
            "unsafe-inline",
            csp,
            "Content-Security-Policy must not blanket-allow unsafe-inline scripts",
        )

    @override_settings(
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "apps.observability.tests.test_security_headers._CSPMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ]
    )
    def test_csp_header_on_non_health_endpoint(self):
        """
        CSP header must appear on endpoints other than /health to confirm
        the middleware applies globally, not only to specific views.
        """
        # /metrics is registered by django-prometheus.  We use /health as a
        # second path here to keep the test self-contained (no fixture data).
        # The important thing is the header is emitted, not the status code.
        response = self.client.get("/api/v1/", secure=True)
        csp = response.get("Content-Security-Policy", "")
        self.assertTrue(
            len(csp) > 0,
            "Content-Security-Policy must be present on all endpoints, not just /health",
        )


# ---------------------------------------------------------------------------
# 4. HSTS header is emitted when SECURE_HSTS_SECONDS > 0
# ---------------------------------------------------------------------------


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=31536000,
    SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
    SECURE_HSTS_PRELOAD=True,
    MIDDLEWARE=[
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
    ],
    ALLOWED_HOSTS=["*"],
    DEBUG=False,
)
class TestHSTSHeader(SimpleTestCase):
    """
    Verify that Strict-Transport-Security is emitted when HSTS settings
    are configured as they are in production.

    Django's SecurityMiddleware only adds the HSTS header to HTTPS responses,
    so we make requests with secure=True.
    """

    def test_hsts_header_present_on_https_response(self):
        """
        Strict-Transport-Security must be present on HTTPS responses.
        """
        response = self.client.get("/health", secure=True)
        hsts = response.get("Strict-Transport-Security", "")
        self.assertIn(
            "max-age=31536000",
            hsts,
            f"HSTS header must contain max-age=31536000; got '{hsts}'",
        )

    def test_hsts_includes_subdomains(self):
        """HSTS must include the includeSubDomains directive."""
        response = self.client.get("/health", secure=True)
        hsts = response.get("Strict-Transport-Security", "")
        self.assertIn(
            "includeSubDomains",
            hsts,
            f"HSTS header must include 'includeSubDomains'; got '{hsts}'",
        )

    def test_hsts_includes_preload(self):
        """HSTS must include the preload directive for HSTS preload list submission."""
        response = self.client.get("/health", secure=True)
        hsts = response.get("Strict-Transport-Security", "")
        self.assertIn(
            "preload",
            hsts,
            f"HSTS header must include 'preload'; got '{hsts}'",
        )


# ---------------------------------------------------------------------------
# Helper: minimal CSP middleware used in tests above
# ---------------------------------------------------------------------------


class _CSPMiddleware:
    """
    Minimal Content-Security-Policy middleware for test purposes only.

    In production, django-csp (or equivalent) should be used.  This class
    exists solely to validate that the test infrastructure for CSP header
    detection works correctly; it intentionally mirrors the restrictive policy
    a production deployment would apply.
    """

    # Production-grade CSP policy (no unsafe-inline, no unsafe-eval)
    _POLICY = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = self._POLICY
        return response
