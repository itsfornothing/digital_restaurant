"""
tests/test_middleware.py — Unit tests for TenantMiddleware.

Tests the three response paths:
  1. Domain not found → 404 JSON with TENANT_NOT_FOUND error code
  2. Tenant is_active=False → 403 JSON with TENANT_SUSPENDED error code
  3. Active tenant → delegates to parent (no early return)

All tests use Django's RequestFactory and mock out database calls and the
parent class's process_request to avoid requiring a live PostgreSQL instance.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.http import JsonResponse
from django.test import RequestFactory

# Import middleware module early to ensure the module object is cached
# before @patch decorators try to resolve attribute paths.
import apps.tenants.middleware  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_request(host="acme.localhost"):
    """Build a minimal GET request with a given HTTP_HOST header."""
    factory = RequestFactory(SERVER_NAME=host)
    request = factory.get("/", HTTP_HOST=host)
    return request


def parse_response_body(response):
    """Decode a JsonResponse body into a Python dict."""
    return json.loads(response.content.decode("utf-8"))


def _make_domain_model_not_found():
    """Return a mock DomainModel whose .get() raises DoesNotExist."""
    MockDomainModel = MagicMock()
    NotFound = type("DoesNotExist", (Exception,), {})
    MockDomainModel.DoesNotExist = NotFound
    MockDomainModel.objects.select_related.return_value.get.side_effect = NotFound(
        "not found"
    )
    return MockDomainModel


def _make_domain_model_with_tenant(is_active, name="Test Restaurant"):
    """Return a mock DomainModel whose .get() returns a domain with the given tenant."""
    mock_tenant = MagicMock()
    mock_tenant.is_active = is_active
    mock_tenant.name = name

    mock_domain = MagicMock()
    mock_domain.tenant = mock_tenant

    MockDomainModel = MagicMock()
    MockDomainModel.DoesNotExist = type("DoesNotExist", (Exception,), {})
    MockDomainModel.objects.select_related.return_value.get.return_value = mock_domain
    return MockDomainModel


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTenantMiddlewareNotFound:
    """404 TENANT_NOT_FOUND when no Domain matches the hostname."""

    def test_returns_404_when_domain_not_found(self):
        """Unknown hostname should yield HTTP 404 with TENANT_NOT_FOUND."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_not_found(),
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            response = middleware.process_request(make_request("unknown.localhost"))

        assert isinstance(response, JsonResponse)
        assert response.status_code == 404
        body = parse_response_body(response)
        assert body["error"] == "TENANT_NOT_FOUND"
        assert "detail" in body

    def test_404_response_contains_detail_field(self):
        """404 response body must include a non-empty 'detail' key."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_not_found(),
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            response = middleware.process_request(make_request("ghost.host"))

        body = parse_response_body(response)
        assert "detail" in body
        assert isinstance(body["detail"], str)
        assert len(body["detail"]) > 0


class TestTenantMiddlewareSuspended:
    """403 TENANT_SUSPENDED when tenant is_active=False."""

    def test_returns_403_when_tenant_inactive(self):
        """Inactive tenant should yield HTTP 403 with TENANT_SUSPENDED."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_with_tenant(is_active=False, name="Suspended Corp"),
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            response = middleware.process_request(make_request("suspended.localhost"))

        assert isinstance(response, JsonResponse)
        assert response.status_code == 403
        body = parse_response_body(response)
        assert body["error"] == "TENANT_SUSPENDED"
        assert "detail" in body

    def test_403_response_contains_detail_field(self):
        """403 response body must include a non-empty 'detail' key."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_with_tenant(is_active=False, name="Frozen"),
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            response = middleware.process_request(make_request("frozen.localhost"))

        body = parse_response_body(response)
        assert "detail" in body
        assert isinstance(body["detail"], str)
        assert len(body["detail"]) > 0


class TestTenantMiddlewareActiveTenant:
    """Active tenant: middleware delegates to parent without early return."""

    def test_delegates_to_parent_for_active_tenant(self):
        """Active tenant should fall through to parent's process_request."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_with_tenant(is_active=True, name="Acme"),
        ), patch(
            "django_tenants.middleware.main.TenantMainMiddleware.process_request",
            return_value=None,
        ) as mock_parent:
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(make_request("acme.localhost"))

        # Parent was called; no early JsonResponse returned from our middleware
        mock_parent.assert_called_once()
        assert result is None

    def test_does_not_return_json_for_active_tenant(self):
        """Active tenant must NOT produce a TENANT_NOT_FOUND or TENANT_SUSPENDED response."""
        from apps.tenants.middleware import TenantMiddleware

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=_make_domain_model_with_tenant(is_active=True),
        ), patch(
            "django_tenants.middleware.main.TenantMainMiddleware.process_request",
            return_value=None,
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(make_request("active.localhost"))

        # Must NOT be an early error response
        assert not isinstance(result, JsonResponse)


class TestTenantMiddlewareErrorCodes:
    """Ensure error code constants and inheritance are correct."""

    def test_not_found_error_code_constant(self):
        """TENANT_NOT_FOUND constant value is the string 'TENANT_NOT_FOUND'."""
        from apps.tenants.middleware import TenantMiddleware

        assert TenantMiddleware.TENANT_NOT_FOUND_ERROR == "TENANT_NOT_FOUND"

    def test_suspended_error_code_constant(self):
        """TENANT_SUSPENDED constant value is the string 'TENANT_SUSPENDED'."""
        from apps.tenants.middleware import TenantMiddleware

        assert TenantMiddleware.TENANT_SUSPENDED_ERROR == "TENANT_SUSPENDED"

    def test_middleware_inherits_from_tenant_main_middleware(self):
        """TenantMiddleware must subclass TenantMainMiddleware."""
        from django_tenants.middleware.main import TenantMainMiddleware

        from apps.tenants.middleware import TenantMiddleware

        assert issubclass(TenantMiddleware, TenantMainMiddleware)
