"""
shared/tests/test_permissions.py

Unit tests for RBAC permission classes defined in shared/permissions.py.

Covers:
  - IsSuperAdmin
  - IsTenantOwner
  - IsBranchManager
  - IsReceptionist
  - IsKitchenStaff
  - IsCustomerSession
  - BranchScopePermission (object-level)
  - TenantScopePermission (object-level)
  - IsSuperAdminOrTenantOwner (composite)
  - IsBranchStaff (composite)

Tests run against SQLite in-memory via config.settings.testing.
Requirements: 4.1, 4.2, 4.3
"""

import uuid
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.authentication.models import UserRole
from apps.branches.models import Branch
from shared.permissions import (
    BranchScopePermission,
    IsBranchManager,
    IsBranchStaff,
    IsCustomerSession,
    IsKitchenStaff,
    IsReceptionist,
    IsSuperAdmin,
    IsSuperAdminOrTenantOwner,
    IsTenantOwner,
    TenantScopePermission,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request(user=None, session_data=None):
    """Build a minimal mock request with an optional user and session."""
    request = MagicMock()
    request.user = user
    request.tenant = None
    session = {}
    if session_data:
        session.update(session_data)
    request.session = session
    return request


def make_user_obj(role, is_active=True, branch=None):
    """Build an unsaved User-like mock for permission checks."""
    user = MagicMock(spec=User)
    user.role = role
    user.is_active = is_active
    user.is_authenticated = True
    user.branch = branch
    user.branch_id = branch.id if branch else None
    return user


def make_branch(branch_id=None):
    """Build a minimal Branch mock."""
    branch = MagicMock(spec=Branch)
    branch.id = branch_id or uuid.uuid4()
    return branch


def make_resource(branch_id=None, tenant_id=None):
    """Build a mock resource object with optional branch/tenant references."""
    obj = MagicMock()
    # Configure attribute access; use _MISSING sentinel pattern
    obj.branch_id = branch_id
    if branch_id is None:
        del obj.branch_id  # make getattr raise AttributeError
        obj.branch = None
    obj.tenant_id = tenant_id
    if tenant_id is None:
        del obj.tenant_id
        obj.tenant = None
    return obj


# ---------------------------------------------------------------------------
# IsSuperAdmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsSuperAdmin:
    perm = IsSuperAdmin()

    def test_allows_super_admin(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_tenant_owner(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_branch_manager(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_receptionist(self):
        user = make_user_obj(UserRole.RECEPTIONIST)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_kitchen_staff(self):
        user = make_user_obj(UserRole.KITCHEN_STAFF)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_customer(self):
        user = make_user_obj(UserRole.CUSTOMER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_unauthenticated(self):
        request = make_request(user=None)
        assert self.perm.has_permission(request, None) is False

    def test_denies_inactive_super_admin(self):
        user = make_user_obj(UserRole.SUPER_ADMIN, is_active=False)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# IsTenantOwner
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsTenantOwner:
    perm = IsTenantOwner()

    def test_allows_tenant_owner(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_super_admin(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_branch_manager(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_unauthenticated(self):
        request = make_request(user=None)
        assert self.perm.has_permission(request, None) is False

    def test_denies_inactive_tenant_owner(self):
        user = make_user_obj(UserRole.TENANT_OWNER, is_active=False)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# IsBranchManager
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsBranchManager:
    perm = IsBranchManager()

    def test_allows_branch_manager(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_other_roles(self):
        for role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.RECEPTIONIST,
            UserRole.KITCHEN_STAFF,
            UserRole.CUSTOMER,
        ):
            user = make_user_obj(role)
            request = make_request(user=user)
            assert self.perm.has_permission(request, None) is False, f"should deny {role}"

    def test_denies_inactive(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER, is_active=False)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# IsReceptionist
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsReceptionist:
    perm = IsReceptionist()

    def test_allows_receptionist(self):
        user = make_user_obj(UserRole.RECEPTIONIST)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_other_roles(self):
        for role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
            UserRole.KITCHEN_STAFF,
            UserRole.CUSTOMER,
        ):
            user = make_user_obj(role)
            request = make_request(user=user)
            assert self.perm.has_permission(request, None) is False, f"should deny {role}"

    def test_denies_unauthenticated(self):
        request = make_request(user=None)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# IsKitchenStaff
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsKitchenStaff:
    perm = IsKitchenStaff()

    def test_allows_kitchen_staff(self):
        user = make_user_obj(UserRole.KITCHEN_STAFF)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_other_roles(self):
        for role in (
            UserRole.SUPER_ADMIN,
            UserRole.TENANT_OWNER,
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
            UserRole.CUSTOMER,
        ):
            user = make_user_obj(role)
            request = make_request(user=user)
            assert self.perm.has_permission(request, None) is False, f"should deny {role}"

    def test_denies_inactive(self):
        user = make_user_obj(UserRole.KITCHEN_STAFF, is_active=False)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# IsCustomerSession
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsCustomerSession:
    perm = IsCustomerSession()

    def test_allows_valid_customer_session(self):
        request = make_request(
            session_data={"customer_session": {"branch_id": "1", "table_number": "7"}}
        )
        assert self.perm.has_permission(request, None) is True

    def test_denies_empty_session(self):
        request = make_request()  # no session_data
        assert self.perm.has_permission(request, None) is False

    def test_denies_session_without_customer_key(self):
        request = make_request(session_data={"some_other_key": "value"})
        assert self.perm.has_permission(request, None) is False

    def test_denies_when_session_is_none(self):
        request = make_request()
        request.session = None
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# BranchScopePermission
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBranchScopePermission:
    perm = BranchScopePermission()

    def _make_resource_with_branch_id(self, branch_id):
        obj = MagicMock()
        obj.branch_id = branch_id
        return obj

    def test_allows_user_accessing_own_branch_resource(self):
        branch_id = uuid.uuid4()
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        user.branch_id = branch_id
        resource = self._make_resource_with_branch_id(branch_id)
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_denies_user_accessing_different_branch_resource(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        user.branch_id = uuid.uuid4()
        resource = self._make_resource_with_branch_id(uuid.uuid4())  # different branch
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is False

    def test_allows_super_admin_any_branch(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        user.branch_id = None
        resource = self._make_resource_with_branch_id(uuid.uuid4())
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_allows_tenant_owner_any_branch(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        user.branch_id = None
        resource = self._make_resource_with_branch_id(uuid.uuid4())
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_denies_receptionist_with_no_branch_assignment(self):
        user = make_user_obj(UserRole.RECEPTIONIST)
        user.branch_id = None
        resource = self._make_resource_with_branch_id(uuid.uuid4())
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is False

    def test_denies_unauthenticated(self):
        resource = self._make_resource_with_branch_id(uuid.uuid4())
        request = make_request(user=None)
        assert self.perm.has_object_permission(request, None, resource) is False

    def test_allows_kitchen_staff_own_branch(self):
        branch_id = uuid.uuid4()
        user = make_user_obj(UserRole.KITCHEN_STAFF)
        user.branch_id = branch_id
        resource = self._make_resource_with_branch_id(branch_id)
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_denies_kitchen_staff_other_branch(self):
        user = make_user_obj(UserRole.KITCHEN_STAFF)
        user.branch_id = uuid.uuid4()
        resource = self._make_resource_with_branch_id(uuid.uuid4())
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is False

    def test_resource_with_branch_object_instead_of_id(self):
        """Resource exposes obj.branch (instance) instead of obj.branch_id."""
        branch_id = uuid.uuid4()
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        user.branch_id = branch_id

        obj = MagicMock()
        # Simulate no branch_id attribute
        del obj.branch_id
        branch_mock = MagicMock()
        branch_mock.id = branch_id
        obj.branch = branch_mock

        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, obj) is True


# ---------------------------------------------------------------------------
# TenantScopePermission
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTenantScopePermission:
    perm = TenantScopePermission()

    def _make_resource_with_tenant_id(self, tenant_id):
        obj = MagicMock()
        obj.tenant_id = tenant_id
        return obj

    def test_allows_super_admin_any_tenant(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        resource = self._make_resource_with_tenant_id(uuid.uuid4())
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_allows_when_resource_has_no_tenant_reference(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        # Resource without tenant reference
        obj = MagicMock()
        del obj.tenant_id
        obj.tenant = None
        request = make_request(user=user)
        assert self.perm.has_object_permission(request, None, obj) is True

    def test_allows_when_no_tenant_on_request(self):
        """Without TenantMiddleware (e.g. tests) the check passes by default."""
        tenant_id = uuid.uuid4()
        user = make_user_obj(UserRole.TENANT_OWNER)
        resource = self._make_resource_with_tenant_id(tenant_id)
        request = make_request(user=user)
        request.tenant = None  # no middleware
        assert self.perm.has_object_permission(request, None, resource) is True

    def test_allows_matching_tenant(self):
        tenant_id = uuid.uuid4()
        user = make_user_obj(UserRole.TENANT_OWNER)
        resource = self._make_resource_with_tenant_id(tenant_id)

        tenant_mock = MagicMock()
        tenant_mock.id = tenant_id
        request = make_request(user=user)
        request.tenant = tenant_mock

        assert self.perm.has_object_permission(request, None, resource) is True

    def test_denies_mismatched_tenant(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        resource = self._make_resource_with_tenant_id(uuid.uuid4())  # tenant A

        tenant_mock = MagicMock()
        tenant_mock.id = uuid.uuid4()  # tenant B
        request = make_request(user=user)
        request.tenant = tenant_mock

        assert self.perm.has_object_permission(request, None, resource) is False

    def test_denies_unauthenticated(self):
        resource = self._make_resource_with_tenant_id(uuid.uuid4())
        request = make_request(user=None)
        assert self.perm.has_object_permission(request, None, resource) is False


# ---------------------------------------------------------------------------
# Composite: IsSuperAdminOrTenantOwner
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsSuperAdminOrTenantOwner:
    perm = IsSuperAdminOrTenantOwner()

    def test_allows_super_admin(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_allows_tenant_owner(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_branch_manager(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_unauthenticated(self):
        request = make_request(user=None)
        assert self.perm.has_permission(request, None) is False


# ---------------------------------------------------------------------------
# Composite: IsBranchStaff
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsBranchStaff:
    perm = IsBranchStaff()

    def test_allows_branch_manager(self):
        user = make_user_obj(UserRole.BRANCH_MANAGER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_allows_receptionist(self):
        user = make_user_obj(UserRole.RECEPTIONIST)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_allows_kitchen_staff(self):
        user = make_user_obj(UserRole.KITCHEN_STAFF)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is True

    def test_denies_super_admin(self):
        user = make_user_obj(UserRole.SUPER_ADMIN)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_tenant_owner(self):
        user = make_user_obj(UserRole.TENANT_OWNER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_customer(self):
        user = make_user_obj(UserRole.CUSTOMER)
        request = make_request(user=user)
        assert self.perm.has_permission(request, None) is False

    def test_denies_unauthenticated(self):
        request = make_request(user=None)
        assert self.perm.has_permission(request, None) is False

    def test_denies_inactive_staff(self):
        for role in (UserRole.BRANCH_MANAGER, UserRole.RECEPTIONIST, UserRole.KITCHEN_STAFF):
            user = make_user_obj(role, is_active=False)
            request = make_request(user=user)
            assert self.perm.has_permission(request, None) is False, f"inactive {role} should be denied"
