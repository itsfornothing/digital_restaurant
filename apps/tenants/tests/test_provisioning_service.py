"""
tests/test_provisioning_service.py — Unit tests for ProvisioningService.

Tests for:
  - create_tenant: validation, model creation, error cases
  - suspend_tenant: is_active flag, session flushing
  - delete_tenant: token validation, schema drop, record deletion
  - generate_delete_token: deterministic output

All external dependencies (DB, Redis, subprocess) are mocked so tests run
with the in-memory SQLite testing profile without requiring Docker.

Requirements: 1.4, 1.5, 1.6
"""

import hashlib
from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_tenant(pk=1, slug="acme", name="Acme Restaurant",
                      schema_name="tenant_acme", is_active=False):
    """Build a lightweight mock Tenant object."""
    t = MagicMock()
    t.pk = pk
    t.slug = slug
    t.name = name
    t.schema_name = schema_name
    t.is_active = is_active
    return t


def _expected_token(tenant_id) -> str:
    raw = f"{settings.SECRET_KEY}:{tenant_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# generate_delete_token
# ---------------------------------------------------------------------------


class TestGenerateDeleteToken:
    """ProvisioningService.generate_delete_token produces correct tokens."""

    def test_token_is_32_hex_chars(self):
        """Token must be exactly 32 hex characters."""
        from apps.tenants.services import ProvisioningService

        token = ProvisioningService().generate_delete_token(tenant_id=42)
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_is_deterministic_for_same_id(self):
        """Same tenant_id always yields same token."""
        from apps.tenants.services import ProvisioningService

        svc = ProvisioningService()
        assert svc.generate_delete_token(1) == svc.generate_delete_token(1)

    def test_different_ids_yield_different_tokens(self):
        """Different tenant_ids must yield different tokens."""
        from apps.tenants.services import ProvisioningService

        svc = ProvisioningService()
        assert svc.generate_delete_token(1) != svc.generate_delete_token(2)

    def test_token_matches_expected_sha256(self):
        """Token matches SHA-256(SECRET_KEY:tenant_id)[:32]."""
        from apps.tenants.services import ProvisioningService

        svc = ProvisioningService()
        assert svc.generate_delete_token(99) == _expected_token(99)


# ---------------------------------------------------------------------------
# suspend_tenant
# ---------------------------------------------------------------------------


class TestSuspendTenant:
    """ProvisioningService.suspend_tenant sets is_active=False and flushes sessions."""

    def test_sets_is_active_false(self):
        """suspend_tenant must set Tenant.is_active=False and save."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(is_active=True)
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch("apps.tenants.services.ProvisioningService._flush_tenant_sessions"):
            ProvisioningService().suspend_tenant(tenant_id=1)

        assert mock_tenant.is_active is False
        mock_tenant.save.assert_called_once_with(update_fields=["is_active"])

    def test_raises_tenant_not_found_for_unknown_id(self):
        """suspend_tenant raises TenantNotFound if no matching tenant."""
        from apps.tenants.services import ProvisioningService, TenantNotFound

        MockTenant = MagicMock()
        MockTenant.objects.get.side_effect = Exception("Not found")
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant):
            with pytest.raises(TenantNotFound):
                ProvisioningService().suspend_tenant(tenant_id=9999)

    def test_calls_flush_tenant_sessions(self):
        """suspend_tenant must call _flush_tenant_sessions after deactivating."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(is_active=True)
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch(
                 "apps.tenants.services.ProvisioningService._flush_tenant_sessions"
             ) as mock_flush:
            ProvisioningService().suspend_tenant(tenant_id=1)

        mock_flush.assert_called_once_with(mock_tenant)

    def test_suspension_order_deactivate_then_flush(self):
        """is_active must be False before session flush is called."""
        from apps.tenants.services import ProvisioningService

        call_order = []
        mock_tenant = _make_mock_tenant(is_active=True)
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = Exception

        def record_save(**kwargs):
            call_order.append("save")

        def record_flush(tenant):
            call_order.append(("flush", tenant.is_active))

        mock_tenant.save.side_effect = record_save

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch(
                 "apps.tenants.services.ProvisioningService._flush_tenant_sessions",
                 side_effect=record_flush,
             ):
            ProvisioningService().suspend_tenant(tenant_id=1)

        assert call_order[0] == "save", "save must happen before flush"
        assert call_order[1] == ("flush", False), (
            "is_active must be False at flush time"
        )


# ---------------------------------------------------------------------------
# delete_tenant
# ---------------------------------------------------------------------------


