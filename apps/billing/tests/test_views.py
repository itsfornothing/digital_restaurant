"""
apps/billing/tests/test_views.py

Integration tests for billing API endpoints:

    GET  /api/v1/plans/                      → 200 with list
    POST /api/v1/plans/                      → 201 with new plan (Super_Admin)
    PATCH /api/v1/plans/{id}/               → 200 with updated plan
    POST /api/v1/plans/                      → 403 for non-Super_Admin

    POST /api/v1/tenants/{id}/subscription/ → 201/200 with subscription
    GET  /api/v1/tenants/{id}/usage/        → 200 with usage data
    GET  /api/v1/tenants/{id}/usage/        → 404 for non-existent tenant

All tests use the SQLite in-memory DB (config.settings.testing).

Requirements: 2.1, 2.2, 2.5, 2.6
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.models import UserRole
from apps.billing.models import SubscriptionPlan, TenantSubscription
from apps.tenants.models import Tenant

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_tenant(slug: str) -> Tenant:
    return Tenant.objects.create(
        schema_name=slug,
        name=f"Test Tenant {slug}",
        slug=slug,
        is_active=True,
    )


def _make_plan(
    name: str = "Starter",
    max_branches: int = 5,
    max_menu_items: int = 50,
    max_staff_accounts: int = 10,
    price_etb: str = "500.00",
) -> SubscriptionPlan:
    return SubscriptionPlan.objects.create(
        name=name,
        max_branches=max_branches,
        max_menu_items=max_menu_items,
        max_staff_accounts=max_staff_accounts,
        price_etb=price_etb,
    )


def _make_subscription(tenant: Tenant, plan: SubscriptionPlan) -> TenantSubscription:
    return TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        status=TenantSubscription.Status.ACTIVE,
        current_period_start=date.today(),
        current_period_end=date(2099, 12, 31),
    )


def _super_admin_client() -> tuple[APIClient, User]:
    user = User.objects.create_user(
        email="admin@platform.et",
        password="AdminPass123!",
        role=UserRole.SUPER_ADMIN,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _non_admin_client() -> tuple[APIClient, User]:
    user = User.objects.create_user(
        email="owner@restaurant.et",
        password="OwnerPass123!",
        role=UserRole.TENANT_OWNER,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


# ---------------------------------------------------------------------------
# Tests — GET /api/v1/plans/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListPlans:
    def test_super_admin_gets_plan_list(self):
        """GET /api/v1/plans/ as Super_Admin returns 200 with list of plans."""
        _make_plan(name="Starter")
        _make_plan(name="Pro", price_etb="1000.00")
        client, _ = _super_admin_client()

        response = client.get("/api/v1/plans/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        names = {item["name"] for item in data}
        assert names == {"Starter", "Pro"}

    def test_empty_plan_list(self):
        """GET /api/v1/plans/ returns empty list when no plans exist."""
        client, _ = _super_admin_client()

        response = client.get("/api/v1/plans/")

        assert response.status_code == 200
        assert response.json() == []

    def test_non_admin_cannot_list_plans(self):
        """GET /api/v1/plans/ as non-Super_Admin returns 403."""
        _make_plan()
        client, _ = _non_admin_client()

        response = client.get("/api/v1/plans/")

        assert response.status_code == 403

    def test_unauthenticated_cannot_list_plans(self):
        """GET /api/v1/plans/ without authentication returns 403."""
        client = APIClient()

        response = client.get("/api/v1/plans/")

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/plans/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreatePlan:
    def test_super_admin_creates_plan(self):
        """POST /api/v1/plans/ as Super_Admin returns 201 with created plan."""
        client, _ = _super_admin_client()

        payload = {
            "name": "Enterprise",
            "max_branches": 20,
            "max_menu_items": 500,
            "max_staff_accounts": 100,
            "feature_flags": {"white_label_domain": True},
            "price_etb": "2500.00",
        }

        response = client.post("/api/v1/plans/", data=payload, format="json")

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Enterprise"
        assert data["max_branches"] == 20
        assert data["max_menu_items"] == 500
        assert data["max_staff_accounts"] == 100
        assert data["price_etb"] == "2500.00"
        assert data["feature_flags"] == {"white_label_domain": True}
        assert SubscriptionPlan.objects.filter(name="Enterprise").exists()

    def test_create_plan_with_minimal_fields(self):
        """POST /api/v1/plans/ with minimal required fields creates a plan."""
        client, _ = _super_admin_client()

        payload = {
            "name": "Basic",
            "max_branches": 1,
            "max_menu_items": 10,
            "max_staff_accounts": 3,
            "price_etb": "100.00",
        }

        response = client.post("/api/v1/plans/", data=payload, format="json")

        assert response.status_code == 201
        assert response.json()["name"] == "Basic"

    def test_non_admin_cannot_create_plan(self):
        """POST /api/v1/plans/ as non-Super_Admin returns 403."""
        client, _ = _non_admin_client()

        payload = {
            "name": "Enterprise",
            "max_branches": 20,
            "max_menu_items": 500,
            "max_staff_accounts": 100,
            "price_etb": "2500.00",
        }

        response = client.post("/api/v1/plans/", data=payload, format="json")

        assert response.status_code == 403
        assert not SubscriptionPlan.objects.filter(name="Enterprise").exists()

    def test_unauthenticated_cannot_create_plan(self):
        """POST /api/v1/plans/ without authentication returns 403."""
        client = APIClient()

        payload = {
            "name": "Hacked Plan",
            "max_branches": 999,
            "max_menu_items": 9999,
            "max_staff_accounts": 9999,
            "price_etb": "0.00",
        }

        response = client.post("/api/v1/plans/", data=payload, format="json")

        assert response.status_code == 403

    def test_duplicate_plan_name_returns_400(self):
        """POST /api/v1/plans/ with a duplicate name returns 400."""
        _make_plan(name="Starter")
        client, _ = _super_admin_client()

        payload = {
            "name": "Starter",
            "max_branches": 5,
            "max_menu_items": 50,
            "max_staff_accounts": 10,
            "price_etb": "500.00",
        }

        response = client.post("/api/v1/plans/", data=payload, format="json")

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Tests — PATCH /api/v1/plans/{id}/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartialUpdatePlan:
    def test_super_admin_patches_plan(self):
        """PATCH /api/v1/plans/{id}/ as Super_Admin returns 200 with updated plan."""
        plan = _make_plan(name="Starter", max_branches=5)
        client, _ = _super_admin_client()

        response = client.patch(
            f"/api/v1/plans/{plan.pk}/",
            data={"max_branches": 10},
            format="json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["max_branches"] == 10
        assert data["name"] == "Starter"  # unchanged
        plan.refresh_from_db()
        assert plan.max_branches == 10

    def test_patch_plan_name(self):
        """PATCH /api/v1/plans/{id}/ can update the plan name."""
        plan = _make_plan(name="Old Name")
        client, _ = _super_admin_client()

        response = client.patch(
            f"/api/v1/plans/{plan.pk}/",
            data={"name": "New Name"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    def test_patch_nonexistent_plan_returns_404(self):
        """PATCH /api/v1/plans/9999/ returns 404."""
        client, _ = _super_admin_client()

        response = client.patch(
            "/api/v1/plans/9999/",
            data={"max_branches": 10},
            format="json",
        )

        assert response.status_code == 404

    def test_non_admin_cannot_patch_plan(self):
        """PATCH /api/v1/plans/{id}/ as non-Super_Admin returns 403."""
        plan = _make_plan()
        client, _ = _non_admin_client()

        response = client.patch(
            f"/api/v1/plans/{plan.pk}/",
            data={"max_branches": 100},
            format="json",
        )

        assert response.status_code == 403
        plan.refresh_from_db()
        assert plan.max_branches == 5  # unchanged


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/tenants/{id}/subscription/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAssignSubscription:
    def test_assign_plan_creates_subscription(self):
        """POST /api/v1/tenants/{id}/subscription/ creates a subscription → 201."""
        tenant = _make_tenant("sub_tenant_a")
        plan = _make_plan(name="Starter")
        client, _ = _super_admin_client()

        payload = {
            "plan_id": plan.pk,
            "status": "active",
            "current_period_start": "2025-01-01",
            "current_period_end": "2026-01-01",
        }

        response = client.post(
            f"/api/v1/tenants/{tenant.pk}/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["plan"]["name"] == "Starter"
        assert data["status"] == "active"
        assert TenantSubscription.objects.filter(tenant=tenant).exists()

    def test_assign_plan_updates_existing_subscription(self):
        """POST /api/v1/tenants/{id}/subscription/ updates an existing subscription → 200."""
        tenant = _make_tenant("sub_tenant_b")
        old_plan = _make_plan(name="Starter")
        new_plan = _make_plan(name="Pro", price_etb="1000.00")
        _make_subscription(tenant, old_plan)
        client, _ = _super_admin_client()

        payload = {
            "plan_id": new_plan.pk,
            "status": "active",
            "current_period_start": "2025-06-01",
            "current_period_end": "2026-06-01",
        }

        response = client.post(
            f"/api/v1/tenants/{tenant.pk}/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["plan"]["name"] == "Pro"
        # Verify only one subscription exists (update, not duplicate create)
        assert TenantSubscription.objects.filter(tenant=tenant).count() == 1

    def test_new_plan_limits_apply_immediately(self):
        """
        After reassigning to a new plan, the TenantSubscription immediately
        reflects the new plan's limits (Requirement 2.7).
        """
        tenant = _make_tenant("sub_tenant_c")
        old_plan = _make_plan(name="StarterC", max_branches=3)
        new_plan = _make_plan(name="ProC", max_branches=20, price_etb="1000.00")
        _make_subscription(tenant, old_plan)
        client, _ = _super_admin_client()

        payload = {
            "plan_id": new_plan.pk,
            "status": "active",
            "current_period_start": "2025-01-01",
            "current_period_end": "2026-01-01",
        }

        response = client.post(
            f"/api/v1/tenants/{tenant.pk}/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 200
        sub = TenantSubscription.objects.get(tenant=tenant)
        assert sub.plan.max_branches == 20  # new limit

    def test_assign_subscription_nonexistent_tenant_returns_404(self):
        """POST /api/v1/tenants/9999/subscription/ returns 404."""
        plan = _make_plan()
        client, _ = _super_admin_client()

        payload = {
            "plan_id": plan.pk,
            "current_period_end": "2026-01-01",
        }

        response = client.post(
            "/api/v1/tenants/9999/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 404

    def test_assign_subscription_invalid_plan_returns_400(self):
        """POST /api/v1/tenants/{id}/subscription/ with unknown plan_id returns 400."""
        tenant = _make_tenant("sub_tenant_d")
        client, _ = _super_admin_client()

        payload = {
            "plan_id": 99999,
            "current_period_end": "2026-01-01",
        }

        response = client.post(
            f"/api/v1/tenants/{tenant.pk}/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 400

    def test_non_admin_cannot_assign_subscription(self):
        """POST /api/v1/tenants/{id}/subscription/ as non-Super_Admin returns 403."""
        tenant = _make_tenant("sub_tenant_e")
        plan = _make_plan(name="StarterE")
        client, _ = _non_admin_client()

        payload = {
            "plan_id": plan.pk,
            "current_period_end": "2026-01-01",
        }

        response = client.post(
            f"/api/v1/tenants/{tenant.pk}/subscription/",
            data=payload,
            format="json",
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests — GET /api/v1/tenants/{id}/usage/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantUsage:
    def test_super_admin_gets_usage_data(self):
        """GET /api/v1/tenants/{id}/usage/ as Super_Admin returns 200 with usage."""
        tenant = _make_tenant("usage_tenant_a")
        plan = _make_plan(
            name="StarterU",
            max_branches=5,
            max_menu_items=50,
            max_staff_accounts=10,
        )
        _make_subscription(tenant, plan)
        client, _ = _super_admin_client()

        response = client.get(f"/api/v1/tenants/{tenant.pk}/usage/")

        assert response.status_code == 200
        data = response.json()

        # Top-level fields
        assert str(data["tenant_id"]) == str(tenant.pk)
        assert data["plan"] == "StarterU"
        assert data["subscription_status"] == "active"

        # Resource usage structure
        assert "branches" in data
        assert "used" in data["branches"]
        assert "limit" in data["branches"]
        assert data["branches"]["limit"] == 5

        assert "menu_items" in data
        assert data["menu_items"]["limit"] == 50

        assert "staff_accounts" in data
        assert data["staff_accounts"]["limit"] == 10

    def test_usage_counts_are_integers(self):
        """Usage 'used' and 'limit' values are integers."""
        tenant = _make_tenant("usage_tenant_b")
        plan = _make_plan(name="StarterUB")
        _make_subscription(tenant, plan)
        client, _ = _super_admin_client()

        response = client.get(f"/api/v1/tenants/{tenant.pk}/usage/")

        assert response.status_code == 200
        data = response.json()
        for resource in ["branches", "menu_items", "staff_accounts"]:
            assert isinstance(data[resource]["used"], int), f"{resource}.used should be int"
            assert isinstance(data[resource]["limit"], int), f"{resource}.limit should be int"

    def test_usage_nonexistent_tenant_returns_404(self):
        """GET /api/v1/tenants/9999/usage/ returns 404."""
        client, _ = _super_admin_client()

        response = client.get("/api/v1/tenants/9999/usage/")

        assert response.status_code == 404

    def test_usage_tenant_no_subscription_returns_404(self):
        """GET /api/v1/tenants/{id}/usage/ for a tenant without a subscription returns 404."""
        tenant = _make_tenant("usage_tenant_nosub")
        client, _ = _super_admin_client()

        response = client.get(f"/api/v1/tenants/{tenant.pk}/usage/")

        assert response.status_code == 404

    def test_non_admin_cannot_get_usage(self):
        """GET /api/v1/tenants/{id}/usage/ as non-Super_Admin returns 403."""
        tenant = _make_tenant("usage_tenant_c")
        plan = _make_plan(name="StarterUC")
        _make_subscription(tenant, plan)
        client, _ = _non_admin_client()

        response = client.get(f"/api/v1/tenants/{tenant.pk}/usage/")

        assert response.status_code == 403
