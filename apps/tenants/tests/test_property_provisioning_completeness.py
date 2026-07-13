"""
Property-Based Tests: Tenant Provisioning Completeness (Property 3)

Property 3: For any valid tenant creation payload (unique name, unique slug,
valid plan, valid owner email), calling ProvisioningService.create_tenant()
shall produce:
  1. Exactly one Tenant record saved with the correct slug/schema
  2. Exactly one Domain record pointing to that tenant
  3. Exactly one User with Tenant_Owner role
  4. The SubscriptionPlan assigned to that tenant (subscription created)

Sub-properties tested:
  3a - Schema Naming: tenant's schema_name == f"tenant_{slug}"
  3b - Domain Created: Domain.objects.create called once with tenant + is_primary=True
  3c - Owner User Created: _create_owner_user called once with the given email
  3d - Subscription Created: _create_subscription called once for non-None plan
  3e - Tenant Activated: is_active=True and save(update_fields=["is_active"]) called once
  3f - Completeness Invariant: all of the above hold simultaneously for any valid payload
  3g - Slug Uniqueness Rejection: duplicate slug raises TenantAlreadyExists, no side effects

Validates: Requirements 1.4
"""

import contextlib
from unittest.mock import MagicMock, patch, call

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid slug: lowercase letter, then letters/digits/hyphens, ends with letter/digit
slug_strategy = st.from_regex(r"[a-z][a-z0-9\-]{0,28}[a-z0-9]", fullmatch=True)

# Single-character slugs (the regex above requires >= 2 chars)
short_slug_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=1
)

any_slug_strategy = st.one_of(slug_strategy, short_slug_strategy)

# Tenant name: letters and digits, 1–100 characters
name_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
)

# Owner email — simple regex-based to avoid Hypothesis email edge-cases with mocking
email_strategy = st.from_regex(r"[a-z]+@[a-z]+\.[a-z]+", fullmatch=True)