class TestDeleteTenant:
    """ProvisioningService.delete_tenant validates token, drops schema, deletes record."""

    def test_raises_tenant_not_found_for_unknown_id(self):
        """delete_tenant raises TenantNotFound when tenant_id does not exist."""
        from apps.tenants.services import ProvisioningService, TenantNotFound

        MockTenant = MagicMock()
        MockTenant.objects.get.side_effect = Exception("Not found")
        MockTenant.DoesNotExist = Exception

        with patch("apps.tenants.services.Tenant", MockTenant):
            with pytest.raises(TenantNotFound):
                ProvisioningService().delete_tenant(
                    tenant_id=9999, confirmation_token="any"
                )

    def test_raises_invalid_token_for_wrong_token(self):
        """delete_tenant raises InvalidConfirmationToken for wrong token."""
        from apps.tenants.services import ProvisioningService, InvalidConfirmationToken

        mock_tenant = _make_mock_tenant()
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = type("DoesNotExist", (Exception,), {})

        with patch("apps.tenants.services.Tenant", MockTenant):
            with pytest.raises(InvalidConfirmationToken):
                ProvisioningService().delete_tenant(
                    tenant_id=1, confirmation_token="wrong-token"
                )

    def test_drops_schema_with_correct_token(self):
        """delete_tenant executes DROP SCHEMA when token is valid."""
        from apps.tenants.services import ProvisioningService

        tenant_id = 42
        token = _expected_token(tenant_id)
        mock_tenant = _make_mock_tenant(pk=tenant_id, schema_name="tenant_acme")
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = type("DoesNotExist", (Exception,), {})

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch("apps.tenants.services.connection") as mock_conn:
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ops.quote_name.return_value = '"tenant_acme"'

            ProvisioningService().delete_tenant(
                tenant_id=tenant_id, confirmation_token=token
            )

        mock_cursor.execute.assert_called_once()
        sql_call = mock_cursor.execute.call_args[0][0]
        assert "DROP SCHEMA" in sql_call
        assert "tenant_acme" in sql_call or '"tenant_acme"' in sql_call

    def test_deletes_tenant_record_after_schema_drop(self):
        """delete_tenant calls tenant.delete() after dropping the schema."""
        from apps.tenants.services import ProvisioningService

        tenant_id = 7
        token = _expected_token(tenant_id)
        mock_tenant = _make_mock_tenant(pk=tenant_id, schema_name="tenant_acme")
        MockTenant = MagicMock()
        MockTenant.objects.get.return_value = mock_tenant
        MockTenant.DoesNotExist = type("DoesNotExist", (Exception,), {})

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch("apps.tenants.services.connection") as mock_conn:
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ops.quote_name.return_value = '"tenant_acme"'

            ProvisioningService().delete_tenant(
                tenant_id=tenant_id, confirmation_token=token
            )

        mock_tenant.delete.assert_called_once()


# ---------------------------------------------------------------------------
# create_tenant
# ---------------------------------------------------------------------------


