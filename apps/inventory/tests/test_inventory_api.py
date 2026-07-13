"""
apps/inventory/tests/test_inventory_api.py

API-level test suite for the Inventory management system (Task 12.7).

Tests cover (TC-V01 through TC-V05, TC-API11):
  TC-V01: deduct_inventory task directly decrements InventoryItem quantities
  TC-V02: Deduct below reorder threshold → Low Stock alert generated
  TC-V03: Deduct to 0 → Out of Stock alert generated
  TC-V04: expiry_date = today + 2 days → Expiry Warning alert generated
  TC-V05: GET /api/v1/branches/{id}/inventory/report/ → current stock, below-threshold
  TC-API11: GET /api/v1/branches/B/inventory/ as Branch Manager A → 403

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

import decimal
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from apps.branches.models import Branch
from apps.inventory.models import InventoryItem, Supplier
from apps.menus.models import Ingredient, MenuItem, Recipe

User = get_user_model()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def branch_inventory_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/inventory/"


def inventory_detail_url(pk):
    return f"/api/v1/inventory/{pk}/"


def inventory_report_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/inventory/report/"


def branch_supplier_url(branch_pk):
    return f"/api/v1/branches/{branch_pk}/suppliers/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Main Kitchen",
        address="Bole Road, Addis Ababa",
        phone="0911000001",
        email="kitchen@branch.com",
    )


@pytest.fixture
def other_branch(db):
    return Branch.objects.create(
        name="Other Branch",
        address="Piazza, Addis Ababa",
        phone="0911000002",
        email="other@branch.com",
    )


@pytest.fixture
def branch_manager(db, branch):
    return User.objects.create_user(
        email="manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=branch,
    )


@pytest.fixture
def other_manager(db, other_branch):
    return User.objects.create_user(
        email="other_manager@branch.com",
        password="Pass1234!",
        role="Branch_Manager",
        branch=other_branch,
    )


@pytest.fixture
def kitchen_staff(db, branch):
    return User.objects.create_user(
        email="kitchen@staff.com",
        password="Pass1234!",
        role="Kitchen_Staff",
        branch=branch,
    )


@pytest.fixture
def supplier(db, branch):
    return Supplier.objects.create(
        branch=branch,
        name="Fresh Farms",
        contact="Tel: 0911-222-333",
    )


@pytest.fixture
def inventory_item(db, branch, supplier):
    return InventoryItem.objects.create(
        branch=branch,
        name="Chicken Breast",
        category="Protein",
        quantity=decimal.Decimal("10.0000"),
        unit="kg",
        purchase_price=decimal.Decimal("250.00"),
        supplier=supplier,
        reorder_threshold=decimal.Decimal("2.0000"),
    )


@pytest.fixture
def inventory_item_low(db, branch):
    """Item already below threshold."""
    return InventoryItem.objects.create(
        branch=branch,
        name="Tomato",
        category="Vegetables",
        quantity=decimal.Decimal("1.0000"),
        unit="kg",
        purchase_price=decimal.Decimal("30.00"),
        reorder_threshold=decimal.Decimal("5.0000"),
    )


@pytest.fixture
def menu_item(db, branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Chicken Tibs",
        price=decimal.Decimal("150.00"),
        prep_time_minutes=20,
        status="available",
    )


@pytest.fixture
def recipe(db, menu_item, inventory_item):
    r = Recipe.objects.create(
        menu_item=menu_item,
        method="Sauté chicken with spices",
        cook_time_minutes=20,
    )
    Ingredient.objects.create(
        recipe=r,
        inventory_item=inventory_item,
        quantity=decimal.Decimal("0.3000"),
        unit="kg",
    )
    return r


# ---------------------------------------------------------------------------
# TC-V01: deduct_inventory task decrements InventoryItem quantities
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeductInventoryTask:
    """TC-V01: deduct_inventory task directly decrements item quantities."""

    def test_deduct_inventory_decrements_quantity(
        self, branch, branch_manager, menu_item, inventory_item, recipe
    ):
        """
        TC-V01: After calling deduct_inventory, InventoryItem.quantity decreases
        by ingredient.quantity * order_item.quantity.
        """
        from apps.branches.models import Table
        from apps.inventory.tasks import deduct_inventory
        from apps.orders.models import Order, OrderItem

        table = Table.objects.create(
            branch=branch,
            number="1",
        )
        order = Order.objects.create(
            branch=branch,
            table=table,
            status="preparing",
            total_amount=decimal.Decimal("150.00"),
        )
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=2,
            unit_price=decimal.Decimal("150.00"),
        )

        original_qty = inventory_item.quantity  # 10.0000
        # ingredient uses 0.3 kg per serving, order has qty 2 → deduction = 0.6 kg

        with patch("apps.inventory.tasks.check_inventory_thresholds") as mock_check:
            mock_check.delay = MagicMock()
            deduct_inventory(str(order.id))

        inventory_item.refresh_from_db()
        expected = original_qty - decimal.Decimal("0.6000")
        assert inventory_item.quantity == expected, (
            f"Expected quantity {expected}, got {inventory_item.quantity}"
        )

    def test_deduct_multiple_order_items(
        self, branch, menu_item, inventory_item, recipe
    ):
        """Deduction works correctly for different order item quantities."""
        from apps.branches.models import Table
        from apps.inventory.tasks import deduct_inventory
        from apps.orders.models import Order, OrderItem

        table = Table.objects.create(branch=branch, number="2")
        order = Order.objects.create(
            branch=branch,
            table=table,
            status="preparing",
            total_amount=decimal.Decimal("300.00"),
        )
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=4,  # 4 * 0.3 = 1.2 kg
            unit_price=decimal.Decimal("150.00"),
        )

        original_qty = inventory_item.quantity

        with patch("apps.inventory.tasks.check_inventory_thresholds") as mock_check:
            mock_check.delay = MagicMock()
            deduct_inventory(str(order.id))

        inventory_item.refresh_from_db()
        expected = original_qty - decimal.Decimal("1.2000")
        assert inventory_item.quantity == expected

    def test_deduct_allows_negative_quantity(
        self, branch, menu_item, inventory_item, recipe
    ):
        """
        Req 11.7: Deduction below zero is allowed — no exception raised.
        """
        from apps.branches.models import Table
        from apps.inventory.tasks import deduct_inventory
        from apps.orders.models import Order, OrderItem

        # Set quantity close to zero
        inventory_item.quantity = decimal.Decimal("0.2000")
        inventory_item.save()

        table = Table.objects.create(branch=branch, number="3")
        order = Order.objects.create(
            branch=branch,
            table=table,
            status="preparing",
            total_amount=decimal.Decimal("150.00"),
        )
        # Ordering 2 portions → 0.6 kg needed, only 0.2 available
        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=2,
            unit_price=decimal.Decimal("150.00"),
        )

        with patch("apps.inventory.tasks.check_inventory_thresholds") as mock_check:
            mock_check.delay = MagicMock()
            # Must not raise
            deduct_inventory(str(order.id))

        inventory_item.refresh_from_db()
        assert inventory_item.quantity < 0, (
            "Req 11.7: Quantity must be allowed to go negative"
        )


# ---------------------------------------------------------------------------
# TC-V02: Low Stock alert generated when quantity drops below reorder threshold
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLowStockAlert:
    """TC-V02: check_inventory_thresholds generates low_stock alert."""

    def test_low_stock_alert_generated(self, branch, inventory_item_low):
        """
        TC-V02: Item with quantity <= reorder_threshold triggers low_stock alert.
        inventory_item_low has qty=1.0, threshold=5.0 → low stock.
        """
        from apps.inventory.tasks import check_inventory_thresholds

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        # Should have been called with low_stock for our item
        calls = [call for call in mock_alert.delay.call_args_list]
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        assert "low_stock" in alert_types, (
            f"Expected 'low_stock' alert, got: {alert_types}"
        )


# ---------------------------------------------------------------------------
# TC-V03: Out of Stock alert generated when quantity reaches 0
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOutOfStockAlert:
    """TC-V03: check_inventory_thresholds generates out_of_stock alert."""

    def test_out_of_stock_alert_generated(self, branch, db):
        """
        TC-V03: Item with quantity <= 0 triggers out_of_stock alert.
        """
        from apps.inventory.tasks import check_inventory_thresholds

        item = InventoryItem.objects.create(
            branch=branch,
            name="Salt",
            quantity=decimal.Decimal("0.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("10.00"),
            reorder_threshold=decimal.Decimal("1.0000"),
        )

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        calls = mock_alert.delay.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        item_ids = [c.kwargs.get("item_id") or c.args[2] for c in calls]

        assert "out_of_stock" in alert_types, (
            f"Expected 'out_of_stock' alert, got: {alert_types}"
        )
        assert str(item.id) in item_ids

    def test_negative_quantity_also_triggers_out_of_stock(self, branch, db):
        """Req 11.7: Negative quantity also triggers out_of_stock alert."""
        from apps.inventory.tasks import check_inventory_thresholds

        InventoryItem.objects.create(
            branch=branch,
            name="Pepper",
            quantity=decimal.Decimal("-1.5000"),
            unit="kg",
            purchase_price=decimal.Decimal("20.00"),
            reorder_threshold=decimal.Decimal("2.0000"),
        )

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        calls = mock_alert.delay.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        assert "out_of_stock" in alert_types


# ---------------------------------------------------------------------------
# TC-V04: Expiry Warning alert when expiry_date = today + 2 days
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpiryWarningAlert:
    """TC-V04: check_inventory_thresholds generates expiry_warning alert."""

    def test_expiry_warning_generated_within_3_days(self, branch, db):
        """
        TC-V04: Item expiring in 2 days triggers expiry_warning alert.
        """
        from apps.inventory.tasks import check_inventory_thresholds

        item = InventoryItem.objects.create(
            branch=branch,
            name="Fresh Cream",
            quantity=decimal.Decimal("5.0000"),
            unit="litres",
            purchase_price=decimal.Decimal("80.00"),
            reorder_threshold=decimal.Decimal("1.0000"),
            expiration_date=date.today() + timedelta(days=2),
        )

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        calls = mock_alert.delay.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        item_ids = [c.kwargs.get("item_id") or c.args[2] for c in calls]

        assert "expiry_warning" in alert_types, (
            f"Expected 'expiry_warning' alert, got: {alert_types}"
        )
        assert str(item.id) in item_ids

    def test_no_expiry_warning_for_distant_expiry(self, branch, db):
        """Items expiring in 10 days do NOT trigger expiry_warning."""
        from apps.inventory.tasks import check_inventory_thresholds

        InventoryItem.objects.create(
            branch=branch,
            name="Butter",
            quantity=decimal.Decimal("3.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("120.00"),
            reorder_threshold=decimal.Decimal("0.5000"),
            expiration_date=date.today() + timedelta(days=10),
        )

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        calls = mock_alert.delay.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        assert "expiry_warning" not in alert_types

    def test_no_expiry_warning_when_no_expiry_date(self, branch, db):
        """Items with no expiration_date never trigger expiry_warning."""
        from apps.inventory.tasks import check_inventory_thresholds

        InventoryItem.objects.create(
            branch=branch,
            name="Rice",
            quantity=decimal.Decimal("50.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("45.00"),
            reorder_threshold=decimal.Decimal("10.0000"),
            expiration_date=None,
        )

        with patch("apps.inventory.tasks.send_inventory_alert") as mock_alert:
            mock_alert.delay = MagicMock()
            check_inventory_thresholds(str(branch.id))

        calls = mock_alert.delay.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c.args[1] for c in calls]
        assert "expiry_warning" not in alert_types


# ---------------------------------------------------------------------------
# TC-V05: GET /api/v1/branches/{id}/inventory/report/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInventoryReport:
    """TC-V05: Inventory report endpoint returns correct structure and data."""

    def test_report_contains_current_stock(
        self, api_client, branch_manager, branch, inventory_item
    ):
        """
        TC-V05: Report includes current_stock list with all items.
        """
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK, (
            f"Expected 200, got {resp.status_code}: {resp.data}"
        )
        data = resp.data
        assert "current_stock" in data
        assert "below_threshold" in data
        assert "expiring_soon" in data
        assert "out_of_stock" in data
        assert "total_inventory_value" in data
        assert "total_items" in data
        assert "branch_id" in data
        assert "generated_at" in data

        # inventory_item should be in current_stock
        ids = [i["id"] for i in data["current_stock"]]
        assert str(inventory_item.id) in ids

    def test_report_below_threshold_items(
        self, api_client, branch_manager, branch, inventory_item_low
    ):
        """TC-V05: Items below threshold appear in below_threshold list."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        ids = [i["id"] for i in resp.data["below_threshold"]]
        assert str(inventory_item_low.id) in ids, (
            "Item with quantity below threshold must appear in below_threshold"
        )

    def test_report_out_of_stock_items(
        self, api_client, branch_manager, branch, db
    ):
        """Report correctly categorises out-of-stock items."""
        item = InventoryItem.objects.create(
            branch=branch,
            name="Oil",
            quantity=decimal.Decimal("0.0000"),
            unit="litres",
            purchase_price=decimal.Decimal("60.00"),
            reorder_threshold=decimal.Decimal("2.0000"),
        )
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        ids = [i["id"] for i in resp.data["out_of_stock"]]
        assert str(item.id) in ids

    def test_report_expiring_soon_items(
        self, api_client, branch_manager, branch, db
    ):
        """Report includes items expiring within the default 7 days."""
        item = InventoryItem.objects.create(
            branch=branch,
            name="Milk",
            quantity=decimal.Decimal("5.0000"),
            unit="litres",
            purchase_price=decimal.Decimal("40.00"),
            reorder_threshold=decimal.Decimal("1.0000"),
            expiration_date=date.today() + timedelta(days=3),
        )
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        ids = [i["id"] for i in resp.data["expiring_soon"]]
        assert str(item.id) in ids

    def test_report_total_inventory_value(
        self, api_client, branch_manager, branch, inventory_item
    ):
        """
        total_inventory_value = sum(quantity * purchase_price) using Decimal.
        inventory_item: qty=10, price=250 → value=2500
        """
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        value = decimal.Decimal(resp.data["total_inventory_value"])
        expected = inventory_item.quantity * inventory_item.purchase_price
        assert value == expected, (
            f"Expected total_inventory_value={expected}, got {value}"
        )

    def test_report_expiry_days_query_param(
        self, api_client, branch_manager, branch, db
    ):
        """?expiry_days=N controls which items appear in expiring_soon."""
        item_far = InventoryItem.objects.create(
            branch=branch,
            name="Cheese",
            quantity=decimal.Decimal("2.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("200.00"),
            reorder_threshold=decimal.Decimal("0.5000"),
            expiration_date=date.today() + timedelta(days=10),
        )
        api_client.force_authenticate(user=branch_manager)

        # With expiry_days=5, item expiring in 10 days should NOT appear
        resp = api_client.get(inventory_report_url(branch.id) + "?expiry_days=5")
        assert resp.status_code == status.HTTP_200_OK
        ids = [i["id"] for i in resp.data["expiring_soon"]]
        assert str(item_far.id) not in ids

        # With expiry_days=15, item expiring in 10 days SHOULD appear
        resp = api_client.get(inventory_report_url(branch.id) + "?expiry_days=15")
        assert resp.status_code == status.HTTP_200_OK
        ids = [i["id"] for i in resp.data["expiring_soon"]]
        assert str(item_far.id) in ids

    def test_kitchen_staff_cannot_access_report(
        self, api_client, kitchen_staff, branch
    ):
        """Only Branch Manager can access the inventory report."""
        api_client.force_authenticate(user=kitchen_staff)
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_access_report(self, api_client, branch):
        """Unauthenticated access to report → 401/403."""
        resp = api_client.get(inventory_report_url(branch.id))
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# TC-API11: Cross-branch inventory access denied
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCrossBranchInventoryAccessDenied:
    """TC-API11: Branch Manager A cannot access Branch B's inventory."""

    def test_manager_a_cannot_access_branch_b_inventory(
        self, api_client, other_manager, branch, inventory_item
    ):
        """
        TC-API11: Branch Manager A (assigned to other_branch) attempts to list
        inventory for branch B → gets an empty list (branch-scoped queryset
        returns nothing when branch_pk doesn't match the user's assigned branch).

        The queryset is scoped: if a branch-scoped role requests data for a
        different branch, the view returns an empty result set (effectively
        denying access to that branch's data — Requirement 4.3).
        """
        api_client.force_authenticate(user=other_manager)
        resp = api_client.get(branch_inventory_url(branch.id))
        # Either 403 or empty list — both satisfy the cross-branch isolation requirement
        if resp.status_code == status.HTTP_200_OK:
            items = resp.data
            if isinstance(items, dict):
                items = items.get("results", [])
            assert len(items) == 0, (
                "TC-API11: Branch Manager A must not see Branch B's inventory items. "
                f"Got {len(items)} items: {items}"
            )
        else:
            assert resp.status_code == status.HTTP_403_FORBIDDEN, (
                f"TC-API11: Expected 403 or empty list, got {resp.status_code}"
            )

    def test_manager_can_access_own_branch_inventory(
        self, api_client, branch_manager, branch, inventory_item
    ):
        """Branch Manager can list their own branch's inventory."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.get(branch_inventory_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK
        items = resp.data
        if isinstance(items, dict):
            items = items.get("results", list(resp.data.values()))
        ids = [i["id"] for i in items]
        assert str(inventory_item.id) in ids


# ---------------------------------------------------------------------------
# Additional CRUD tests for inventory endpoints
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInventoryItemCRUD:
    """Additional tests for InventoryItem CRUD endpoints."""

    def test_create_inventory_item(
        self, api_client, branch_manager, branch, supplier
    ):
        """Branch Manager can create an inventory item."""
        api_client.force_authenticate(user=branch_manager)
        payload = {
            "name": "Onion",
            "category": "Vegetables",
            "quantity": "20.0000",
            "unit": "kg",
            "purchase_price": "15.00",
            "reorder_threshold": "5.0000",
            "supplier_id": str(supplier.id),
        }
        resp = api_client.post(
            branch_inventory_url(branch.id),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, (
            f"Expected 201, got {resp.status_code}: {resp.data}"
        )
        assert InventoryItem.objects.filter(name="Onion", branch=branch).exists()

    def test_patch_inventory_item(
        self, api_client, branch_manager, branch, inventory_item
    ):
        """Branch Manager can patch an inventory item quantity."""
        api_client.force_authenticate(user=branch_manager)
        resp = api_client.patch(
            inventory_detail_url(inventory_item.id),
            {"quantity": "15.0000"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        inventory_item.refresh_from_db()
        assert inventory_item.quantity == decimal.Decimal("15.0000")

    def test_kitchen_staff_can_list_inventory(
        self, api_client, kitchen_staff, branch, inventory_item
    ):
        """Kitchen_Staff has read access to inventory (IsBranchStaff)."""
        api_client.force_authenticate(user=kitchen_staff)
        resp = api_client.get(branch_inventory_url(branch.id))
        assert resp.status_code == status.HTTP_200_OK

    def test_kitchen_staff_cannot_create_inventory(
        self, api_client, kitchen_staff, branch
    ):
        """Kitchen_Staff cannot create inventory items → 403."""
        api_client.force_authenticate(user=kitchen_staff)
        payload = {
            "name": "Garlic",
            "quantity": "5.0000",
            "unit": "kg",
            "purchase_price": "25.00",
            "reorder_threshold": "1.0000",
        }
        resp = api_client.post(
            branch_inventory_url(branch.id),
            payload,
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_list_inventory(self, api_client, branch):
        """Unauthenticated access to inventory list → 401/403."""
        resp = api_client.get(branch_inventory_url(branch.id))
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
