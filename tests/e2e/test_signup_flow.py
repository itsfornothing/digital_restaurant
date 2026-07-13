"""
tests/e2e/test_signup_flow.py

E2E tests for the manager-provisioned staff account creation flow.

The endpoint requires an authenticated manager/owner session and uses an
invite-based flow (no password set at creation).
"""

import uuid

import pytest
from django.test import Client
from rest_framework.test import APIClient


# =============================================================================
# E2E via Django test Client
# =============================================================================

@pytest.mark.django_db
class TestSignupFlowViaClient:
    """
    Manager-provisioned staff account creation via Django's test Client.

    The /staff/register/ page requires:
      - Authenticated session (Branch_Manager / Tenant_Owner / Super_Admin)
      - POST with email + role (no password)
    """

    REGISTER_URL = "/staff/register/"

    def _auth_client(self, role="Branch_Manager"):
        """Return a Client logged in as a user with the given role."""
        from apps.authentication.models import User

        User.objects.create_user(
            email=f"{role.lower()}-e2e@test.com",
            password="testpass1234",
            role=role,
        )
        client = Client()
        client.force_login(User.objects.get(email=f"{role.lower()}-e2e@test.com"))
        return client

    def test_register_page_redirects_anonymous(self):
        """Unauthenticated users are redirected to login."""
        client = Client()
        resp = client.get(self.REGISTER_URL)
        assert resp.status_code == 302
        assert resp["Location"].startswith("/staff/login/")

    def test_register_page_renders_for_authenticated_manager(self):
        """GET /staff/register/ returns 200 for authenticated manager."""
        client = self._auth_client()
        resp = client.get(self.REGISTER_URL)
        assert resp.status_code == 200
        assert b"Create Account" in resp.content

    def test_non_manager_redirected(self):
        """Staff with non-manager role are redirected away."""
        client = self._auth_client(role="Kitchen_Staff")
        resp = client.get(self.REGISTER_URL)
        assert resp.status_code == 302

    def test_successful_registration_redirects_with_invite_flag(self):
        """POST valid data → 302 redirect to /staff/?invite_sent=1."""
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "newstaff@test.com", "role": "Receptionist"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/staff/?invite_sent=1"

    def test_created_user_has_no_password(self):
        """User created via staff_register has unusable password."""
        from apps.authentication.models import User

        client = self._auth_client()
        client.post(
            self.REGISTER_URL,
            {"email": "invite@test.com", "role": "Kitchen_Staff"},
        )
        user = User.objects.get(email="invite@test.com")
        assert user.role == "Kitchen_Staff"
        assert user.is_active is True
        assert user.has_usable_password() is False

    def test_duplicate_email_shows_error(self):
        """Registering with an existing email returns the form with error."""
        from apps.authentication.models import User

        User.objects.create_user(
            email="dup@test.com", password="testpass123", role="Branch_Manager"
        )
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "dup@test.com", "role": "Receptionist"},
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.content.lower()

    def test_empty_email_returns_error(self):
        """Submitting the form with empty email shows an error."""
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "", "role": "Branch_Manager"},
        )
        assert resp.status_code == 200
        assert b"Email and role are required" in resp.content


# =============================================================================
# E2E via Playwright (browser-based)
# =============================================================================

pytestmark = pytest.mark.e2e


def _playwright_available():
    """Check if pytest-playwright is installed."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _playwright_available(), reason="pytest-playwright not installed")
@pytest.mark.django_db(transaction=True)
class TestSignupFlowViaPlaywright:
    """
    Playwright-based E2E test for the staff registration page.

    Requires:
        - pytest-playwright   (pip install pytest-playwright)
        - playwright browsers (playwright install chromium)
    """

    def _login_as_manager(self, page, live_server):
        """Log into the staff portal as a Branch_Manager."""
        from apps.authentication.models import User

        User.objects.create_user(
            email="playwright-mgr@test.com",
            password="password1234",
            role="Branch_Manager",
        )
        page.goto(f"{live_server.url}/staff/login/")
        page.fill("#email", "playwright-mgr@test.com")
        page.fill("#password", "password1234")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{live_server.url}/staff/**")

    def test_page_renders_in_browser(self, page, live_server):
        """Playwright: /staff/register/ loads with form for manager."""
        self._login_as_manager(page, live_server)
        page.goto(f"{live_server.url}/staff/register/")
        assert "Create Account" in page.content()
        assert page.is_visible("#email")
        assert page.is_visible("#role")

    def test_successful_signup_in_browser(self, page, live_server):
        """Playwright: fill form, submit, verify redirect to dashboard."""
        self._login_as_manager(page, live_server)
        unique_email = f"pw-{uuid.uuid4().hex[:8]}@test.com"

        page.goto(f"{live_server.url}/staff/register/")
        page.fill("#email", unique_email)
        page.select_option("#role", "Branch_Manager")
        page.click('button[type="submit"]')

        page.wait_for_url(f"{live_server.url}/staff/**")
        assert "invite_sent=1" in page.url or page.url.endswith("/staff/")

    def test_duplicate_email_in_browser(self, page, live_server):
        """Playwright: duplicate email shows error."""
        from apps.authentication.models import User

        User.objects.create_user(
            email="dup-pw@test.com", password="testpass123", role="Branch_Manager"
        )
        self._login_as_manager(page, live_server)

        page.goto(f"{live_server.url}/staff/register/")
        page.fill("#email", "dup-pw@test.com")
        page.select_option("#role", "Kitchen_Staff")
        page.click('button[type="submit"]')
        assert page.is_visible("text=already exists")
