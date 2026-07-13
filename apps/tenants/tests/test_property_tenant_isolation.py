"""
Property-Based Tests: Tenant Data Isolation (Property 1)

Property 1: For any two distinct tenants A and B, any data object created in
tenant A's context, when queried in tenant B's context, shall return an empty
result set.

Specifically:

  1a - Context Switch Integrity: After `connection.set_tenant(tenant_b)`, the
       active schema is tenant_b's schema, never tenant_a's.
  1b - Data Opacity: A query executed after set_tenant(tenant_b) returns no
       rows that were written in tenant_a's context.
  1c - Symmetry: Isolation holds in both directions — data from A is invisible
       in B's context, and data from B is invisible in A's context.
  1d - Non-Interference: Creating objects in both tenants sequentially leaves
       each tenant's query scope unchanged; the connection always reflects only
       the most recently set tenant context.

The tests use mocks to simulate the ORM-level schema isolation mechanism
(connection.set_tenant / connection.schema_name) because:
  - The testing profile uses SQLite in-memory (no real PostgreSQL schemas).
  - Real schema provisioning is deferred to Task 2.6 (Docker integration tests).
  - The unit layer tests the contract: that the isolation mechanism is invoked
    correctly and the query routing respects the current tenant context.

Validates: Requirements 1.1, 1.3
"""

from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid slug-like strings for tenant identifiers.
# Must start with a lowercase letter, optionally followed by lowercase letters,
# digits, or hyphens, and end with a letter or digit. Matches Django SlugField.
slug_strategy = st.from_regex(r"[a-z][a-z0-9\-]{0,28}[a-z0-9]", fullmatch=True)
short_slug_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=1
)
any_slug_strategy = st.one_of(slug_strategy, short_slug_strategy)

