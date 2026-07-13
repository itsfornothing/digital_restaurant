"""
tests/e2e/test_tenant_isolation_e2e.py

E2E-03: Tenant isolation verification
Validates: Requirements 1.1, 1.3, 4.2, 4.3

Verifies that one tenant's data is structurally inaccessible to another
tenant's authenticated users.

Scenario overview:
  - Tenant A has a Branch, a Branch Manager, one Order, one InventoryItem,
    and one Expense.
  - Tenant B has a Branch and a Branch Manager.
  - Tenant B's Branch Manager attempts to read/delete Tenant A's resources
    by UUID — every attempt must return 403 or 404.
  - After the cross-tenant attempts, Tenant A's data must still exist
    (no inadvertent destruction).

Note on test-environment behaviour
-----------------------------------
The testing profile (config/settings/testing.py) uses SQLite in-memory
without django-tenants PostgreSQL schema routing.  In this environment
tenant isolation is enforced by ORM queryset filtering (branch_id / user
role scope checks in the ViewSets), NOT by separate database schemas.

Both enforcement mechanisms (ORM scoping and schema routing) should block
cross-tenant access; these tests verify that the ORM-layer enforcement is in
place.  The HTTP_HOST-based TenantMiddleware is NOT active in the test
settings (it is excluded from MIDDLEWARE), so we rely on the ORM-level
permission checks to confirm isolation.

Markers
-------
  @pytest.mark.django_db(transaction=True)
  @pytest.mark.e2e
  @pytest.mark.tenant_isolation  (class-level)
"""

from datetime import date
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.authentication.models import User, UserRole
from apps.branches.models import Branch, Table
from apps.expenses.models import Expense
from apps.inventory.models import InventoryItem
from apps.menus.models import MenuItem
from apps.orders.models import Order
from apps.qr.models import QRCode


# ---------------------------------------------------------------------------
# Local fixtures — two independent tenants with their own data
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_a_setup(db):
    """
    Create all Tenant A data in a single fixture:
      - Branch A
      - Table for Branch A (required to create an Order)
      - QRCode for that table (required for customer session)
      - Branch Manager A (linked to Branch A)
      - One MenuItem (required to build an Order with items)
      - One Order (status='confirmed', linked to Branch A)
      - One InventoryItem (linked to Branch A)
      - One Expense (linked to Branch A)

    Returns:
        dict with keys: branch, table, qr_code, manager, menu_item,
                        order, inventory_item, expense
    """
    branch = Branch.objects.create(
        name="Tenant A Branch",
        address="1 Tenant A Street, Addis Ababa",
        phone="0911000001",
        email="branch-a@tenant-a.et",
    )
    table = Table.objects.create(
        branch=branch,
        number="1",
        seat_count=2,
    )
    qr_code = QRCode.objects.create(
        table=table,
        is_active=True,
        image_url="",
    )
    manager = User.objects.create_user(
        email="manager-a@tenant-a.et",
        password="SecurePassA!2024",
        role=UserRole.BRANCH_MANAGER,
        branch=branch,
    )
    menu_item = MenuItem.objects.create(
        branch=branch,
        name="Tenant A Special",
        price=Decimal("120.00"),
        prep_time_minutes=10,
        status="available",
        is_archived=False,
        dietary_tags=[],
    )
    order = Order.objects.create(
        branch=branch,
        table=table,
        status="confirmed",
        total_amount=Decimal("120.00"),
    )
    inventory_item = InventoryItem.objects.create(
        branch=branch,
        name="Tenant A Ingredient",
        category="Protein",
        quantity=Decimal("50.0000"),
        unit="kg",
        purchase_price=Decimal("80.00"),
        reorder_threshold=Decimal("10.0000"),
    )
    expense = Expense.objects.create(
        branch=branch,
        description="Tenant A Rent",
        category="rent",
        amount=Decimal("5000.00"),
        date_incurred=date.today(),
    )
    return {
        "branch": branch,
        "table": table,
        "qr_code": qr_code,
        "manager": manager,
        "menu_item": menu_item,
        "order": order,
        "inventory_item": inventory_item,
        "expense": expense,
    }


