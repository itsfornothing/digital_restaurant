"""
shared/tests/test_db_router.py

Unit tests for shared.db_router.ReadReplicaRouter.

Verifies that:
  - Read queries for FinancialService models (Income, ProfitRecord) are
    routed to the 'replica' database when it is configured.
  - Read queries for AuditLog are routed to the 'replica' database.
  - Write queries for all models always return None (defer to default).
  - Models outside the targeted apps (e.g. Branch, User) return None for
    reads, indicating no routing override (fall-through to default).
  - When 'replica' is absent from DATABASES, reads also return None
    (graceful fallback).

Tests run under config.settings.testing which uses SQLite in-memory and
sets DATABASES['replica'] = {"TEST": {"MIRROR": "default"}}.

Requirements: 19.9 (Task 20.4)
"""

from unittest.mock import patch

import pytest

from shared.db_router import ReadReplicaRouter


# ---------------------------------------------------------------------------
# Minimal model stubs
# We only need model.__name__ and model._meta.app_label, so we use simple
# classes rather than full Django models.  This keeps tests fast and free
# of DB setup.
# ---------------------------------------------------------------------------

class _FakeModel:
    """Minimal model stub — only class-level attributes used by the router."""

    def __init_subclass__(cls, name, app_label, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.__name__ = name
        cls._meta = type("_Meta", (), {"app_label": app_label})()


# FinancialService models
class Income(_FakeModel, name="Income", app_label="financials"):
    pass


class ProfitRecord(_FakeModel, name="ProfitRecord", app_label="financials"):
    pass


# AuditLog model
class AuditLog(_FakeModel, name="AuditLog", app_label="audit"):
    pass


# Models NOT in the replica-targeted set
class Branch(_FakeModel, name="Branch", app_label="branches"):
    pass


class User(_FakeModel, name="User", app_label="authentication"):
    pass


class Order(_FakeModel, name="Order", app_label="orders"):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# DATABASES dict that includes a 'replica' key (simulates production / base.py)
_DATABASES_WITH_REPLICA = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "replica": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

# DATABASES dict without a 'replica' key (simulates environments without replica)
_DATABASES_WITHOUT_REPLICA = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadReplicaRouterReadRouting:
    """db_for_read() routes targeted models to 'replica' when configured."""

    router = ReadReplicaRouter()

    @pytest.mark.parametrize("model_cls", [Income, ProfitRecord, AuditLog])
    def test_targeted_model_routes_to_replica_when_replica_exists(self, model_cls):
        """
        Income, ProfitRecord, and AuditLog reads must go to the replica
        database when DATABASES['replica'] is present.

        Requirements: 19.9
        """
        with patch("django.conf.settings.DATABASES", _DATABASES_WITH_REPLICA):
            result = self.router.db_for_read(model_cls)
        assert result == "replica", (
            f"{model_cls.__name__} read should route to 'replica', got {result!r}"
        )

    @pytest.mark.parametrize("model_cls", [Income, ProfitRecord, AuditLog])
    def test_targeted_model_falls_back_when_no_replica(self, model_cls):
        """
        When DATABASES has no 'replica' key, the router returns None so
        Django's default routing (primary DB) takes over gracefully.

        Requirements: 19.9
        """
        with patch("django.conf.settings.DATABASES", _DATABASES_WITHOUT_REPLICA):
            result = self.router.db_for_read(model_cls)
        assert result is None, (
            f"{model_cls.__name__} read should return None without replica, got {result!r}"
        )

    @pytest.mark.parametrize("model_cls", [Branch, User, Order])
    def test_non_targeted_model_returns_none(self, model_cls):
        """
        Models outside the read-replica set must return None, allowing the
        next router / Django default to handle them.

        Requirements: 19.9
        """
        with patch("django.conf.settings.DATABASES", _DATABASES_WITH_REPLICA):
            result = self.router.db_for_read(model_cls)
        assert result is None, (
            f"{model_cls.__name__} read should return None (no override), got {result!r}"
        )


class TestReadReplicaRouterWriteRouting:
    """db_for_write() always returns None — all writes go to the primary."""

    router = ReadReplicaRouter()

    @pytest.mark.parametrize("model_cls", [Income, ProfitRecord, AuditLog, Branch, User, Order])
    def test_writes_always_return_none(self, model_cls):
        """
        Writes for all models — including targeted ones — must return None
        so that Django's default write routing (primary DB) applies.

        Requirements: 19.9
        """
        with patch("django.conf.settings.DATABASES", _DATABASES_WITH_REPLICA):
            result = self.router.db_for_write(model_cls)
        assert result is None, (
            f"{model_cls.__name__} write should return None, got {result!r}"
        )


class TestReadReplicaRouterRelationsAndMigrations:
    """allow_relation() and allow_migrate() return expected values."""

    router = ReadReplicaRouter()

    def test_allow_relation_returns_true(self):
        """
        Relations between any two objects should always be allowed so that
        cross-DB relations between primary and replica objects work.
        """
        assert self.router.allow_relation(Income, AuditLog) is True

    def test_allow_relation_same_model_returns_true(self):
        assert self.router.allow_relation(Income, Income) is True

    def test_allow_relation_unrelated_models_returns_true(self):
        assert self.router.allow_relation(Branch, Order) is True

    def test_allow_migrate_returns_none(self):
        """
        The router defers migration routing to django-tenants and Django's
        default logic by returning None.
        """
        result = self.router.allow_migrate("default", "financials", model_name="income")
        assert result is None

    def test_allow_migrate_replica_returns_none(self):
        """
        Migrations should NOT run on the replica — the router returns None
        to let Django's built-in logic prevent it.
        """
        result = self.router.allow_migrate("replica", "audit", model_name="auditlog")
        assert result is None


class TestReadReplicaRouterReadModelsSet:
    """Verify the READ_MODELS frozenset contains exactly the expected models."""

    router = ReadReplicaRouter()

    def test_income_in_read_models(self):
        assert "Income" in self.router.READ_MODELS

    def test_profit_record_in_read_models(self):
        assert "ProfitRecord" in self.router.READ_MODELS

    def test_auditlog_in_read_models(self):
        assert "AuditLog" in self.router.READ_MODELS

    def test_expense_in_read_models(self):
        """Expense is also included in the read-replica set for reporting."""
        assert "Expense" in self.router.READ_MODELS

    def test_branch_not_in_read_models(self):
        assert "Branch" not in self.router.READ_MODELS

    def test_user_not_in_read_models(self):
        assert "User" not in self.router.READ_MODELS

    def test_order_not_in_read_models(self):
        assert "Order" not in self.router.READ_MODELS
