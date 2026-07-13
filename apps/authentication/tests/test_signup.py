"""Tests for the staff-account creation flow (manager-provisioned)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
class TestSignupAPI:
    """Tests for POST /api/v1/auth/register/ (authenticated manager)"""

    REGISTER_URL = "/api/v1/auth/register/"

    def _auth_client(self, role="Branch_Manager"):
        """Return an APIClient authenticated as a user with the given role."""
        user = User.objects.create_user(
            email=f"{role.lower()}@example.com",
            password="testpass1234",
            role=role,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_kitchen_staff_denied(self):
        client = self._auth_client(role="Kitchen_Staff")
        resp = client.post(
            self.REGISTER_URL,
            {"email": "ks-create@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_request_denied(self):
        client = APIClient()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "new@test.com", "role": "Branch_Manager"},
            format="json",
        )
        # Custom exception handler converts NotAuthenticated to 403
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_creates_user_with_unusable_password(self):
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "new@test.com", "role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["email"] == "new@test.com"
        assert resp.data["role"] == "Receptionist"
        assert resp.data["invite_sent"] is True
        assert "set their password" in resp.data["message"]

        db_user = User.objects.get(email="new@test.com")
        assert db_user.has_usable_password() is False

    def test_duplicate_email_returns_400(self):
        client = self._auth_client()
        User.objects.create_user(
            email="dup@test.com", password="testpass123", role="Branch_Manager"
        )
        resp = client.post(
            self.REGISTER_URL,
            {"email": "dup@test.com", "role": "Kitchen_Staff"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "EMAIL_EXISTS" in str(resp.data) or "already exists" in str(resp.data)

    def test_weak_password_not_requested(self):
        """Password is not accepted on this endpoint — invite flow only."""
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "nopw@test.com", "password": "short", "role": "Branch_Manager"},
            format="json",
        )
        # password field is not in serializer, should be ignored or 201
        assert resp.status_code == status.HTTP_201_CREATED

    def test_role_super_admin_rejected(self):
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "sa@test.com", "role": "Super_Admin"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_role_tenant_owner_rejected(self):
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "to@test.com", "role": "Tenant_Owner"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_branch_id_accepted(self):
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "withbranch@test.com", "role": "Kitchen_Staff", "branch_id": None},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_no_session_created_on_signup(self):
        client = self._auth_client()
        resp = client.post(
            self.REGISTER_URL,
            {"email": "nosession@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert "sessionid" not in resp.cookies or not resp.cookies["sessionid"].value

    def test_rate_limit_applies(self):
        from django.core.cache import cache
        from django.test import override_settings as _os

        with _os(RATELIMIT_ENABLE=True):
            cache.clear()

            mgr = User.objects.create_user(
                email="rl-mgr@test.com", password="testpass1234", role="Branch_Manager"
            )
            client2 = APIClient()
            client2.force_authenticate(user=mgr)

            for i in range(10):
                resp = client2.post(
                    self.REGISTER_URL,
                    {"email": f"rl-test-{i}@test.com", "role": "Kitchen_Staff"},
                    format="json",
                )
                assert resp.status_code != status.HTTP_429_TOO_MANY_REQUESTS

            resp_11 = client2.post(
                self.REGISTER_URL,
                {"email": "rl-last@test.com", "role": "Kitchen_Staff"},
                format="json",
            )
            assert resp_11.status_code == status.HTTP_429_TOO_MANY_REQUESTS
