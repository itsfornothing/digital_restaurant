"""
shared/db_router.py

ReadReplicaRouter — routes read queries for reporting/analytics models to
the ``replica`` PostgreSQL database when it is configured, and falls back
to ``default`` gracefully when no replica is available (e.g. development
and staging environments without a replica).

Targeted models
---------------
The following models generate high read volume from FinancialService report
methods and AuditLog read operations.  Routing them to a replica reduces
load on the primary write database:

  - Income         (apps/financials/models.py) — used in P&L reports
  - ProfitRecord   (apps/financials/models.py) — pre-computed profit snapshots
  - Expense        (apps/expenses/models.py)   — used in expense reports
  - AuditLog       (apps/audit/models.py)      — high-volume, read-heavy

All write operations always go to ``default`` (the primary).

Configuration
-------------
Add this router AFTER the django-tenants router in settings/base.py:

    DATABASE_ROUTERS = [
        "django_tenants.routers.TenantSyncRouter",
        "shared.db_router.ReadReplicaRouter",
    ]

Add the replica database in settings/production.py:

    DATABASES['replica'] = { ... }

The router uses ``settings.DATABASES`` at call time, so the replica config
can be added or removed without changing any application code.

Requirements: 19.9 (Task 20.4)
"""


class ReadReplicaRouter:
    """
    Route read queries from FinancialService report methods and AuditLog
    read operations to the 'replica' database when available.
    Falls back to 'default' if 'replica' is not configured.

    This router only affects read routing for the models listed in
    READ_MODELS.  All writes always return None (letting Django use the
    default primary database).

    Requirements: 19.9
    """

    # Model class names whose SELECT queries should go to the replica.
    # Use __name__ (not the full app-qualified label) for simplicity.
    READ_MODELS = frozenset(
        {
            "Income",       # apps/financials/models.py
            "ProfitRecord", # apps/financials/models.py
            "Expense",      # apps/expenses/models.py
            "AuditLog",     # apps/audit/models.py
        }
    )

    def db_for_read(self, model, **hints):
        """
        Route reads for reporting/analytics models to the replica.

        Returns ``'replica'`` if:
          - The model is in READ_MODELS, AND
          - A 'replica' key exists in settings.DATABASES, AND
          - The current environment is NOT a test run (to avoid SQLite
            in-memory database locking issues in pytest).

        Otherwise returns ``None`` (defer to the next router or default).
        """
        from django.conf import settings

        if model.__name__ in self.READ_MODELS:
            if "replica" in settings.DATABASES:
                # Do NOT route to replica during test execution.
                # The test replica is an in-memory SQLite mirror of default,
                # and concurrent access causes "database table is locked" errors.
                # Detection: Django's test runner sets TEST_RUNNER or uses the
                # in-memory SQLite sentinel value ':memory:'.
                replica_db = settings.DATABASES["replica"]
                is_test_mirror = (
                    replica_db.get("TEST", {}).get("MIRROR") is not None
                    or replica_db.get("NAME") == ":memory:"
                )
                if not is_test_mirror:
                    return "replica"
        return None

    def db_for_write(self, model, **hints):
        """
        All writes go to the primary database (default).
        Returning None lets django-tenants / Django's default logic take over.
        """
        return None  # always default

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations between any two models.

        Because we only route reads (not writes) to the replica, and because
        the replica mirrors the primary, relations are always valid between
        objects regardless of which database was used to fetch them.
        """
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Defer migration routing to django-tenants and Django's default logic.
        Returns None to indicate this router has no opinion on migration routing.
        """
        return None
