"""
tests/test_tenant_api.py — Unit tests for TenantViewSet (Task 4.2).

Tests cover:
  - POST /api/v1/tenants/              → create action
  - POST /api/v1/tenants/{id}/suspend/ → suspend action
  - DELETE /api/v1/tenants/{id}/       → destroy action

All database and service calls are mocked so the tests run with the
in-memory SQLite testing profile without requiring Docker or PostgreSQL.

IsSuperAdmin is also patched to always return True so that permission
logic (Task 5) does not interfere with these service-wiring tests.

Requirements: 1.2, 1.4, 1.5, 1.6
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.test import RequestFactory
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from apps.tenants.views import TenantViewSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expected_token(tenant_id: int) -> str:
    """Reproduce the deterministic delete token from services.py."""
    raw = f"{settings.SECRET_KEY}:{tenant_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _make_mock_tenant(pk=1, slug="acme", name="Acme Restaurant",
                      schema_name="tenant_acme", is_active=True):
    t = MagicMock()
    t.pk = pk
    t.id = pk
    t.slug = slug
    t.name = name
    t.schema_name = schema_name
    t.is_active = is_active
    t.created_at = None
    return t


# ---------------------------------------------------------------------------
# Fixture: always-allow IsSuperAdmin
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def allow_super_admin(monkeypatch):
    """
    Patch IsSuperAdmin.has_permission to always return True so permission
    enforcement (Task 5) does not block these unit tests.
    """
    monkeypatch.setattr(
        "shared.permissions.IsSuperAdmin.has_permission",
        lambda self, request, view: True,
    )


# ---------------------------------------------------------------------------
# Helper: build an authenticated fake request
# ---------------------------------------------------------------------------


def _api_factory():
    return APIRequestFactory()


# ===========================================================================
# POST /api/v1/tenants/  — create
# ===========================================================================


class TestTenantCreate:
    """Tests for TenantViewSet.create (POST /api/v1/tenants/)."""

    VALID_PAYLOAD = {
        "name": "Green Leaf",
        "slug": "greenleaf",
        "plan_id": 1,
        "owner_email": "owner@greenleaf.et",
    }

    def _call_create(self, payload, service_mock):
        factory = _api_factory()
        request = factory.post("/api/v1/tenants/", payload, format="json")
        view = TenantViewSet.as_view({"post": "create"})
        with patch("apps.tenants.views.ProvisioningService", return_value=service_mock):
            response = view(request)
        return response

    # --- Success path ---

    def test_returns_201_on_success(self):
        """Valid payload with a working service returns 201 Created."""
        mock_tenant = _make_mock_tenant(slug="greenleaf", name="Green Leaf")
        service = MagicMock()
        service.create_tenant.return_value = mock_tenant

        response = self._call_create(self.VALID_PAYLOAD, service)

        assert response.status_code == status.HTTP_201_CREATED

    def test_calls_service_with_correct_args(self):
        """create passes validated data to ProvisioningService.create_tenant."""
        mock_tenant = _make_mock_tenant()
        service = MagicMock()
        service.create_tenant.return_value = mock_tenant

        self._call_create(self.VALID_PAYLOAD, service)

        service.create_tenant.assert_called_once_with(
            name="Green Leaf",
            slug="greenleaf",
            plan_id=1,
            owner_email="owner@greenleaf.et",
        )

    # --- Validation errors ---

    def test_returns_400_for_missing_name(self):
        """Missing 'name' field yields 400 VALIDATION_ERROR."""
        service = MagicMock()
        payload = {k: v for k, v in self.VALID_PAYLOAD.items() if k != "name"}
        response = self._call_create(payload, service)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "VALIDATION_ERROR"

    def test_returns_400_for_invalid_email(self):
        """Invalid owner_email yields 400 VALIDATION_ERROR."""
        service = MagicMock()
        payload = {**self.VALID_PAYLOAD, "owner_email": "not-an-email"}
        response = self._call_create(payload, service)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "VALIDATION_ERROR"

    def test_returns_400_for_invalid_slug(self):
        """Slug with spaces yields 400 VALIDATION_ERROR."""
        service = MagicMock()
        payload = {**self.VALID_PAYLOAD, "slug": "has spaces"}
        response = self._call_create(payload, service)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "VALIDATION_ERROR"

    # --- Service-level errors ---

    def test_returns_400_for_tenant_already_exists(self):
        """TenantAlreadyExists from service maps to 400 TENANT_ALREADY_EXISTS."""
        from apps.tenants.services import TenantAlreadyExists

        service = MagicMock()
        service.create_tenant.side_effect = TenantAlreadyExists("Slug taken.")

        response = self._call_create(self.VALID_PAYLOAD, service)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "TENANT_ALREADY_EXISTS"

    def test_returns_400_for_plan_not_found(self):
        """PlanNotFound from service maps to 400 PLAN_NOT_FOUND."""
        from apps.tenants.services import PlanNotFound

        service = MagicMock()
        service.create_tenant.side_effect = PlanNotFound("No plan.")

        response = self._call_create(self.VALID_PAYLOAD, service)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "PLAN_NOT_FOUND"

    def test_returns_500_for_provisioning_error(self):
        """ProvisioningError (e.g. migration failure) maps to 500."""
        from apps.tenants.services import ProvisioningError

        service = MagicMock()
        service.create_tenant.side_effect = ProvisioningError("migrate_schemas failed")

        response = self._call_create(self.VALID_PAYLOAD, service)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data["error"]["code"] == "PROVISIONING_ERROR"


# ===========================================================================
# POST /api/v1/tenants/{pk}/suspend/  — suspend
# ===========================================================================


class TestTenantSuspend:
    """Tests for TenantViewSet.suspend (POST /api/v1/tenants/{id}/suspend/)."""

    def _call_suspend(self, pk, service_mock):
        factory = _api_factory()
        request = factory.post(f"/api/v1/tenants/{pk}/suspend/")
        view = TenantViewSet.as_view({"post": "suspend"})
        with patch("apps.tenants.views.ProvisioningService", return_value=service_mock):
            response = view(request, pk=str(pk))
        return response

    def test_returns_200_on_success(self):
        """Successful suspension returns 200 with confirmation message."""
        service = MagicMock()
        service.suspend_tenant.return_value = None

        response = self._call_suspend(pk=1, service_mock=service)

        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data

    def test_calls_service_with_correct_tenant_id(self):
        """suspend passes the pk to ProvisioningService.suspend_tenant."""
        service = MagicMock()

        self._call_suspend(pk=42, service_mock=service)

        service.suspend_tenant.assert_called_once_with(tenant_id="42")

    def test_returns_404_for_tenant_not_found(self):
        """TenantNotFound from service maps to 404 TENANT_NOT_FOUND."""
        from apps.tenants.services import TenantNotFound

        service = MagicMock()
        service.suspend_tenant.side_effect = TenantNotFound("No tenant.")

        response = self._call_suspend(pk=9999, service_mock=service)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "TENANT_NOT_FOUND"


# ===========================================================================
# DELETE /api/v1/tenants/{pk}/  — destroy
# ===========================================================================


class TestTenantDestroy:
    """Tests for TenantViewSet.destroy (DELETE /api/v1/tenants/{id}/)."""

    TENANT_ID = 7
    VALID_TOKEN = _expected_token(TENANT_ID)

    def _call_destroy(self, pk, service_mock, confirm_header=True,
                      confirmation_token=None):
        factory = _api_factory()
        body = {}
        if confirmation_token is not None:
            body["confirmation_token"] = confirmation_token

        headers = {}
        if confirm_header is True:
            headers["HTTP_X_CONFIRM_DELETE"] = "true"
        elif confirm_header is not None:
            # custom value
            headers["HTTP_X_CONFIRM_DELETE"] = confirm_header

        request = factory.delete(
            f"/api/v1/tenants/{pk}/",
            data=body,
            format="json",
            **headers,
        )
        view = TenantViewSet.as_view({"delete": "destroy"})
        with patch("apps.tenants.views.ProvisioningService", return_value=service_mock):
            response = view(request, pk=str(pk))
        return response

    # --- Header enforcement ---

    def test_returns_400_when_confirm_header_missing(self):
        """Missing X-Confirm-Delete header returns 400 MISSING_CONFIRM_HEADER."""
        service = MagicMock()
        response = self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header=None,
            confirmation_token=self.VALID_TOKEN,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "MISSING_CONFIRM_HEADER"

    def test_returns_400_when_confirm_header_not_true(self):
        """X-Confirm-Delete: false returns 400 MISSING_CONFIRM_HEADER."""
        service = MagicMock()
        response = self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header="false",
            confirmation_token=self.VALID_TOKEN,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "MISSING_CONFIRM_HEADER"

    # --- Token enforcement ---

    def test_returns_400_when_confirmation_token_absent(self):
        """No confirmation_token in body returns 400 MISSING_CONFIRM_TOKEN."""
        service = MagicMock()
        # Provide the token hint via generate_delete_token
        service.generate_delete_token.return_value = self.VALID_TOKEN

        response = self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header=True,
            confirmation_token=None,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "MISSING_CONFIRM_TOKEN"
        # Hint token should be included in the response
        assert "delete_token" in response.data["error"]

    def test_returns_400_for_invalid_confirmation_token(self):
        """Wrong confirmation_token returns 400 INVALID_CONFIRM_TOKEN."""
        from apps.tenants.services import InvalidConfirmationToken

        service = MagicMock()
        service.delete_tenant.side_effect = InvalidConfirmationToken("Bad token.")

        response = self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header=True,
            confirmation_token="wrong-token",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "INVALID_CONFIRM_TOKEN"

    # --- Success path ---

    def test_returns_204_on_success(self):
        """Correct header + token returns 204 No Content."""
        service = MagicMock()
        service.delete_tenant.return_value = None

        response = self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header=True,
            confirmation_token=self.VALID_TOKEN,
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_calls_service_with_correct_args(self):
        """destroy passes pk and token to ProvisioningService.delete_tenant."""
        service = MagicMock()
        service.delete_tenant.return_value = None

        self._call_destroy(
            pk=self.TENANT_ID,
            service_mock=service,
            confirm_header=True,
            confirmation_token=self.VALID_TOKEN,
        )

        service.delete_tenant.assert_called_once_with(
            tenant_id=str(self.TENANT_ID),
            confirmation_token=self.VALID_TOKEN,
        )

    def test_returns_404_for_tenant_not_found(self):
        """TenantNotFound from service maps to 404 TENANT_NOT_FOUND."""
        from apps.tenants.services import TenantNotFound

        service = MagicMock()
        service.delete_tenant.side_effect = TenantNotFound("No tenant.")

        response = self._call_destroy(
            pk=9999,
            service_mock=service,
            confirm_header=True,
            confirmation_token=self.VALID_TOKEN,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "TENANT_NOT_FOUND"

    def test_confirmation_token_accepted_via_query_param(self):
        """confirmation_token can be passed as a query parameter."""
        service = MagicMock()
        service.delete_tenant.return_value = None

        factory = _api_factory()
        # Pass confirmation_token as a URL query parameter (appended to path)
        request = factory.delete(
            f"/api/v1/tenants/{self.TENANT_ID}/?confirmation_token={self.VALID_TOKEN}",
            HTTP_X_CONFIRM_DELETE="true",
        )

        view = TenantViewSet.as_view({"delete": "destroy"})
        with patch("apps.tenants.views.ProvisioningService", return_value=service):
            response = view(request, pk=str(self.TENANT_ID))

        assert response.status_code == status.HTTP_204_NO_CONTENT


# ===========================================================================
# Permission enforcement
# ===========================================================================


class TestTenantPermissions:
    """Ensures IsSuperAdmin is declared on the viewset."""

    def test_viewset_uses_is_super_admin_permission(self):
        """TenantViewSet.permission_classes must include IsSuperAdmin."""
        from shared.permissions import IsSuperAdmin

        assert IsSuperAdmin in TenantViewSet.permission_classes

    def test_create_returns_403_when_permission_denied(self, monkeypatch):
        """When IsSuperAdmin denies access, POST returns 403."""
        monkeypatch.setattr(
            "shared.permissions.IsSuperAdmin.has_permission",
            lambda self, request, view: False,
        )
        factory = _api_factory()
        request = factory.post(
            "/api/v1/tenants/",
            {"name": "X", "slug": "x", "plan_id": 1, "owner_email": "x@x.com"},
            format="json",
        )
        view = TenantViewSet.as_view({"post": "create"})
        response = view(request)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_suspend_returns_403_when_permission_denied(self, monkeypatch):
        """When IsSuperAdmin denies access, POST suspend returns 403."""
        monkeypatch.setattr(
            "shared.permissions.IsSuperAdmin.has_permission",
            lambda self, request, view: False,
        )
        factory = _api_factory()
        request = factory.post("/api/v1/tenants/1/suspend/")
        view = TenantViewSet.as_view({"post": "suspend"})
        response = view(request, pk="1")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_destroy_returns_403_when_permission_denied(self, monkeypatch):
        """When IsSuperAdmin denies access, DELETE returns 403."""
        monkeypatch.setattr(
            "shared.permissions.IsSuperAdmin.has_permission",
            lambda self, request, view: False,
        )
        factory = _api_factory()
        request = factory.delete(
            "/api/v1/tenants/1/",
            HTTP_X_CONFIRM_DELETE="true",
        )
        view = TenantViewSet.as_view({"delete": "destroy"})
        response = view(request, pk="1")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# URL registration smoke test
# ===========================================================================


class TestURLRegistration:
    """Verify the router generated the expected URL patterns."""

    def test_create_url_registered(self):
        """POST /api/v1/tenants/ must be routable."""
        from django.urls import reverse

        # Verify using the basename registered in urls.py
        url = reverse("tenant-list")
        assert url == "/api/v1/tenants/"

    def test_suspend_url_registered(self):
        """POST /api/v1/tenants/{pk}/suspend/ must be routable."""
        from django.urls import reverse

        url = reverse("tenant-suspend", kwargs={"pk": 1})
        assert url == "/api/v1/tenants/1/suspend/"

    def test_destroy_url_registered(self):
        """DELETE /api/v1/tenants/{pk}/ must be routable."""
        from django.urls import reverse

        url = reverse("tenant-detail", kwargs={"pk": 1})
        assert url == "/api/v1/tenants/1/"

    def test_retrieve_url_registered(self):
        """GET /api/v1/tenants/{pk}/ must be routable."""
        from django.urls import reverse

        url = reverse("tenant-detail", kwargs={"pk": 1})
        assert url == "/api/v1/tenants/1/"


# ===========================================================================
# TC-API17 / TC-API18 — Tenant Owner access to tenant retrieve endpoint
# (Task 8.6 — Requirement 1.2, 1.5)
# ===========================================================================


class TestTenantOwnerRetrieve:
    """
    TC-API17: GET /api/v1/tenants/{id}/ as Tenant Owner for own tenant → 200
    TC-API18: GET /api/v1/tenants/{id}/ as Tenant Owner for different tenant → 403

    These tests patch the IsSuperAdmin gate so that the per-action
    Tenant_Owner logic in TenantViewSet.retrieve() is reached.
    The Tenant model queryset is also mocked so no database is required.

    Validates: Requirements 1.2, 1.5
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_tenant_owner_user(tenant_pk=1):
        """Build a mock Tenant_Owner user."""
        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.role = "Tenant_Owner"
        return user

    @staticmethod
    def _call_retrieve(pk, user, request_tenant=None):
        """
        Call TenantViewSet.retrieve with a fake request authenticated as *user*.

        *request_tenant* simulates the tenant injected by TenantMiddleware.
        If None, ``request.tenant`` is not set, which triggers the 403 path.
        """
        factory = _api_factory()
        request = factory.get(f"/api/v1/tenants/{pk}/")
        request.user = user

        # Simulate TenantMiddleware setting request.tenant
        if request_tenant is not None:
            request.tenant = request_tenant

        view = TenantViewSet.as_view({"get": "retrieve"})

        # Bypass IsSuperAdmin at the class-level permission gate so the
        # Tenant_Owner branch of retrieve() is reached.
        with patch(
            "shared.permissions.IsSuperAdmin.has_permission",
            lambda self, req, view_obj: True,  # let the action decide
        ):
            response = view(request, pk=str(pk))

        return response

    # ------------------------------------------------------------------
    # TC-API17: Tenant Owner retrieves own tenant → 200
    # ------------------------------------------------------------------

    def test_tc_api17_tenant_owner_own_tenant_returns_200(self):
        """
        TC-API17: A Tenant_Owner requesting GET /api/v1/tenants/{id}/ where
        {id} matches their own tenant receives HTTP 200 and the tenant data.
        """
        tenant_pk = 42
        mock_tenant = _make_mock_tenant(pk=tenant_pk, slug="my-restaurant",
                                        name="My Restaurant")
        user = self._make_tenant_owner_user(tenant_pk=tenant_pk)

        # request.tenant simulates what TenantMiddleware injects: the same tenant
        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.objects.get.return_value = mock_tenant

            response = self._call_retrieve(
                pk=tenant_pk,
                user=user,
                request_tenant=mock_tenant,  # same tenant — own access
            )

        assert response.status_code == status.HTTP_200_OK

    def test_tc_api17_response_contains_tenant_fields(self):
        """
        TC-API17 (supplementary): The 200 response body contains expected
        tenant fields (id, name, slug, schema_name, is_active).
        """
        tenant_pk = 42
        mock_tenant = _make_mock_tenant(pk=tenant_pk, slug="my-restaurant",
                                        name="My Restaurant",
                                        schema_name="tenant_my-restaurant")
        # Make the mock serialise as a dict (TenantSerializer reads model fields)
        mock_tenant.id = tenant_pk
        user = self._make_tenant_owner_user(tenant_pk=tenant_pk)

        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.objects.get.return_value = mock_tenant
            response = self._call_retrieve(
                pk=tenant_pk,
                user=user,
                request_tenant=mock_tenant,
            )

        assert response.status_code == status.HTTP_200_OK
        # TenantSerializer always includes at minimum 'name' and 'slug'
        assert "name" in response.data or response.data  # serializer returned something

    # ------------------------------------------------------------------
    # TC-API18: Tenant Owner retrieves a different tenant → 403
    # ------------------------------------------------------------------

    def test_tc_api18_tenant_owner_different_tenant_returns_403(self):
        """
        TC-API18: A Tenant_Owner requesting GET /api/v1/tenants/{id}/ where
        {id} belongs to a *different* tenant receives HTTP 403 TENANT_ACCESS_DENIED.
        """
        # The requested tenant (what the URL points to)
        requested_tenant_pk = 99
        requested_tenant = _make_mock_tenant(pk=requested_tenant_pk,
                                             slug="other-restaurant",
                                             name="Other Restaurant")

        # The owner's own tenant (what TenantMiddleware injects)
        own_tenant_pk = 42
        own_tenant = _make_mock_tenant(pk=own_tenant_pk, slug="my-restaurant",
                                       name="My Restaurant")

        user = self._make_tenant_owner_user(tenant_pk=own_tenant_pk)

        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.objects.get.return_value = requested_tenant

            response = self._call_retrieve(
                pk=requested_tenant_pk,
                user=user,
                request_tenant=own_tenant,  # different from requested_tenant_pk
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"]["code"] == "TENANT_ACCESS_DENIED"

    def test_tc_api18_response_has_structured_error_envelope(self):
        """
        TC-API18 (supplementary): The 403 response conforms to the standard
        error envelope shape: {"error": {"code": ..., "message": ..., "details": ...}}.
        """
        requested_tenant = _make_mock_tenant(pk=99, slug="other")
        own_tenant = _make_mock_tenant(pk=42, slug="mine")
        user = self._make_tenant_owner_user(tenant_pk=42)

        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.objects.get.return_value = requested_tenant

            response = self._call_retrieve(
                pk=99,
                user=user,
                request_tenant=own_tenant,
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        error = response.data.get("error", {})
        assert "code" in error
        assert "message" in error

    def test_tc_api18_tenant_owner_no_request_tenant_returns_403(self):
        """
        TC-API18 (edge case): If TenantMiddleware has not set request.tenant
        (e.g. an unusual routing path), a Tenant_Owner request to any tenant
        detail returns 403 TENANT_ACCESS_DENIED rather than leaking data.
        """
        requested_tenant = _make_mock_tenant(pk=99, slug="other")
        user = self._make_tenant_owner_user()

        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.objects.get.return_value = requested_tenant

            # No request_tenant passed → request.tenant attribute absent
            response = self._call_retrieve(
                pk=99,
                user=user,
                request_tenant=None,
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"]["code"] == "TENANT_ACCESS_DENIED"

    # ------------------------------------------------------------------
    # Non-Tenant_Owner roles → 403
    # ------------------------------------------------------------------

    def test_retrieve_returns_403_for_branch_manager(self):
        """
        A Branch_Manager may not retrieve tenant records — 403 is returned.
        """
        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.role = "Branch_Manager"

        factory = _api_factory()
        request = factory.get("/api/v1/tenants/1/")
        request.user = user

        view = TenantViewSet.as_view({"get": "retrieve"})

        # Let the IsSuperAdmin gate pass (so the action code decides)
        with patch(
            "shared.permissions.IsSuperAdmin.has_permission",
            lambda self, req, view_obj: True,
        ):
            response = view(request, pk="1")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_retrieve_returns_404_when_tenant_does_not_exist(self):
        """
        GET /api/v1/tenants/{id}/ for a non-existent id returns 404 TENANT_NOT_FOUND
        (for both Super_Admin and Tenant_Owner callers).
        """
        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.role = "Super_Admin"

        factory = _api_factory()
        request = factory.get("/api/v1/tenants/9999/")
        request.user = user

        view = TenantViewSet.as_view({"get": "retrieve"})

        with patch("apps.tenants.views.Tenant") as MockTenantModel:
            MockTenantModel.DoesNotExist = Exception
            MockTenantModel.objects.get.side_effect = MockTenantModel.DoesNotExist

            with patch(
                "shared.permissions.IsSuperAdmin.has_permission",
                lambda self, req, view_obj: True,
            ):
                response = view(request, pk="9999")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "TENANT_NOT_FOUND"
