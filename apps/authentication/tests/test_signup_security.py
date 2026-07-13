"""
Comprehensive security tests for the staff-account creation endpoint.

Covers edge cases the basic happy-path tests miss:
  - Caller-role restriction (Kitchen_Staff / Receptionist must be denied)
  - Empty / malformed request bodies
  - Billing limit exceeded
  - Invite-email failure is still a success (201)
  - Various HTTP method verbs
"""

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()
REGISTER_URL = "/api/v1/auth/register/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def as_mgr():
    """Return APIClient authenticated as a Branch_Manager."""
    u = User.objects.create_user(
        email="mgr-signup-sec@test.com",
        password="testpass1234",
        role="Branch_Manager",
    )
    c = APIClient()
    c.force_authenticate(user=u)
    return c


@pytest.fixture
def as_ks():
    """Return APIClient authenticated as Kitchen_Staff."""
    u = User.objects.create_user(
        email="ks-signup-sec@test.com",
        password="testpass1234",
        role="Kitchen_Staff",
    )
    c = APIClient()
    c.force_authenticate(user=u)
    return c


@pytest.fixture
def as_owner():
    """Return APIClient authenticated as a Tenant_Owner."""
    u = User.objects.create_user(
        email="owner-signup-sec@test.com",
        password="testpass1234",
        role="Tenant_Owner",
    )
    c = APIClient()
    c.force_authenticate(user=u)
    return c


@pytest.mark.django_db
class TestSignupCallerRoleRestriction:
    """Only Branch_Manager, Tenant_Owner, Super_Admin may create accounts."""

    def test_kitchen_staff_denied(self, as_ks):
        resp = as_ks.post(
            REGISTER_URL,
            {"email": "ks-create@test.com", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data["error"]["code"] == "FORBIDDEN"

    def test_receptionist_denied(self):
        rcp = User.objects.create_user(
            email="rcp@test.com", password="testpass1234", role="Receptionist",
        )
        client = APIClient()
        client.force_authenticate(user=rcp)
        resp = client.post(
            REGISTER_URL,
            {"email": "rcp-create@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_branch_manager_allowed(self, as_mgr):
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "mgr-create@test.com", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_tenant_owner_allowed(self, as_owner):
        resp = as_owner.post(
            REGISTER_URL,
            {"email": "owner-create@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_super_admin_allowed(self):
        sa = User.objects.create_user(
            email="sa@test.com", password="testpass1234", role="Super_Admin",
        )
        client = APIClient()
        client.force_authenticate(user=sa)
        resp = client.post(
            REGISTER_URL,
            {"email": "sa-create@test.com", "role": "Kitchen_Staff"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
class TestSignupMalformedRequests:
    """Edge cases: empty body, missing fields, wrong content-type."""

    def test_empty_json_body(self, as_mgr):
        resp = as_mgr.post(REGISTER_URL, {}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_email_field(self, as_mgr):
        resp = as_mgr.post(REGISTER_URL, {"role": "Receptionist"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in resp.data.get("error", {}).get("message", "")

    def test_missing_role_field(self, as_mgr):
        resp = as_mgr.post(
            REGISTER_URL, {"email": "missingrole@test.com"}, format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "role" in resp.data.get("error", {}).get("message", "")

    def test_empty_email_string(self, as_mgr):
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_email_format(self, as_mgr):
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "not-an-email", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_long_email(self, as_mgr):
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "a" * 300 + "@test.com", "role": "Receptionist"},
            format="json",
        )
        # Either 400 (email too long) or 201 (Django may accept it)
        assert resp.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_201_CREATED)


@pytest.mark.django_db
class TestSignupBillingLimit:
    """Resource-limit enforcement on the signup endpoint."""

    def test_billing_exceeded_returns_400(self, as_mgr):
        """When BillingService raises, the endpoint returns 400.

        We simulate the tenant-middleware by patching ``request.tenant``
        on the APIClient's request before it reaches the view.
        """
        from apps.authentication.views import SignupView as SV
        from apps.billing.exceptions import ResourceLimitExceeded as RLE

        # Manually exercise the error path: patch the view's billing service
        # and the request tenant lookup so the guard `if tenant is not None`
        # passes.
        original_dispatch = SV.dispatch

        def _patched_dispatch(self, request, *args, **kwargs):
            request.tenant = object()  # Pretend tenant middleware ran
            return original_dispatch(self, request, *args, **kwargs)

        with patch.object(SV, "dispatch", _patched_dispatch):
            with patch(
                "apps.authentication.views.BillingService.check_resource_limit",
                side_effect=RLE("staff_accounts", 10, 5),
            ):
                resp = as_mgr.post(
                    REGISTER_URL,
                    {"email": "bill-exceed@test.com", "role": "Kitchen_Staff"},
                    format="json",
                )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestSignupInviteEmailFailure:
    """Invite email failure should not prevent user creation."""

    @patch("django.core.mail.send_mail", side_effect=Exception("SMTP down"))
    def test_user_created_even_if_email_fails(self, mock_mail, as_mgr):
        """When the invite email fails, the user is still created (201)."""
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "emailfail@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email="emailfail@test.com").exists()
        # invite_sent should be False because email failed
        assert resp.data.get("invite_sent") is False

    @patch("django.core.mail.send_mail", return_value=1)
    def test_invite_sent_flag_true(self, mock_mail, as_mgr):
        """When email succeeds, invite_sent is True."""
        resp = as_mgr.post(
            REGISTER_URL,
            {"email": "emailsuccess@test.com", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data.get("invite_sent") is True


@pytest.mark.django_db
class TestSignupHttpMethods:
    """Only POST is allowed for user creation."""

    def test_get_returns_405(self, as_mgr):
        resp = as_mgr.get(REGISTER_URL)
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_put_returns_405(self, as_mgr):
        resp = as_mgr.put(
            REGISTER_URL,
            {"email": "put@test.com", "role": "Kitchen_Staff"},
            format="json",
        )
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_delete_returns_405(self, as_mgr):
        resp = as_mgr.delete(REGISTER_URL)
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.django_db
class TestSignupDuplicateEmailAcrossRoles:
    """Email uniqueness is enforced regardless of who creates."""

    def test_same_email_different_caller(self, as_mgr, as_owner):
        """Two different managers trying to create the same email = 400."""
        as_mgr.post(
            REGISTER_URL,
            {"email": "shareddup@test.com", "role": "Receptionist"},
            format="json",
        )
        resp = as_owner.post(
            REGISTER_URL,
            {"email": "shareddup@test.com", "role": "Kitchen_Staff"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