class TestCreateTenant:
    """ProvisioningService.create_tenant provisions all required records."""

    def _make_create_tenant_patches(self, slug="acme"):
        """Return a context-manager stack of patches for create_tenant."""
        mock_tenant = _make_mock_tenant(slug=slug, schema_name=f"tenant_{slug}")
        mock_domain = MagicMock()

        patches = {
            "Tenant_objects": patch("apps.tenants.models.Tenant.objects"),
            "Domain_objects": patch("apps.tenants.models.Domain.objects"),
            "run_migrate": patch(
                "apps.tenants.services.ProvisioningService._run_migrate_schemas"
            ),
            "create_config": patch(
                "apps.tenants.services.ProvisioningService._create_tenant_config"
            ),
            "create_owner": patch(
                "apps.tenants.services.ProvisioningService._create_owner_user",
                return_value=MagicMock(pk="user-uuid"),
            ),
            "create_sub": patch(
                "apps.tenants.services.ProvisioningService._create_subscription"
            ),
            "resolve_plan": patch(
                "apps.tenants.services.ProvisioningService._resolve_plan",
                return_value=None,
            ),
            "connection": patch("apps.tenants.services.connection"),
            "restore": patch(
                "apps.tenants.services.ProvisioningService._restore_public_schema"
            ),
            "transaction": patch("apps.tenants.services.transaction"),
        }
        return patches, mock_tenant

    def test_raises_tenant_already_exists_for_duplicate_slug(self):
        """create_tenant raises TenantAlreadyExists when slug is taken."""
        from apps.tenants.services import ProvisioningService, TenantAlreadyExists

        MockTenant = MagicMock()
        MockTenant.objects.filter.return_value.exists.return_value = True

        with patch("apps.tenants.services.Tenant", MockTenant):
            with pytest.raises(TenantAlreadyExists):
                ProvisioningService().create_tenant(
                    name="Dup", slug="acme", plan_id=1, owner_email="a@b.com"
                )

    def _run_create_tenant(self, mock_tenant=None, owner_email="owner@acme.et",
                            slug="acme", name="Acme", plan_id=1,
                            extra_patches=None):
        """
        Helper to run create_tenant with all external dependencies mocked.

        Patches apps.tenants.services.Tenant so the service uses mock_tenant
        as the constructed Tenant instance.
        """
        from apps.tenants.services import ProvisioningService

        if mock_tenant is None:
            mock_tenant = _make_mock_tenant(slug=slug, schema_name=f"tenant_{slug}")

        # MockTenant: calling MockTenant(...) returns mock_tenant; .objects is a mock
        MockTenant = MagicMock(return_value=mock_tenant)
        MockTenant.objects.filter.return_value.exists.return_value = False
        MockTenant.DoesNotExist = Exception

        MockDomain = MagicMock()

        patches = [
            patch("apps.tenants.services.Tenant", MockTenant),
            patch("apps.tenants.services.Domain", MockDomain),
            patch("apps.tenants.services.ProvisioningService._run_migrate_schemas"),
            patch("apps.tenants.services.ProvisioningService._create_tenant_config"),
            patch("apps.tenants.services.ProvisioningService._create_owner_user",
                  return_value=MagicMock(pk="u1")),
            patch("apps.tenants.services.ProvisioningService._create_subscription"),
            patch("apps.tenants.services.ProvisioningService._resolve_plan",
                  return_value=None),
            patch("apps.tenants.services.connection"),
            patch("apps.tenants.services.ProvisioningService._restore_public_schema"),
            patch("apps.tenants.services.transaction"),
        ]
        extra_mocks = {}
        if extra_patches:
            for name, mock_obj in extra_patches.items():
                patches.append(patch(name, mock_obj))
                extra_mocks[name] = mock_obj

        # Apply all patches
        active = [p.start() for p in patches]
        try:
            result = ProvisioningService().create_tenant(
                name=name, slug=slug, plan_id=plan_id, owner_email=owner_email
            )
        finally:
            for p in patches:
                p.stop()

        return result, mock_tenant, active

    def test_creates_tenant_record(self):
        """create_tenant saves a Tenant object."""
        mock_tenant = _make_mock_tenant()
        result, mock_tenant, _ = self._run_create_tenant(mock_tenant=mock_tenant)
        mock_tenant.save.assert_called()

    def test_create_owner_user_is_called(self):
        """create_tenant calls _create_owner_user with the provided email."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant()
        mock_create_owner = MagicMock(return_value=MagicMock(pk="u1"))

        MockTenant = MagicMock(return_value=mock_tenant)
        MockTenant.objects.filter.return_value.exists.return_value = False
        MockDomain = MagicMock()

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch("apps.tenants.services.Domain", MockDomain), \
             patch("apps.tenants.services.ProvisioningService._run_migrate_schemas"), \
             patch("apps.tenants.services.ProvisioningService._create_tenant_config"), \
             patch("apps.tenants.services.ProvisioningService._create_owner_user",
                   mock_create_owner), \
             patch("apps.tenants.services.ProvisioningService._create_subscription"), \
             patch("apps.tenants.services.ProvisioningService._resolve_plan",
                   return_value=None), \
             patch("apps.tenants.services.connection"), \
             patch("apps.tenants.services.ProvisioningService._restore_public_schema"), \
             patch("apps.tenants.services.transaction"):
            ProvisioningService().create_tenant(
                name="Acme", slug="acme", plan_id=1, owner_email="owner@acme.et"
            )

        mock_create_owner.assert_called_once_with("owner@acme.et")

    def test_migrate_schemas_is_called(self):
        """create_tenant calls _run_migrate_schemas with the correct schema name."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(schema_name="tenant_acme")
        mock_migrate = MagicMock()

        MockTenant = MagicMock(return_value=mock_tenant)
        MockTenant.objects.filter.return_value.exists.return_value = False
        MockDomain = MagicMock()

        with patch("apps.tenants.services.Tenant", MockTenant), \
             patch("apps.tenants.services.Domain", MockDomain), \
             patch("apps.tenants.services.ProvisioningService._run_migrate_schemas",
                   mock_migrate), \
             patch("apps.tenants.services.ProvisioningService._create_tenant_config"), \
             patch("apps.tenants.services.ProvisioningService._create_owner_user",
                   return_value=MagicMock(pk="u1")), \
             patch("apps.tenants.services.ProvisioningService._create_subscription"), \
             patch("apps.tenants.services.ProvisioningService._resolve_plan",
                   return_value=None), \
             patch("apps.tenants.services.connection"), \
             patch("apps.tenants.services.ProvisioningService._restore_public_schema"), \
             patch("apps.tenants.services.transaction"):
            ProvisioningService().create_tenant(
                name="Acme", slug="acme", plan_id=1, owner_email="owner@acme.et"
            )

        mock_migrate.assert_called_once_with("tenant_acme")

    def test_tenant_activated_at_end(self):
        """create_tenant sets is_active=True on the returned tenant."""
        mock_tenant = _make_mock_tenant(is_active=False)
        result, mock_tenant, _ = self._run_create_tenant(mock_tenant=mock_tenant)

        assert mock_tenant.is_active is True
        # save should have been called with is_active update
        save_calls = mock_tenant.save.call_args_list
        activation_calls = [c for c in save_calls if c[1].get("update_fields") == ["is_active"]]
        assert len(activation_calls) == 1, "is_active must be saved exactly once via update_fields"


