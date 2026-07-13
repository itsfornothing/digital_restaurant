"""
tests/test_platform_audit.py — Verifies platform-level audit logging.

The test strategy:
  - Integration test: calls suspend_tenant with mocks for Tenant and
    session flushing, but lets PlatformAuditLog write to the test DB.
  - Graceful-failure test: verifies that a failing audit write does not
    propagate (already done via side_effect above).
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.django_db
class TestSuspendTenantPlatformAudit:

    def test_audit_write_during_suspend(self, db):
        """suspend_tenant creates PlatformAuditLog with TENANT_SUSPEND."""
        from apps.tenants.models import PlatformAuditLog
        from apps.tenants.services import ProvisioningService

        mock_tenant = MagicMock()
        mock_tenant.pk = "550e8400-e29b-41d4-a716-446655440000"
        mock_tenant.id = "550e8400-e29b-41d4-a716-446655440000"
        mock_tenant.name = "Test"
        mock_tenant.slug = "test"
        mock_tenant.schema_name = "tenant_test"
        mock_tenant.is_active = True

        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch(
                 "apps.tenants.services.ProvisioningService._flush_tenant_sessions"
             ):
            ProvisioningService().suspend_tenant(tenant_id=1)

        # Verify the record was actually written to the database
        records = PlatformAuditLog.objects.filter(action="TENANT_SUSPEND")
        assert records.count() == 1, (
            f"Expected 1 PlatformAuditLog record, got {records.count()}"
        )
        log_entry = records.first()
        assert log_entry.resource_type == "Tenant"
        assert log_entry.old_value == {"is_active": True}
        assert log_entry.new_value == {"is_active": False}
        assert log_entry.status == "success"

    def test_audit_failure_is_graceful(self, db):
        """A failing audit write does not propagate exceptions."""
        from apps.tenants.models import PlatformAuditLog
        from apps.tenants.services import ProvisioningService

        mock_tenant = MagicMock()
        mock_tenant.pk = "550e8400-e29b-41d4-a716-446655440000"
        mock_tenant.id = "550e8400-e29b-41d4-a716-446655440000"
        mock_tenant.name = "Test"
        mock_tenant.slug = "test"
        mock_tenant.schema_name = "tenant_test"
        mock_tenant.is_active = True

        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch(
                 "apps.tenants.services.ProvisioningService._flush_tenant_sessions"
             ), \
             patch.object(
                 PlatformAuditLog.objects, "create",
                 side_effect=RuntimeError("DB error"),
             ):
            ProvisioningService().suspend_tenant(tenant_id=1)
