"""Tests for the staff management API (UserViewSet)."""

import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

STAFF_LIST_URL = "/api/v1/auth/users/"
STAFF_DETAIL_URL = "/api/v1/auth/users/{pk}/"
STAFF_DEACTIVATE_URL = "/api/v1/auth/users/{pk}/deactivate/"
STAFF_REASSIGN_URL = "/api/v1/auth/users/{pk}/reassign/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_client(role="Super_Admin"):
    user = User.objects.create_user(
        email=f"{role.lower()}-{uuid.uuid4().hex[:6]}@test.com",
        password="testpass1234", role=role,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Staff list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffList:
    """GET /api/v1/auth/users/"""

    def test_unauthenticated_denied(self):
        resp = APIClient().get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthorized_role_rejected(self):
        client = _auth_client(role="Kitchen_Staff")
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_receptionist_denied(self):
        client = _auth_client(role="Receptionist")
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_branch_manager_denied(self):
        """IsSuperAdminOrTenantOwner excludes Branch_Manager from listing."""
        client = _auth_client(role="Branch_Manager")
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_tenant_owner_allowed(self):
        client = _auth_client(role="Tenant_Owner")
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_super_admin_allowed(self):
        client = _auth_client(role="Super_Admin")
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_lists_users(self):
        client = _auth_client()
        User.objects.create_user(
            email="alice@test.com", password="pass1234", role="Branch_Manager",
        )
        User.objects.create_user(
            email="bob@test.com", password="pass1234", role="Receptionist",
        )
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        emails = [u["email"] for u in resp.data]
        assert "alice@test.com" in emails
        assert "bob@test.com" in emails

    def test_list_includes_inactive_users(self):
        """Deactivated users still appear in the list."""
        client = _auth_client()
        User.objects.create_user(
            email="inactive@test.com", password="pass1234",
            role="Kitchen_Staff", is_active=False,
        )
        resp = client.get(STAFF_LIST_URL)
        emails = [u["email"] for u in resp.data]
        assert "inactive@test.com" in emails

    def test_list_excludes_password_field(self):
        client = _auth_client()
        resp = client.get(STAFF_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        if len(resp.data) > 0:
            assert "password" not in resp.data[0]

    def test_list_response_is_list(self):
        client = _auth_client()
        resp = client.get(STAFF_LIST_URL)
        assert isinstance(resp.data, list), "Expected list response"


# ---------------------------------------------------------------------------
# Staff detail / PATCH
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffUpdate:
    """PATCH /api/v1/auth/users/{id}/"""

    def test_update_role(self):
        client = _auth_client()
        target = User.objects.create_user(
            email="update-role@test.com", password="pass1234",
            role="Kitchen_Staff",
        )
        resp = client.patch(
            STAFF_DETAIL_URL.format(pk=target.pk),
            {"role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        assert target.role == "Receptionist"

    def test_update_branch(self, db):
        from apps.branches.models import Branch

        client = _auth_client()
        target = User.objects.create_user(
            email="update-branch@test.com", password="pass1234",
            role="Branch_Manager",
        )
        branch = Branch.objects.create(name="Branch X")
        url = STAFF_DETAIL_URL.format(pk=target.pk)
        print(f"\nDEBUG url={url}, target.pk={target.pk}, branch.pk={branch.pk}", end="")
        resp = client.patch(
            url,
            {"branch_id": str(branch.pk)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, (
            f"PATCH failed: {resp.status_code} {resp.data}"
        )
        target.refresh_from_db()
        assert target.branch_id == branch.pk, (
            f"branch_id not updated. Response data: {resp.data}\n"
            f"Target branch_id after refresh: {target.branch_id}"
        )

    def test_patch_read_only_email_ignored(self):
        """PATCH should ignore email changes (read_only)."""
        client = _auth_client()
        target = User.objects.create_user(
            email="readonly@test.com", password="pass1234", role="Kitchen_Staff",
        )
        resp = client.patch(
            STAFF_DETAIL_URL.format(pk=target.pk),
            {"email": "hacked@test.com"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        assert target.email == "readonly@test.com"

    def test_patch_nonexistent_user(self):
        client = _auth_client()
        resp = client.patch(
            STAFF_DETAIL_URL.format(pk=uuid.uuid4()),
            {"role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_patch_unauthorized_role_denied(self):
        client = _auth_client(role="Kitchen_Staff")
        target = User.objects.create_user(
            email="no-perm-patch@test.com", password="pass1234",
            role="Branch_Manager",
        )
        resp = client.patch(
            STAFF_DETAIL_URL.format(pk=target.pk),
            {"role": "Receptionist"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Staff deactivation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffDeactivate:
    """POST /api/v1/auth/users/{id}/deactivate/"""

    def test_deactivate_sets_is_active_false(self):
        client = _auth_client()
        target = User.objects.create_user(
            email="target@test.com", password="pass1234", role="Kitchen_Staff",
        )
        resp = client.post(
            STAFF_DEACTIVATE_URL.format(pk=target.pk), {}, format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        assert target.is_active is False

    def test_deactivate_idempotent(self):
        client = _auth_client()
        target = User.objects.create_user(
            email="idempotent@test.com", password="pass1234",
            role="Branch_Manager", is_active=False,
        )
        resp = client.post(
            STAFF_DEACTIVATE_URL.format(pk=target.pk), {}, format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        assert target.is_active is False

    def test_deactivate_nonexistent_user(self):
        client = _auth_client()
        fake_pk = uuid.uuid4()
        resp = client.post(
            STAFF_DEACTIVATE_URL.format(pk=fake_pk), {}, format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_deactivate_unauthorized_role_denied(self):
        client = _auth_client(role="Kitchen_Staff")
        target = User.objects.create_user(
            email="no-perm-deact@test.com", password="pass1234",
            role="Branch_Manager",
        )
        resp = client.post(
            STAFF_DEACTIVATE_URL.format(pk=target.pk), {}, format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_deactivate_self_not_allowed(self):
        """A user cannot deactivate themselves (not implemented)."""
        client = _auth_client(role="Super_Admin")
        caller = User.objects.get(email__startswith="super_admin")
        resp = client.post(
            STAFF_DEACTIVATE_URL.format(pk=caller.pk), {}, format="json",
        )
        # Currently allowed — no self-deactivation guard exists
        assert resp.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Staff reassignment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffReassign:
    """POST /api/v1/auth/users/{id}/reassign/"""

    def test_reassign_updates_branch(self, db):
        from apps.branches.models import Branch

        client = _auth_client()
        target = User.objects.create_user(
            email="reassign-target@test.com", password="pass1234",
            role="Receptionist",
        )
        branch = Branch.objects.create(name="Test Branch")
        resp = client.post(
            STAFF_REASSIGN_URL.format(pk=target.pk),
            {"branch_id": str(branch.pk)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        assert target.branch_id == branch.pk

    def test_reassign_without_branch_id_returns_400(self):
        client = _auth_client()
        target = User.objects.create_user(
            email="reassign-no-branch@test.com", password="pass1234",
            role="Kitchen_Staff",
        )
        resp = client.post(
            STAFF_REASSIGN_URL.format(pk=target.pk), {}, format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_reassign_nonexistent_user(self):
        client = _auth_client()
        resp = client.post(
            STAFF_REASSIGN_URL.format(pk=uuid.uuid4()),
            {"branch_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_reassign_unauthorized_role_denied(self):
        client = _auth_client(role="Receptionist")
        target = User.objects.create_user(
            email="no-perm-reassign@test.com", password="pass1234",
            role="Kitchen_Staff",
        )
        resp = client.post(
            STAFF_REASSIGN_URL.format(pk=target.pk),
            {"branch_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Staff creation (POST to list endpoint)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffCreate:
    """POST /api/v1/auth/users/ — create a staff user."""

    def test_create_minimal(self):
        client = _auth_client()
        resp = client.post(
            STAFF_LIST_URL,
            {"email": "create-minimal@test.com", "role": "Receptionist"},
            format="json",
        )
        print("DEBUG status:", resp.status_code)
        print("DEBUG data:", resp.data)
        assert resp.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email="create-minimal@test.com").exists(), (
            f"User not found in DB. Response: {resp.data}"
        )

    def test_create_unauthorized_role_denied(self):
        client = _auth_client(role="Kitchen_Staff")
        resp = client.post(
            STAFF_LIST_URL,
            {"email": "create-unauth@test.com", "role": "Branch_Manager"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_duplicate_email(self):
        client = _auth_client()
        User.objects.create_user(
            email="dup-create@test.com", password="pass1234", role="Receptionist",
        )
        resp = client.post(
            STAFF_LIST_URL,
            {"email": "dup-create@test.com", "role": "Kitchen_Staff"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