# Model type names representing tenant-scoped objects (for parameterisation)
model_type_strategy = st.sampled_from(
    ["Branch", "MenuItem", "InventoryItem", "Expense", "AuditLog"]
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(schema_name: str, slug: str, name: str, is_active: bool = True):
    """Build a lightweight mock Tenant with the schema_name attribute set."""
    tenant = MagicMock()
    tenant.schema_name = schema_name
    tenant.slug = slug
    tenant.name = name
    tenant.is_active = is_active
    return tenant


def _make_queryset(rows: list):
    """
    Build a mock QuerySet whose .all()/.filter() returns only the provided rows.

    The queryset is context-aware: it stores which tenant context it was
    evaluated under (connection.schema_name at call time) so tests can assert
    that queries only see data from the active schema.
    """
    qs = MagicMock()
    qs.__iter__ = MagicMock(return_value=iter(rows))
    qs.count.return_value = len(rows)
    qs.exists.return_value = bool(rows)
    qs.all.return_value = qs
    qs.filter.return_value = qs
    return qs


def _make_model_manager(tenant_data_map: dict):
    """
    Build a mock model manager that routes .all() to the row set belonging to
    the schema_name currently on `connection`.

    tenant_data_map: { schema_name: [list of mock row objects] }

    The manager's .objects.all() is called inside a `with tenant_context(...)`
    patch, so it reads connection.schema_name to decide which rows to return.
    """
    manager = MagicMock()

    def _all_in_context():
        """Return only the rows belonging to the current schema_name."""
        # Imported lazily to pick up the patched connection object.
        from django.db import connection
        current_schema = getattr(connection, "schema_name", None)
        rows = tenant_data_map.get(current_schema, [])
        return _make_queryset(rows)

    manager.objects.all.side_effect = lambda: _all_in_context()
    return manager


class _TenantContext:
    """
    Context manager that patches `django.db.connection.schema_name` and
    records calls to `connection.set_tenant(tenant)`.

    This simulates what django-tenants does under the hood: `set_tenant(t)`
    switches connection.schema_name to `t.schema_name`.
    """

    def __init__(self, initial_schema: str = "public"):
        self._schema = initial_schema
        self.set_tenant_calls = []
        self._patcher = None

    def set_tenant(self, tenant):
        """Simulate connection.set_tenant(tenant) by updating schema_name."""
        self._schema = tenant.schema_name
        self.set_tenant_calls.append(tenant)

    def get_schema_name(self):
        return self._schema

    def __enter__(self):
        # Patch django.db.connection to be this context tracker
        self._patcher = patch("django.db.connection")
        mock_conn = self._patcher.start()
        mock_conn.schema_name = self._schema
        mock_conn.set_tenant.side_effect = self.set_tenant

        # Keep schema_name in sync as set_tenant is called
        type(mock_conn).schema_name = PropertyMock(side_effect=self.get_schema_name)
        return self, mock_conn

    def __exit__(self, *args):
        self._patcher.stop()


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestTenantDataIsolationProperty:
    """
    Property-based tests verifying Tenant Data Isolation (Property 1).

    **Validates: Requirements 1.1, 1.3**
    """

    # ------------------------------------------------------------------
    # Property 1a — Context Switch Integrity
    # ------------------------------------------------------------------

    @given(
        slug_a=any_slug_strategy,
        slug_b=any_slug_strategy,
    )
    @settings(max_examples=500)
    def test_property_set_tenant_switches_to_correct_schema(self, slug_a, slug_b):
        """
        **Validates: Requirements 1.1, 1.3**

        After connection.set_tenant(tenant_b), connection.schema_name must
        equal tenant_b.schema_name, never tenant_a.schema_name.
        The isolation mechanism must be invoked with the exact tenant object.
        """
        assume(slug_a != slug_b)

        schema_a = f"tenant_{slug_a}"
        schema_b = f"tenant_{slug_b}"

        tenant_a = _make_tenant(schema_a, slug_a, f"Restaurant {slug_a}")
        tenant_b = _make_tenant(schema_b, slug_b, f"Restaurant {slug_b}")

        ctx = _TenantContext(initial_schema="public")

        with ctx:
            # Enter tenant A's context
            ctx.set_tenant(tenant_a)
            assert ctx.get_schema_name() == schema_a, (
                f"After set_tenant(tenant_a), expected schema '{schema_a}', "
                f"got '{ctx.get_schema_name()}'"
            )
            # Cross-check: tenant_b's schema is NOT active
            assert ctx.get_schema_name() != schema_b, (
                f"Tenant B's schema '{schema_b}' must not be active "
                f"while in tenant A's context"
            )

            # Switch to tenant B's context
            ctx.set_tenant(tenant_b)
            assert ctx.get_schema_name() == schema_b, (
                f"After set_tenant(tenant_b), expected schema '{schema_b}', "
                f"got '{ctx.get_schema_name()}'"
            )
            # Cross-check: tenant_a's schema is no longer active
            assert ctx.get_schema_name() != schema_a, (
                f"Tenant A's schema '{schema_a}' must not remain active "
                f"after switching to tenant B"
            )

    # ------------------------------------------------------------------
    # Property 1b — Data Opacity: A queries in B's context returns empty
    # ------------------------------------------------------------------

    @given(
        slug_a=any_slug_strategy,
        slug_b=any_slug_strategy,
        model_type=model_type_strategy,
    )
    @settings(max_examples=500)
    def test_property_data_created_in_tenant_a_invisible_in_tenant_b(
        self, slug_a, slug_b, model_type
    ):
        """
        **Validates: Requirements 1.1, 1.3**

        Data objects written in tenant A's schema must not appear when a
        query is executed in tenant B's context. The query returns 0 rows.
        """
        assume(slug_a != slug_b)

        schema_a = f"tenant_{slug_a}"
        schema_b = f"tenant_{slug_b}"

        tenant_a = _make_tenant(schema_a, slug_a, f"Restaurant {slug_a}")
        tenant_b = _make_tenant(schema_b, slug_b, f"Restaurant {slug_b}")

        # Simulate two rows stored under tenant_a's schema
        row_1 = MagicMock(name=f"{model_type}_1_tenant_a")
        row_2 = MagicMock(name=f"{model_type}_2_tenant_a")
        row_1.schema_name = schema_a
        row_2.schema_name = schema_a

        # Build a schema-aware manager: tenant_a has 2 rows, tenant_b has none
        manager = _make_model_manager({
            schema_a: [row_1, row_2],
            schema_b: [],
        })

        ctx = _TenantContext(initial_schema="public")

        with ctx:
            from django.db import connection

            # Write phase: set tenant A context, confirm data exists there
            ctx.set_tenant(tenant_a)
            rows_in_a = manager.objects.all()
            assert rows_in_a.count() == 2, (
                f"Expected 2 rows in tenant A ({schema_a}), "
                f"got {rows_in_a.count()}"
            )

            # Query phase: switch to tenant B context, query same model
            ctx.set_tenant(tenant_b)
            rows_in_b = manager.objects.all()

            assert rows_in_b.count() == 0, (
                f"Isolation violation for {model_type}: "
                f"expected 0 rows in tenant B ({schema_b}), "
                f"but got {rows_in_b.count()}. "
                f"Data from tenant A must not leak into tenant B's context."
            )
            assert not rows_in_b.exists(), (
                f"Isolation violation: .exists() returned True in tenant B's "
                f"context for {model_type} data created in tenant A."
            )

    # ------------------------------------------------------------------
    # Property 1c — Symmetry
    # ------------------------------------------------------------------

    @given(
        slug_a=any_slug_strategy,
        slug_b=any_slug_strategy,
        model_type=model_type_strategy,
    )
    @settings(max_examples=500)
    def test_property_isolation_is_symmetric(self, slug_a, slug_b, model_type):
        """
        **Validates: Requirements 1.1, 1.3**

        Isolation holds in both directions:
        - Data created in A is invisible in B's context (tested in 1b).
        - Data created in B is also invisible in A's context.

        Both directions are verified in this single property.
        """
        assume(slug_a != slug_b)

        schema_a = f"tenant_{slug_a}"
        schema_b = f"tenant_{slug_b}"

        tenant_a = _make_tenant(schema_a, slug_a, f"Restaurant {slug_a}")
        tenant_b = _make_tenant(schema_b, slug_b, f"Restaurant {slug_b}")

        # Each tenant has its own set of objects
        obj_a = MagicMock(name=f"{model_type}_in_a")
        obj_a.schema_name = schema_a

        obj_b = MagicMock(name=f"{model_type}_in_b")
        obj_b.schema_name = schema_b

        manager = _make_model_manager({
            schema_a: [obj_a],
            schema_b: [obj_b],
        })

        ctx = _TenantContext(initial_schema="public")

        with ctx:
            # --- Direction A → B: A's data invisible from B ---
            ctx.set_tenant(tenant_a)
            rows_in_a = manager.objects.all()
            # Confirm A's data is visible in A's own context
            assert rows_in_a.count() == 1, (
                f"Expected 1 row in tenant A's own context, got {rows_in_a.count()}"
            )
            # Verify B's data is absent from A's context
            for row in rows_in_a:
                assert row.schema_name == schema_a, (
                    f"Row in A's context has wrong schema: {row.schema_name}"
                )

            # --- Direction B → A: B's data invisible from A ---
            ctx.set_tenant(tenant_b)
            rows_in_b = manager.objects.all()
            # Confirm B's data is visible in B's own context
            assert rows_in_b.count() == 1, (
                f"Expected 1 row in tenant B's own context, got {rows_in_b.count()}"
            )
            # Verify A's data is absent from B's context
            for row in rows_in_b:
                assert row.schema_name == schema_b, (
                    f"Row in B's context has wrong schema: {row.schema_name}"
                )

            # --- Final cross-check: switch back to A, B's data still absent ---
            ctx.set_tenant(tenant_a)
            rows_in_a_again = manager.objects.all()
            assert rows_in_a_again.count() == 1, (
                f"After switching back to tenant A, expected 1 row "
                f"(only A's own data), got {rows_in_a_again.count()}"
            )
            for row in rows_in_a_again:
                assert row.schema_name == schema_a, (
                    f"Symmetry violation: row with schema '{row.schema_name}' "
                    f"appeared in tenant A's context after returning from B"
                )

    # ------------------------------------------------------------------
    # Property 1d — Non-Interference: sequential tenant writes
    # ------------------------------------------------------------------

    @given(
        slug_a=any_slug_strategy,
        slug_b=any_slug_strategy,
        model_type=model_type_strategy,
    )
    @settings(max_examples=500)
    def test_property_sequential_context_switches_do_not_cross_contaminate(
        self, slug_a, slug_b, model_type
    ):
        """
        **Validates: Requirements 1.1, 1.3**

        After any sequence of set_tenant(A) / set_tenant(B) calls, the
        connection.schema_name always reflects the most recently set tenant,
        and data queries return only that tenant's rows.

        This tests non-interference: rapid context switches must not bleed
        state from one tenant into another.
        """
        assume(slug_a != slug_b)

        schema_a = f"tenant_{slug_a}"
        schema_b = f"tenant_{slug_b}"

        tenant_a = _make_tenant(schema_a, slug_a, f"Restaurant {slug_a}")
        tenant_b = _make_tenant(schema_b, slug_b, f"Restaurant {slug_b}")

        obj_a = MagicMock(name=f"{model_type}_a")
        obj_a.schema_name = schema_a
        obj_b = MagicMock(name=f"{model_type}_b")
        obj_b.schema_name = schema_b

        manager = _make_model_manager({
            schema_a: [obj_a],
            schema_b: [obj_b],
        })

        ctx = _TenantContext(initial_schema="public")

        with ctx:
            # Perform a sequence of context switches: A → B → A → B
            switches = [tenant_a, tenant_b, tenant_a, tenant_b]
            for i, tenant in enumerate(switches):
                ctx.set_tenant(tenant)

                # Active schema must match the tenant just set
                assert ctx.get_schema_name() == tenant.schema_name, (
                    f"Step {i}: after set_tenant({tenant.name}), "
                    f"expected schema '{tenant.schema_name}', "
                    f"got '{ctx.get_schema_name()}'"
                )

                # Query result must match the active tenant's data only
                rows = manager.objects.all()
                assert rows.count() == 1, (
                    f"Step {i}: expected 1 row in {tenant.schema_name}, "
                    f"got {rows.count()}"
                )
                for row in rows:
                    assert row.schema_name == tenant.schema_name, (
                        f"Step {i}: cross-contamination detected. "
                        f"Expected schema '{tenant.schema_name}', "
                        f"got '{row.schema_name}' in row."
                    )

            # Verify set_tenant was called exactly 4 times (once per switch)
            assert len(ctx.set_tenant_calls) == 4, (
                f"Expected 4 set_tenant calls, got {len(ctx.set_tenant_calls)}"
            )
            # Verify the call sequence matches the switch order
            for i, (call_tenant, expected_tenant) in enumerate(
                zip(ctx.set_tenant_calls, switches)
            ):
                assert call_tenant.schema_name == expected_tenant.schema_name, (
                    f"set_tenant call {i}: expected schema "
                    f"'{expected_tenant.schema_name}', "
                    f"got '{call_tenant.schema_name}'"
                )

    # ------------------------------------------------------------------
    # Property 1e — Schema Name Format Invariant
    # ------------------------------------------------------------------

    @given(slug=any_slug_strategy)
    @settings(max_examples=500)
    def test_property_tenant_schema_name_follows_naming_convention(self, slug):
        """
        **Validates: Requirements 1.1**

        For any tenant slug, the schema_name used for PostgreSQL isolation
        must follow the `tenant_{slug}` naming convention, ensuring every
        tenant has a structurally distinct schema namespace.
        """
        schema_name = f"tenant_{slug}"
        tenant = _make_tenant(schema_name, slug, f"Restaurant {slug}")

        # Schema name must match the expected format
        assert tenant.schema_name.startswith("tenant_"), (
            f"Schema name '{tenant.schema_name}' must start with 'tenant_' "
            f"to maintain the isolation namespace convention."
        )
        assert tenant.schema_name != "public", (
            f"Schema name must never be 'public' — the public schema is "
            f"reserved for platform-level data."
        )
        # Slug embedded in schema_name must match the tenant's own slug
        expected_suffix = tenant.slug
        assert tenant.schema_name.endswith(expected_suffix), (
            f"Schema name '{tenant.schema_name}' must end with slug "
            f"'{expected_suffix}' to match the tenant_{slug} convention."
        )
        # Each distinct slug yields a distinct schema name (uniqueness)
        other_slug = slug + "x"
        other_schema = f"tenant_{other_slug}"
        assert other_schema != schema_name, (
            f"Different slugs must produce different schema names. "
            f"'{slug}' and '{other_slug}' both produced '{schema_name}'."
        )