@pytest.fixture
def tenant_b_setup(db):
    """
    Create Tenant B data:
      - Branch B
      - Branch Manager B (linked to Branch B)

    Returns:
        dict with keys: branch, manager
    """
    branch = Branch.objects.create(
        name="Tenant B Branch",
        address="2 Tenant B Street, Addis Ababa",
        phone="0922000002",
        email="branch-b@tenant-b.et",
    )
    manager = User.objects.create_user(
        email="manager-b@tenant-b.et",
        password="SecurePassB!2024",
        role=UserRole.BRANCH_MANAGER,
        branch=branch,
    )
    return {
        "branch": branch,
        "manager": manager,
    }


# ---------------------------------------------------------------------------
# E2E Test Class
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.e2e
@pytest.mark.tenant_isolation
class TestTenantIsolationE2E03:
    """
    E2E-03: Tenant isolation verification

    Verifies that Tenant B's Branch Manager cannot access or destroy
    data belonging to Tenant A — even when the exact UUIDs are known.

    Both 403 and 404 responses are acceptable outcomes:
      - 403 means the permission layer explicitly denied access.
      - 404 means the platform correctly hides the existence of the
        resource from the requesting tenant (resource not found in the
        ORM queryset scoped to Tenant B's context).

    After the cross-tenant access attempts, all of Tenant A's data must
    remain intact (re-queried in Tenant A's own context).

    Validates: Requirements 1.1, 1.3, 4.2, 4.3
    """

    # ------------------------------------------------------------------
    # Step 1 — Authenticate as Tenant B Branch Manager
    # ------------------------------------------------------------------

    def test_step1_tenant_b_manager_can_authenticate(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Step 1: Tenant B Branch Manager authenticates successfully.

        Validates the fixture is correct and that Tenant B's user exists.
        """
        manager_b = tenant_b_setup["manager"]
        client = APIClient()
        # force_authenticate simulates a logged-in Tenant B session
        client.force_authenticate(user=manager_b)

        # Access Tenant B's own branch — must succeed
        response = client.get(
            f"/api/v1/branches/{tenant_b_setup['branch'].id}/",
        )
        # 200 = own resource accessible; could also be 200 depending on viewset
        # We just verify the client is authenticated (not 401/403 for own resource)
        assert response.status_code != 401, (
            "Tenant B's manager must be authenticated (not 401)"
        )

    # ------------------------------------------------------------------
    # Step 2 — Cross-tenant Order access: GET /api/v1/orders/{id}/
    # ------------------------------------------------------------------

    def test_step2_tenant_b_cannot_read_tenant_a_order(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Step 2: Tenant B's Branch Manager attempts GET on Tenant A's Order UUID.

        Both 403 and 404 are acceptable:
          - 403 means access denied by the RBAC/permission layer.
          - 404 means the platform correctly hides the existence of
            Tenant A's resource from Tenant B (order not in queryset
            scoped to Tenant B's branch).

        Validates: Requirements 1.1, 1.3, 4.2, 4.3
        """
        manager_b = tenant_b_setup["manager"]
        tenant_a_order_id = tenant_a_setup["order"].id

        client = APIClient()
        client.force_authenticate(user=manager_b)

        response = client.get(f"/api/v1/orders/{tenant_a_order_id}/")

        assert response.status_code in (403, 404), (
            f"Tenant B must NOT be able to read Tenant A's Order. "
            f"Expected 403 or 404, got {response.status_code}. "
            f"Response body: {getattr(response, 'data', response.content)}"
        )

    # ------------------------------------------------------------------
    # Step 3 — Cross-tenant Inventory access: GET /api/v1/inventory/{id}/
    # ------------------------------------------------------------------

    def test_step3_tenant_b_cannot_read_tenant_a_inventory_item(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Step 3: Tenant B's Branch Manager attempts GET on Tenant A's InventoryItem UUID.

        Both 403 and 404 are acceptable — 403 means access denied,
        404 means the platform correctly hides the existence of Tenant A's
        resource from Tenant B.

        Validates: Requirements 1.1, 1.3, 4.2, 4.3
        """
        manager_b = tenant_b_setup["manager"]
        tenant_a_inventory_id = tenant_a_setup["inventory_item"].id

        client = APIClient()
        client.force_authenticate(user=manager_b)

        response = client.get(f"/api/v1/inventory/{tenant_a_inventory_id}/")

        assert response.status_code in (403, 404), (
            f"Tenant B must NOT be able to read Tenant A's InventoryItem. "
            f"Expected 403 or 404, got {response.status_code}. "
            f"Response body: {getattr(response, 'data', response.content)}"
        )

    # ------------------------------------------------------------------
    # Step 4 — Cross-tenant Expense deletion: DELETE /api/v1/expenses/{id}/
    # ------------------------------------------------------------------

    def test_step4_tenant_b_cannot_delete_tenant_a_expense(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Step 4: Tenant B's Branch Manager attempts DELETE on Tenant A's Expense UUID.

        Both 403 and 404 are acceptable — 403 means access denied,
        404 means the platform correctly hides the existence of Tenant A's
        resource from Tenant B.

        Validates: Requirements 1.1, 1.3, 4.2, 4.3
        """
        manager_b = tenant_b_setup["manager"]
        tenant_a_expense_id = tenant_a_setup["expense"].id

        client = APIClient()
        client.force_authenticate(user=manager_b)

        response = client.delete(f"/api/v1/expenses/{tenant_a_expense_id}/")

        assert response.status_code in (403, 404), (
            f"Tenant B must NOT be able to delete Tenant A's Expense. "
            f"Expected 403 or 404, got {response.status_code}. "
            f"Response body: {getattr(response, 'data', response.content)}"
        )

    # ------------------------------------------------------------------
    # Step 5 — Verify Tenant A's data is still intact
    # ------------------------------------------------------------------

    def test_step5_tenant_a_data_untouched_after_cross_tenant_attempts(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Step 5: After all cross-tenant access attempts by Tenant B, verify
        that Tenant A's Order, InventoryItem, and Expense still exist in
        Tenant A's own context.

        This confirms the cross-tenant attempts caused no inadvertent
        data destruction.

        Validates: Requirements 1.1, 1.3
        """
        # Perform the three cross-tenant attempts first (mirroring steps 2–4)
        manager_b = tenant_b_setup["manager"]
        client_b = APIClient()
        client_b.force_authenticate(user=manager_b)

        client_b.get(f"/api/v1/orders/{tenant_a_setup['order'].id}/")
        client_b.get(f"/api/v1/inventory/{tenant_a_setup['inventory_item'].id}/")
        client_b.delete(f"/api/v1/expenses/{tenant_a_setup['expense'].id}/")

        # Now re-query Tenant A's data from the database directly
        # (simulates re-querying in Tenant A's context after the cross-tenant attempts)
        order_still_exists = Order.objects.filter(
            id=tenant_a_setup["order"].id
        ).exists()
        assert order_still_exists, (
            "Tenant A's Order must still exist after Tenant B's failed read attempt. "
            "The cross-tenant GET must not have deleted or modified the resource."
        )

        inventory_still_exists = InventoryItem.objects.filter(
            id=tenant_a_setup["inventory_item"].id
        ).exists()
        assert inventory_still_exists, (
            "Tenant A's InventoryItem must still exist after Tenant B's failed read attempt."
        )

        expense_still_exists = Expense.objects.filter(
            id=tenant_a_setup["expense"].id
        ).exists()
        assert expense_still_exists, (
            "Tenant A's Expense must still exist after Tenant B's failed DELETE attempt. "
            "The cross-tenant DELETE must have been blocked (403/404), not executed."
        )

    # ------------------------------------------------------------------
    # Integrated E2E test (all steps in one test)
    # ------------------------------------------------------------------

    def test_complete_tenant_isolation_flow_e2e(
        self, tenant_a_setup, tenant_b_setup
    ):
        """
        Complete E2E-03: all 5 steps in sequence.

        1. Authenticate as Tenant B's Branch Manager
        2. Attempt GET /api/v1/orders/{tenant_a_order_id}/ → 403 or 404
        3. Attempt GET /api/v1/inventory/{tenant_a_inventory_id}/ → 403 or 404
        4. Attempt DELETE /api/v1/expenses/{tenant_a_expense_id}/ → 403 or 404
        5. Verify Tenant A's Order, InventoryItem, and Expense still exist

        Both 403 and 404 are acceptable — 403 means access denied,
        404 means the platform correctly hides the existence of Tenant A's
        resource from Tenant B.

        Validates: Requirements 1.1, 1.3, 4.2, 4.3 (E2E-03)
        """
        manager_b = tenant_b_setup["manager"]
        tenant_a_order_id = tenant_a_setup["order"].id
        tenant_a_inventory_id = tenant_a_setup["inventory_item"].id
        tenant_a_expense_id = tenant_a_setup["expense"].id

        # ------------------------------------------------------------------
        # Step 1: Authenticate as Tenant B's Branch Manager
        # ------------------------------------------------------------------
        client_b = APIClient()
        client_b.force_authenticate(user=manager_b)

        # Verify authentication by accessing Tenant B's own branch
        own_branch_resp = client_b.get(
            f"/api/v1/branches/{tenant_b_setup['branch'].id}/"
        )
        assert own_branch_resp.status_code != 401, (
            f"Step 1 failed — Tenant B manager must be authenticated. "
            f"Got {own_branch_resp.status_code}"
        )

        # ------------------------------------------------------------------
        # Step 2: Attempt GET on Tenant A's Order → 403 or 404
        # ------------------------------------------------------------------
        order_resp = client_b.get(f"/api/v1/orders/{tenant_a_order_id}/")

        assert order_resp.status_code in (403, 404), (
            f"Step 2 failed — Tenant B must not read Tenant A's Order. "
            f"Expected 403 or 404, got {order_resp.status_code}: "
            f"{getattr(order_resp, 'data', order_resp.content)}"
        )

        # ------------------------------------------------------------------
        # Step 3: Attempt GET on Tenant A's InventoryItem → 403 or 404
        # ------------------------------------------------------------------
        inventory_resp = client_b.get(f"/api/v1/inventory/{tenant_a_inventory_id}/")

        assert inventory_resp.status_code in (403, 404), (
            f"Step 3 failed — Tenant B must not read Tenant A's InventoryItem. "
            f"Expected 403 or 404, got {inventory_resp.status_code}: "
            f"{getattr(inventory_resp, 'data', inventory_resp.content)}"
        )

        # ------------------------------------------------------------------
        # Step 4: Attempt DELETE on Tenant A's Expense → 403 or 404
        # ------------------------------------------------------------------
        expense_resp = client_b.delete(f"/api/v1/expenses/{tenant_a_expense_id}/")

        assert expense_resp.status_code in (403, 404), (
            f"Step 4 failed — Tenant B must not delete Tenant A's Expense. "
            f"Expected 403 or 404, got {expense_resp.status_code}: "
            f"{getattr(expense_resp, 'data', expense_resp.content)}"
        )

        # ------------------------------------------------------------------
        # Step 5: Verify Tenant A's data is intact
        # ------------------------------------------------------------------
        assert Order.objects.filter(id=tenant_a_order_id).exists(), (
            "Step 5 failed — Tenant A's Order must still exist after "
            "Tenant B's blocked read attempt."
        )

        assert InventoryItem.objects.filter(id=tenant_a_inventory_id).exists(), (
            "Step 5 failed — Tenant A's InventoryItem must still exist after "
            "Tenant B's blocked read attempt."
        )

        assert Expense.objects.filter(id=tenant_a_expense_id).exists(), (
            "Step 5 failed — Tenant A's Expense must still exist after "
            "Tenant B's blocked DELETE attempt. The DELETE must have been "
            "rejected (403/404), not executed."
        )