# Plan ID: any positive integer
plan_id_strategy = st.integers(min_value=1, max_value=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_tenant(slug: str) -> MagicMock:
    """Build a lightweight mock Tenant with realistic attribute defaults."""
    t = MagicMock()
    t.pk = 1
    t.slug = slug
    t.schema_name = f"tenant_{slug}"
    t.name = f"Restaurant {slug}"
    t.is_active = False
    return t


def _build_patches(mock_tenant: MagicMock, mock_plan=None) -> list:
    """
    Return a list of `patch()` objects covering all external dependencies of
    ProvisioningService.create_tenant().

    The caller is responsible for starting/stopping them (or using ExitStack).
    """
    MockTenant = MagicMock(return_value=mock_tenant)
    MockTenant.objects.filter.return_value.exists.return_value = False
    MockTenant.DoesNotExist = Exception

    MockDomain = MagicMock()

    mock_owner_user = MagicMock()
    mock_owner_user.pk = "owner-uuid"

    return [
        patch("apps.tenants.services.Tenant", MockTenant),
        patch("apps.tenants.services.Domain", MockDomain),
        patch(
            "apps.tenants.services.ProvisioningService._run_migrate_schemas"
        ),
        patch(
            "apps.tenants.services.ProvisioningService._create_tenant_config"
        ),
        patch(
            "apps.tenants.services.ProvisioningService._create_owner_user",
            return_value=mock_owner_user,
        ),
        patch(
            "apps.tenants.services.ProvisioningService._create_subscription"
        ),
        patch(
            "apps.tenants.services.ProvisioningService._resolve_plan",
            return_value=mock_plan,
        ),
        patch("apps.tenants.services.connection"),
        patch(
            "apps.tenants.services.ProvisioningService._restore_public_schema"
        ),
        patch("apps.tenants.services.transaction"),
    ]


def _run_create_tenant(mock_tenant, slug, name, owner_email, plan_id, mock_plan=None):
    """
    Execute ProvisioningService.create_tenant() with all external dependencies
    mocked. Returns (result, active_mocks_dict).

    active_mocks_dict keys:
        MockTenant, MockDomain, mock_migrate, mock_config,
        mock_create_owner, mock_create_sub, mock_resolve_plan,
        mock_connection, mock_restore, mock_transaction
    """
    from apps.tenants.services import ProvisioningService

    patches = _build_patches(mock_tenant, mock_plan=mock_plan)

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        (
            MockTenant,
            MockDomain,
            mock_migrate,
            mock_config,
            mock_create_owner,
            mock_create_sub,
            mock_resolve_plan,
            mock_connection,
            mock_restore,
            mock_transaction,
        ) = mocks

        result = ProvisioningService().create_tenant(
            name=name,
            slug=slug,
            plan_id=plan_id,
            owner_email=owner_email,
        )

        active = {
            "MockTenant": MockTenant,
            "MockDomain": MockDomain,
            "mock_migrate": mock_migrate,
            "mock_config": mock_config,
            "mock_create_owner": mock_create_owner,
            "mock_create_sub": mock_create_sub,
            "mock_resolve_plan": mock_resolve_plan,
            "mock_connection": mock_connection,
            "mock_restore": mock_restore,
            "mock_transaction": mock_transaction,
        }

    return result, active


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestProvisioningCompletenessProperty:
    """
    Property-based tests verifying Tenant Provisioning Completeness (Property 3).

    **Validates: Requirements 1.4**
    """

    # ------------------------------------------------------------------
    # Property 3a — Schema Naming
    # ------------------------------------------------------------------

    @given(slug=any_slug_strategy)
    @settings(max_examples=500)
    def test_property_schema_name_equals_tenant_prefix_plus_slug(self, slug):
        """
        **Validates: Requirements 1.4**

        For any valid slug, the provisioned tenant's schema_name must equal
        'tenant_{slug}' exactly — no other naming is acceptable.
        """
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(slug)
        # schema_name is set to the correct value by _make_mock_tenant;
        # we verify the service constructs it consistently.
        expected_schema = f"tenant_{slug}"

        _, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=f"Restaurant {slug}",
            owner_email="owner@test.com",
            plan_id=1,
        )

        # The service passes the schema_name to _run_migrate_schemas
        active["mock_migrate"].assert_called_once_with(expected_schema)

        # The mock tenant returned has the correct schema_name
        assert mock_tenant.schema_name == expected_schema, (
            f"Expected schema_name='tenant_{slug}', "
            f"got '{mock_tenant.schema_name}'"
        )

    # ------------------------------------------------------------------
    # Property 3b — Domain Created
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        name=name_strategy,
        owner_email=email_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_exactly_one_domain_record_created(
        self, slug, name, owner_email, plan_id
    ):
        """
        **Validates: Requirements 1.4**

        For any valid creation payload, exactly one Domain record must be
        created pointing to the tenant with is_primary=True.
        """
        from apps.tenants.services import ProvisioningService

        mock_tenant = _make_mock_tenant(slug)

        _, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=name,
            owner_email=owner_email,
            plan_id=plan_id,
        )

        MockDomain = active["MockDomain"]

        # Exactly one Domain.objects.create call
        assert MockDomain.objects.create.call_count == 1, (
            f"Expected exactly 1 Domain.objects.create call, "
            f"got {MockDomain.objects.create.call_count}"
        )

        # Must be called with tenant= the provisioned tenant
        create_kwargs = MockDomain.objects.create.call_args[1]
        assert create_kwargs.get("tenant") is mock_tenant, (
            f"Domain.objects.create must receive tenant={mock_tenant!r}, "
            f"got {create_kwargs.get('tenant')!r}"
        )
        assert create_kwargs.get("is_primary") is True, (
            f"Domain.objects.create must receive is_primary=True, "
            f"got {create_kwargs.get('is_primary')!r}"
        )

    # ------------------------------------------------------------------
    # Property 3c — Owner User Created
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        owner_email=email_strategy,
    )
    @settings(max_examples=500)
    def test_property_owner_user_created_with_correct_email(self, slug, owner_email):
        """
        **Validates: Requirements 1.4**

        For any valid owner_email, _create_owner_user must be called exactly
        once with that exact email address.
        """
        mock_tenant = _make_mock_tenant(slug)

        _, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=f"Restaurant {slug}",
            owner_email=owner_email,
            plan_id=1,
        )

        mock_create_owner = active["mock_create_owner"]

        assert mock_create_owner.call_count == 1, (
            f"Expected _create_owner_user called once, "
            f"got {mock_create_owner.call_count}"
        )
        actual_email = mock_create_owner.call_args[0][0]
        assert actual_email == owner_email, (
            f"Expected _create_owner_user('{owner_email}'), "
            f"but called with '{actual_email}'"
        )

    # ------------------------------------------------------------------
    # Property 3d — Subscription Created (when plan is not None)
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_subscription_created_for_non_none_plan(self, slug, plan_id):
        """
        **Validates: Requirements 1.4**

        When the resolved plan is not None, _create_subscription must be
        called exactly once with the tenant and the resolved plan object.
        """
        mock_tenant = _make_mock_tenant(slug)
        mock_plan = MagicMock()
        mock_plan.pk = plan_id
        mock_plan.name = f"Plan {plan_id}"

        _, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=f"Restaurant {slug}",
            owner_email="owner@test.com",
            plan_id=plan_id,
            mock_plan=mock_plan,
        )

        mock_create_sub = active["mock_create_sub"]

        assert mock_create_sub.call_count == 1, (
            f"Expected _create_subscription called once for non-None plan, "
            f"got {mock_create_sub.call_count}"
        )
        sub_args = mock_create_sub.call_args[0]
        assert sub_args[0] is mock_tenant, (
            f"_create_subscription must receive the tenant as first arg"
        )
        assert sub_args[1] is mock_plan, (
            f"_create_subscription must receive the resolved plan as second arg"
        )

    @given(
        slug=any_slug_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_subscription_not_created_when_plan_is_none(self, slug, plan_id):
        """
        **Validates: Requirements 1.4**

        When _resolve_plan returns None (e.g. billing not yet active),
        _create_subscription must NOT be called.
        """
        mock_tenant = _make_mock_tenant(slug)

        # mock_plan=None is the default — subscription is skipped
        _, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=f"Restaurant {slug}",
            owner_email="owner@test.com",
            plan_id=plan_id,
            mock_plan=None,
        )

        assert active["mock_create_sub"].call_count == 0, (
            "Expected _create_subscription NOT called when plan is None, "
            f"but it was called {active['mock_create_sub'].call_count} time(s)"
        )

    # ------------------------------------------------------------------
    # Property 3e — Tenant Activated
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        name=name_strategy,
        owner_email=email_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_tenant_is_activated_after_create(
        self, slug, name, owner_email, plan_id
    ):
        """
        **Validates: Requirements 1.4**

        After create_tenant returns, the tenant's is_active must be True and
        save(update_fields=["is_active"]) must have been called exactly once.
        """
        mock_tenant = _make_mock_tenant(slug)

        result, _ = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=name,
            owner_email=owner_email,
            plan_id=plan_id,
        )

        assert mock_tenant.is_active is True, (
            f"Expected tenant.is_active=True after create_tenant, "
            f"got {mock_tenant.is_active}"
        )

        # Exactly one save call with update_fields=["is_active"]
        activation_saves = [
            c
            for c in mock_tenant.save.call_args_list
            if c[1].get("update_fields") == ["is_active"]
        ]
        assert len(activation_saves) == 1, (
            f"Expected exactly one save(update_fields=['is_active']), "
            f"got {len(activation_saves)} such call(s). "
            f"All save calls: {mock_tenant.save.call_args_list}"
        )

    # ------------------------------------------------------------------
    # Property 3f — Completeness Invariant (all sub-properties together)
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        name=name_strategy,
        owner_email=email_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_completeness_invariant_all_artifacts_created(
        self, slug, name, owner_email, plan_id
    ):
        """
        **Validates: Requirements 1.4**

        For any valid (name, slug, owner_email, plan_id) combination, ALL of
        the following must hold simultaneously after create_tenant returns:
          - One Tenant record saved (initial save triggered)
          - One Domain record created (pointing to tenant, is_primary=True)
          - One owner user created with the given email
          - Tenant is activated (is_active=True)
          - _run_migrate_schemas called with 'tenant_{slug}'
        """
        mock_tenant = _make_mock_tenant(slug)
        mock_plan = MagicMock()
        mock_plan.pk = plan_id

        result, active = _run_create_tenant(
            mock_tenant=mock_tenant,
            slug=slug,
            name=name,
            owner_email=owner_email,
            plan_id=plan_id,
            mock_plan=mock_plan,
        )

        expected_schema = f"tenant_{slug}"

        # --- 1. One Tenant record saved ---
        assert mock_tenant.save.call_count >= 1, (
            "Tenant.save() must be called at least once (initial creation)"
        )

        # --- 2. One Domain record created pointing to this tenant with is_primary=True ---
        MockDomain = active["MockDomain"]
        assert MockDomain.objects.create.call_count == 1, (
            f"Expected exactly 1 Domain.objects.create, "
            f"got {MockDomain.objects.create.call_count}"
        )
        domain_kwargs = MockDomain.objects.create.call_args[1]
        assert domain_kwargs.get("tenant") is mock_tenant, (
            "Domain must point to the provisioned tenant"
        )
        assert domain_kwargs.get("is_primary") is True, (
            "Domain must be created with is_primary=True"
        )

        # --- 3. One owner user created with the given email ---
        mock_create_owner = active["mock_create_owner"]
        assert mock_create_owner.call_count == 1, (
            f"Expected 1 call to _create_owner_user, got {mock_create_owner.call_count}"
        )
        assert mock_create_owner.call_args[0][0] == owner_email, (
            f"_create_owner_user must be called with '{owner_email}', "
            f"got '{mock_create_owner.call_args[0][0]}'"
        )

        # --- 4. Tenant is activated ---
        assert mock_tenant.is_active is True, (
            "Tenant must be activated (is_active=True) after provisioning"
        )
        activation_saves = [
            c
            for c in mock_tenant.save.call_args_list
            if c[1].get("update_fields") == ["is_active"]
        ]
        assert len(activation_saves) == 1, (
            f"Expected exactly one save(update_fields=['is_active']), "
            f"got {len(activation_saves)}"
        )

        # --- 5. _run_migrate_schemas called with 'tenant_{slug}' ---
        active["mock_migrate"].assert_called_once_with(expected_schema)

        # --- 6. Subscription created (plan is non-None in this sub-test) ---
        mock_create_sub = active["mock_create_sub"]
        assert mock_create_sub.call_count == 1, (
            f"Expected 1 call to _create_subscription for non-None plan, "
            f"got {mock_create_sub.call_count}"
        )

    # ------------------------------------------------------------------
    # Property 3g — Slug Uniqueness Rejection
    # ------------------------------------------------------------------

    @given(
        slug=any_slug_strategy,
        name=name_strategy,
        owner_email=email_strategy,
        plan_id=plan_id_strategy,
    )
    @settings(max_examples=500)
    def test_property_duplicate_slug_raises_and_no_side_effects(
        self, slug, name, owner_email, plan_id
    ):
        """
        **Validates: Requirements 1.4**

        For any slug that already exists, create_tenant must raise
        TenantAlreadyExists immediately. No Domain record, no schema migration,
        no owner user creation, and no subscription must be triggered.
        """
        from apps.tenants.services import ProvisioningService, TenantAlreadyExists

        mock_tenant = _make_mock_tenant(slug)

        # MockTenant.objects.filter(...).exists() returns True → slug taken
        MockTenant = MagicMock(return_value=mock_tenant)
        MockTenant.objects.filter.return_value.exists.return_value = True
        MockTenant.DoesNotExist = Exception

        MockDomain = MagicMock()
        mock_migrate = MagicMock()
        mock_create_owner = MagicMock(return_value=MagicMock(pk="u"))
        mock_create_sub = MagicMock()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("apps.tenants.services.Tenant", MockTenant))
            stack.enter_context(patch("apps.tenants.services.Domain", MockDomain))
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._run_migrate_schemas",
                    mock_migrate,
                )
            )
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._create_tenant_config"
                )
            )
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._create_owner_user",
                    mock_create_owner,
                )
            )
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._create_subscription",
                    mock_create_sub,
                )
            )
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._resolve_plan",
                    return_value=MagicMock(),
                )
            )
            stack.enter_context(patch("apps.tenants.services.connection"))
            stack.enter_context(
                patch(
                    "apps.tenants.services.ProvisioningService._restore_public_schema"
                )
            )
            stack.enter_context(patch("apps.tenants.services.transaction"))

            with pytest.raises(TenantAlreadyExists):
                ProvisioningService().create_tenant(
                    name=name,
                    slug=slug,
                    plan_id=plan_id,
                    owner_email=owner_email,
                )

        # No side effects must have occurred
        assert MockDomain.objects.create.call_count == 0, (
            "Domain.objects.create must NOT be called for a duplicate slug"
        )
        assert mock_migrate.call_count == 0, (
            "_run_migrate_schemas must NOT be called for a duplicate slug"
        )
        assert mock_create_owner.call_count == 0, (
            "_create_owner_user must NOT be called for a duplicate slug"
        )
        assert mock_create_sub.call_count == 0, (
            "_create_subscription must NOT be called for a duplicate slug"
        )