# ---------------------------------------------------------------------------
# _flush_tenant_sessions
# ---------------------------------------------------------------------------


class TestFlushTenantSessions:
    """_flush_tenant_sessions deletes matching Redis keys; degrades gracefully."""

    def test_graceful_degradation_without_redis(self):
        """When no Redis client is available, flush logs a warning but does not raise."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant()

        with patch("apps.tenants.services._get_redis_client", return_value=None):
            # Should not raise even with no Redis
            ProvisioningService._flush_tenant_sessions(mock_tenant)

    def test_scans_and_deletes_matching_keys(self):
        """Matching session keys are deleted from Redis."""
        from apps.tenants.services import ProvisioningService, _session_belongs_to_tenant

        schema_name = "tenant_acme"
        mock_tenant = _make_mock_tenant(schema_name=schema_name)

        matching_key = b"session_key_1"
        non_matching_key = b"session_key_2"

        mock_redis = MagicMock()
        # scan returns (cursor=0, keys) — single batch, then done
        mock_redis.scan.return_value = (0, [matching_key, non_matching_key])
        # Matching key contains schema_name bytes; non-matching does not
        mock_redis.get.side_effect = lambda key: (
            b"data:" + schema_name.encode() if key == matching_key else b"other:data"
        )

        with patch("apps.tenants.services._get_redis_client", return_value=mock_redis):
            ProvisioningService._flush_tenant_sessions(mock_tenant)

        mock_redis.delete.assert_called_once_with(matching_key)

    def test_no_delete_when_no_keys_match(self):
        """No delete call is made if no keys belong to the tenant."""
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(schema_name="tenant_acme")

        mock_redis = MagicMock()
        mock_redis.scan.return_value = (0, [b"unrelated_key"])
        mock_redis.get.return_value = b"some:other:data"

        with patch("apps.tenants.services._get_redis_client", return_value=mock_redis):
            ProvisioningService._flush_tenant_sessions(mock_tenant)

        mock_redis.delete.assert_not_called()


# ---------------------------------------------------------------------------
# _session_belongs_to_tenant
# ---------------------------------------------------------------------------


class TestSessionBelongsToTenant:
    """_session_belongs_to_tenant correctly identifies tenant session data."""

    def test_returns_true_when_schema_name_in_bytes(self):
        from apps.tenants.services import _session_belongs_to_tenant

        data = b'{"_auth_user_id": "1", "tenant_schema": "tenant_acme"}'
        assert _session_belongs_to_tenant(data, "tenant_acme") is True

    def test_returns_false_when_schema_name_absent(self):
        from apps.tenants.services import _session_belongs_to_tenant

        data = b'{"_auth_user_id": "1", "tenant_schema": "tenant_other"}'
        assert _session_belongs_to_tenant(data, "tenant_acme") is False

    def test_returns_false_for_empty_bytes(self):
        from apps.tenants.services import _session_belongs_to_tenant

        assert _session_belongs_to_tenant(b"", "tenant_acme") is False
