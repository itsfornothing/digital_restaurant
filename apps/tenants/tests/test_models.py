"""
tests/test_models.py — Unit tests for the Tenant and Domain models.

These tests use Django's test framework in database-aware mode. They do NOT
require a live PostgreSQL instance because django-tenants' auto_create_schema
is side-stepped by only testing model field declarations, str methods, and
Meta configuration.

Tests are structured to run with pytest-django using the --no-migrations flag
where needed, or with in-memory SQLite for pure model logic.
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Tenant model field declaration tests
# ---------------------------------------------------------------------------


class TestTenantModelFields:
    """Verify that the Tenant model declares all required fields with correct properties."""

    def test_tenant_has_name_field(self):
        """Tenant model must have a CharField named 'name' with max_length=200."""
        from apps.tenants.models import Tenant

        field = Tenant._meta.get_field("name")
        assert field.max_length == 200

    def test_tenant_has_slug_field_unique(self):
        """Tenant.slug must be a unique SlugField."""
        from apps.tenants.models import Tenant

        field = Tenant._meta.get_field("slug")
        assert field.unique is True

    def test_tenant_has_is_active_field_default_false(self):
        """Tenant.is_active must default to False (tenants start inactive)."""
        from apps.tenants.models import Tenant

        field = Tenant._meta.get_field("is_active")
        assert field.default is False

    def test_tenant_has_created_at_auto_now_add(self):
        """Tenant.created_at must be auto-populated on creation."""
        from apps.tenants.models import Tenant

        field = Tenant._meta.get_field("created_at")
        assert field.auto_now_add is True

    def test_tenant_has_schema_name_from_mixin(self):
        """Tenant must inherit schema_name field from TenantMixin."""
        from apps.tenants.models import Tenant

        field = Tenant._meta.get_field("schema_name")
        assert field is not None
        assert field.unique is True

    def test_tenant_auto_create_schema_is_true(self):
        """Tenant.auto_create_schema must be True so schemas are created on save."""
        from apps.tenants.models import Tenant

        assert Tenant.auto_create_schema is True

    def test_tenant_str_representation(self):
        """Tenant.__str__ returns 'name (schema_name)' format."""
        from apps.tenants.models import Tenant

        tenant = Tenant.__new__(Tenant)
        tenant.name = "Acme Restaurant"
        tenant.schema_name = "tenant_acme"
        assert str(tenant) == "Acme Restaurant (tenant_acme)"

    def test_tenant_verbose_name(self):
        """Tenant Meta.verbose_name must be 'Tenant'."""
        from apps.tenants.models import Tenant

        assert Tenant._meta.verbose_name == "Tenant"

    def test_tenant_verbose_name_plural(self):
        """Tenant Meta.verbose_name_plural must be 'Tenants'."""
        from apps.tenants.models import Tenant

        assert Tenant._meta.verbose_name_plural == "Tenants"


# ---------------------------------------------------------------------------
# Domain model field declaration tests
# ---------------------------------------------------------------------------


class TestDomainModelFields:
    """Verify that the Domain model declares all required fields from DomainMixin."""

    def test_domain_has_domain_field_unique(self):
        """Domain.domain must be a unique CharField."""
        from apps.tenants.models import Domain

        field = Domain._meta.get_field("domain")
        assert field.unique is True

    def test_domain_has_is_primary_field(self):
        """Domain must have is_primary BooleanField (from DomainMixin)."""
        from apps.tenants.models import Domain

        field = Domain._meta.get_field("is_primary")
        assert field is not None

    def test_domain_has_tenant_fk(self):
        """Domain must have a ForeignKey to Tenant."""
        from apps.tenants.models import Domain, Tenant

        field = Domain._meta.get_field("tenant")
        assert field.related_model is Tenant

    def test_domain_str_representation(self):
        """Domain.__str__ returns the domain hostname string."""
        from apps.tenants.models import Domain

        domain = Domain.__new__(Domain)
        domain.domain = "acme.platform.com"
        assert str(domain) == "acme.platform.com"

    def test_domain_verbose_name(self):
        """Domain Meta.verbose_name must be 'Domain'."""
        from apps.tenants.models import Domain

        assert Domain._meta.verbose_name == "Domain"

    def test_domain_verbose_name_plural(self):
        """Domain Meta.verbose_name_plural must be 'Domains'."""
        from apps.tenants.models import Domain

        assert Domain._meta.verbose_name_plural == "Domains"


# ---------------------------------------------------------------------------
# Model inheritance tests
# ---------------------------------------------------------------------------


class TestTenantInheritance:
    """Ensure Tenant and Domain inherit from the correct django-tenants mixins."""

    def test_tenant_inherits_tenant_mixin(self):
        """Tenant must be a subclass of TenantMixin."""
        from django_tenants.models import TenantMixin

        from apps.tenants.models import Tenant

        assert issubclass(Tenant, TenantMixin)

    def test_domain_inherits_domain_mixin(self):
        """Domain must be a subclass of DomainMixin."""
        from django_tenants.models import DomainMixin

        from apps.tenants.models import Domain

        assert issubclass(Domain, DomainMixin)
