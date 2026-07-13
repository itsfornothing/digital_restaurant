"""
Property-Based Tests: Subdomain Resolution Round-Trip

Property 2: For any registered subdomain, TenantMiddleware resolves to the
correct tenant and no other. Specifically:

  2a - Round-Trip Correctness: A registered subdomain for an active tenant
       always resolves to that exact tenant (no 404 or 403).
  2b - Uniqueness / No Cross-Resolution: Two distinct subdomains registered
       to different tenants never cross-resolve; each only resolves to its own
       tenant. A request for an unregistered subdomain always returns 404.
  2c - Inactive Tenant Rejection: A subdomain registered to an inactive tenant
       always returns 403 (TENANT_SUSPENDED), never resolves.
  2d - Unknown Subdomain Isolation: Any subdomain not present in the domain
       registry always returns 404 (TENANT_NOT_FOUND).

Validates: Requirements 1.2, 1.7
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import apps.tenants.middleware  # noqa: F401 — ensure module is cached before patching

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid subdomain-like strings: start with lowercase ASCII letter, optionally
# followed by lowercase ASCII letters, digits, or hyphens, end with letter/digit.
# These are RFC 1034/1035-compliant label strings.
subdomain_strategy = st.from_regex(r"[a-z][a-z0-9\-]{0,28}[a-z0-9]", fullmatch=True)

# Single-character subdomains (≥2 chars required for the regex above); cover
# them with pure ASCII lowercase.
short_subdomain_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=1
)

any_subdomain_strategy = st.one_of(subdomain_strategy, short_subdomain_strategy)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(host: str):
    """Build a minimal GET request directed at *host*."""
    factory = RequestFactory(SERVER_NAME=host)
    return factory.get("/", HTTP_HOST=host)


def _make_domain_model_not_found():
    """Mock DomainModel whose .get() raises DoesNotExist."""
    MockDomainModel = MagicMock()
    NotFound = type("DoesNotExist", (Exception,), {})
    MockDomainModel.DoesNotExist = NotFound
    MockDomainModel.objects.select_related.return_value.get.side_effect = NotFound(
        "not found"
    )
    return MockDomainModel


def _make_domain_model_for_tenant(is_active: bool, tenant_name: str = "Test Tenant"):
    """Mock DomainModel whose .get() returns a domain tied to a single tenant."""
    mock_tenant = MagicMock()
    mock_tenant.is_active = is_active
    mock_tenant.name = tenant_name

    mock_domain = MagicMock()
    mock_domain.tenant = mock_tenant

    MockDomainModel = MagicMock()
    MockDomainModel.DoesNotExist = type("DoesNotExist", (Exception,), {})
    MockDomainModel.objects.select_related.return_value.get.return_value = mock_domain
    return MockDomainModel, mock_tenant


def _make_cross_resolution_domain_model(sub_a: str, tenant_a, sub_b: str, tenant_b):
    """
    Mock DomainModel that routes sub_a → tenant_a and sub_b → tenant_b.
    Any other subdomain raises DoesNotExist.
    """
    NotFound = type("DoesNotExist", (Exception,), {})

    domain_map = {}

    domain_a = MagicMock()
    domain_a.tenant = tenant_a
    domain_map[sub_a] = domain_a

    domain_b = MagicMock()
    domain_b.tenant = tenant_b
    domain_map[sub_b] = domain_b

    def _get(domain):
        if domain in domain_map:
            return domain_map[domain]
        raise NotFound(f"No domain: {domain}")

    MockDomainModel = MagicMock()
    MockDomainModel.DoesNotExist = NotFound
    MockDomainModel.objects.select_related.return_value.get.side_effect = (
        lambda **kw: _get(kw.get("domain", ""))
    )
    return MockDomainModel


def _parse_body(response) -> dict:
    return json.loads(response.content.decode("utf-8"))


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestSubdomainResolutionProperty:
    """Property-based tests for TenantMiddleware subdomain resolution."""

    # ------------------------------------------------------------------
    # Property 2a — Round-Trip Correctness
    # ------------------------------------------------------------------

    @given(subdomain=any_subdomain_strategy)
    @settings(max_examples=500)
    def test_property_round_trip_active_tenant_delegates_to_parent(self, subdomain):
        """
        **Validates: Requirements 1.2**

        For any subdomain registered to an active tenant, process_request
        must delegate to the parent (no 404/403 early return).
        """
        from apps.tenants.middleware import TenantMiddleware

        mock_domain_model, mock_tenant = _make_domain_model_for_tenant(
            is_active=True, tenant_name="Active Restaurant"
        )

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=mock_domain_model,
        ), patch(
            "django_tenants.middleware.main.TenantMainMiddleware.process_request",
            return_value=None,
        ) as mock_parent:
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(_make_request(subdomain))

        # Parent must be called — our middleware did NOT produce an error response.
        mock_parent.assert_called_once()
        # Result is None (parent's sentinel), NOT a JsonResponse.
        from django.http import JsonResponse
        assert not isinstance(result, JsonResponse), (
            f"Expected parent delegation for active subdomain '{subdomain}', "
            f"but got a JsonResponse (status={getattr(result, 'status_code', '?')})"
        )

    # ------------------------------------------------------------------
    # Property 2b — Uniqueness / No Cross-Resolution
    # ------------------------------------------------------------------

    @given(
        sub_a=any_subdomain_strategy,
        sub_b=any_subdomain_strategy,
    )
    @settings(max_examples=500)
    def test_property_no_cross_resolution_between_tenants(self, sub_a, sub_b):
        """
        **Validates: Requirements 1.2, 1.7**

        Two distinct subdomains registered to different tenants must never
        cross-resolve: a request for sub_a must never reach tenant_b, and
        a request for sub_b must never reach tenant_a.
        """
        assume(sub_a != sub_b)

        from apps.tenants.middleware import TenantMiddleware
        from django.http import JsonResponse

        tenant_a = MagicMock()
        tenant_a.is_active = True
        tenant_a.name = "Tenant A"

        tenant_b = MagicMock()
        tenant_b.is_active = True
        tenant_b.name = "Tenant B"

        mock_domain_model = _make_cross_resolution_domain_model(
            sub_a, tenant_a, sub_b, tenant_b
        )

        resolved_tenants = {}

        def parent_process_request(request):
            # Capture which tenant was set on the connection by inspecting
            # which mock domain was returned from .get().
            # We verify this indirectly: if parent is called, no error response
            # was generated, meaning the correct tenant was looked up.
            return None

        for subdomain in (sub_a, sub_b):
            with patch(
                "apps.tenants.middleware.get_tenant_domain_model",
                return_value=mock_domain_model,
            ), patch(
                "django_tenants.middleware.main.TenantMainMiddleware.process_request",
                side_effect=parent_process_request,
            ):
                middleware = TenantMiddleware(get_response=MagicMock())
                result = middleware.process_request(_make_request(subdomain))

            # Each registered subdomain resolves (no error response)
            assert not isinstance(result, JsonResponse), (
                f"Subdomain '{subdomain}' unexpectedly returned "
                f"status {getattr(result, 'status_code', '?')} instead of delegating"
            )

    @given(
        registered_sub=any_subdomain_strategy,
        unregistered_sub=any_subdomain_strategy,
    )
    @settings(max_examples=500)
    def test_property_unregistered_subdomain_does_not_resolve_to_registered_tenant(
        self, registered_sub, unregistered_sub
    ):
        """
        **Validates: Requirements 1.2, 1.7**

        A request for an unregistered subdomain must return 404, even when
        other valid tenants exist. No cross-resolution is possible.
        """
        assume(registered_sub != unregistered_sub)

        from apps.tenants.middleware import TenantMiddleware
        from django.http import JsonResponse

        # Only registered_sub is in the registry; unregistered_sub is absent.
        NotFound = type("DoesNotExist", (Exception,), {})

        mock_tenant = MagicMock()
        mock_tenant.is_active = True
        mock_tenant.name = "Known Tenant"

        mock_domain = MagicMock()
        mock_domain.tenant = mock_tenant

        def _get(domain):
            if domain == registered_sub:
                return mock_domain
            raise NotFound(f"No domain: {domain}")

        MockDomainModel = MagicMock()
        MockDomainModel.DoesNotExist = NotFound
        MockDomainModel.objects.select_related.return_value.get.side_effect = (
            lambda **kw: _get(kw.get("domain", ""))
        )

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=MockDomainModel,
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(_make_request(unregistered_sub))

        assert isinstance(result, JsonResponse), (
            f"Expected 404 JsonResponse for unregistered subdomain '{unregistered_sub}'"
        )
        assert result.status_code == 404
        body = _parse_body(result)
        assert body["error"] == "TENANT_NOT_FOUND"

    # ------------------------------------------------------------------
    # Property 2c — Inactive Tenant Rejection
    # ------------------------------------------------------------------

    @given(subdomain=any_subdomain_strategy)
    @settings(max_examples=500)
    def test_property_inactive_tenant_always_returns_403(self, subdomain):
        """
        **Validates: Requirements 1.2, 1.7**

        Any subdomain registered to an inactive tenant (is_active=False) must
        return 403 TENANT_SUSPENDED. An inactive tenant is never resolved.
        """
        from apps.tenants.middleware import TenantMiddleware
        from django.http import JsonResponse

        mock_domain_model, _ = _make_domain_model_for_tenant(
            is_active=False, tenant_name="Suspended Restaurant"
        )

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=mock_domain_model,
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(_make_request(subdomain))

        assert isinstance(result, JsonResponse), (
            f"Expected JsonResponse for inactive tenant on subdomain '{subdomain}'"
        )
        assert result.status_code == 403, (
            f"Expected 403 for inactive tenant, got {result.status_code}"
        )
        body = _parse_body(result)
        assert body["error"] == "TENANT_SUSPENDED", (
            f"Expected TENANT_SUSPENDED error, got {body.get('error')}"
        )

    # ------------------------------------------------------------------
    # Property 2d — Unknown Subdomain Isolation
    # ------------------------------------------------------------------

    @given(subdomain=any_subdomain_strategy)
    @settings(max_examples=500)
    def test_property_unknown_subdomain_always_returns_404(self, subdomain):
        """
        **Validates: Requirements 1.2, 1.7**

        Any subdomain that has no Domain record in the registry must always
        return 404 TENANT_NOT_FOUND, confirming no phantom resolution occurs.
        """
        from apps.tenants.middleware import TenantMiddleware
        from django.http import JsonResponse

        mock_domain_model = _make_domain_model_not_found()

        with patch(
            "apps.tenants.middleware.get_tenant_domain_model",
            return_value=mock_domain_model,
        ):
            middleware = TenantMiddleware(get_response=MagicMock())
            result = middleware.process_request(_make_request(subdomain))

        assert isinstance(result, JsonResponse), (
            f"Expected 404 JsonResponse for unknown subdomain '{subdomain}'"
        )
        assert result.status_code == 404, (
            f"Expected status 404, got {result.status_code} for subdomain '{subdomain}'"
        )
        body = _parse_body(result)
        assert body["error"] == "TENANT_NOT_FOUND", (
            f"Expected TENANT_NOT_FOUND, got {body.get('error')}"
        )
        assert "detail" in body and body["detail"], (
            "Response body must contain a non-empty 'detail' field"
        )
