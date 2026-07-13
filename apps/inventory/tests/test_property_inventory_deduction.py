"""
Property-Based Tests: Inventory Deduction Invariant (Property 22)

For any order transitioning to Preparing, the deduction formula holds:
    quantity_after = quantity_before - (ingredient.quantity * order_item.quantity)

for each Ingredient in the MenuItem's Recipe, for each OrderItem in the order.

Sub-properties tested:
  22a — Single OrderItem: deduction formula holds for each recipe ingredient
  22b — Multiple OrderItems in one order: all are deducted correctly
  22c — Deduction below zero is allowed (Requirement 11.7): formula still applies
        even when quantity_after is negative

Validates: Requirements 11.2
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.branches.models import Branch, Table
from apps.inventory.models import InventoryItem
from apps.inventory.tasks import deduct_inventory
from apps.menus.models import Ingredient, MenuItem, Recipe
from apps.orders.models import Order, OrderItem


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Positive decimal quantities with 4 decimal places — represent stock amounts
# and recipe quantities.  Range kept modest to stay well within DecimalField
# max_digits=12, decimal_places=4 constraints.
positive_qty_strategy = st.decimals(
    min_value="0.0001",
    max_value="999.9999",
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

# Small positive integer for order item quantities (PositiveSmallIntegerField)
order_item_qty_strategy = st.integers(min_value=1, max_value=20)

# Small positive integer for number of OrderItems in multi-item tests
num_items_strategy = st.integers(min_value=2, max_value=5)


# ---------------------------------------------------------------------------
# Helper: quantize a value to 4 decimal places as Decimal
# ---------------------------------------------------------------------------

def _q4(value) -> Decimal:
    """Return value as Decimal quantized to 4 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _q2(value) -> Decimal:
    """Return value as Decimal quantized to 2 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Property 22 Test Class
# ---------------------------------------------------------------------------


class TestPropertyInventoryDeductionInvariant(TestCase):
    """
    Property 22: Inventory Deduction Invariant

    For any order transitioning to Preparing, the deduction formula
    quantity_after = quantity_before - (ingredient.quantity * order_item.quantity)
    must hold for each ingredient in every OrderItem's recipe.

    Validates: Requirements 11.2
    """

    def setUp(self):
        """Create a shared Branch and Table reused across all Hypothesis iterations."""
        self.branch = Branch.objects.create(
            name=f"Deduction Test Branch {uuid.uuid4().hex[:6]}",
            address="123 Test Street",
            phone="0911000001",
            email="deduct@test.com",
        )
        self.table = Table.objects.create(
            branch=self.branch,
            number="1",
            seat_count=4,
        )

    # -----------------------------------------------------------------------
    # 22a — Single OrderItem: deduction formula holds per ingredient
    # -----------------------------------------------------------------------

    @given(
        initial_qty=positive_qty_strategy,
        ingredient_qty=positive_qty_strategy,
        order_item_qty=order_item_qty_strategy,
    )
    @settings(max_examples=500)
    def test_property_22a_single_order_item_deduction(
        self,
        initial_qty,
        ingredient_qty,
        order_item_qty,
    ):
        """
        **Validates: Requirements 11.2**

        Sub-property 22a: For a single OrderItem with one recipe ingredient,
        calling the deduction logic decrements the InventoryItem quantity by
        exactly ingredient.quantity * order_item.quantity.
        """
        initial_qty = _q4(initial_qty)
        ingredient_qty = _q4(ingredient_qty)

        # Build object graph: Branch → MenuItem → Recipe → Ingredient → InventoryItem
        inventory_item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"Item-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=initial_qty,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("0.0001"),
        )

        menu_item = MenuItem.objects.create(
            branch=self.branch,
            name=f"Dish-{uuid.uuid4().hex[:8]}",
            price=_q2("10.00"),
            prep_time_minutes=5,
            status="available",
        )

        recipe = Recipe.objects.create(
            menu_item=menu_item,
            method="Test method",
            cook_time_minutes=5,
        )

        Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=ingredient_qty,
            unit="kg",
        )

        # Build Branch → Table → Order → OrderItem
        order = Order.objects.create(
            branch=self.branch,
            table=self.table,
            status="confirmed",
            total_amount=_q2("10.00"),
        )

        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=order_item_qty,
            unit_price=_q2("10.00"),
        )

        # Record quantity_before
        quantity_before = initial_qty

        # Call the deduction task directly, suppressing Celery threshold task
        with patch(
            "apps.inventory.tasks.check_inventory_thresholds.delay"
        ):
            deduct_inventory.run(str(order.id))

        # Refresh and verify the formula
        inventory_item.refresh_from_db()
        expected = quantity_before - ingredient_qty * order_item_qty
        self.assertEqual(
            inventory_item.quantity,
            expected,
            msg=(
                f"Expected quantity {expected}, got {inventory_item.quantity}. "
                f"initial={quantity_before}, ingredient_qty={ingredient_qty}, "
                f"order_item_qty={order_item_qty}"
            ),
        )

    # -----------------------------------------------------------------------
    # 22b — Multiple OrderItems: all deductions applied correctly
    # -----------------------------------------------------------------------

    @given(
        num_items=num_items_strategy,
        initial_qtys=st.lists(
            positive_qty_strategy, min_size=5, max_size=5
        ),
        ingredient_qtys=st.lists(
            positive_qty_strategy, min_size=5, max_size=5
        ),
        order_item_qtys=st.lists(
            order_item_qty_strategy, min_size=5, max_size=5
        ),
    )
    @settings(max_examples=500)
    def test_property_22b_multiple_order_items_deduction(
        self,
        num_items,
        initial_qtys,
        ingredient_qtys,
        order_item_qtys,
    ):
        """
        **Validates: Requirements 11.2**

        Sub-property 22b: For an order with multiple OrderItems, each having
        its own recipe ingredient, all deductions are applied independently and
        the formula holds for every InventoryItem.
        """
        # Clamp lists to num_items
        initial_qtys = [_q4(v) for v in initial_qtys[:num_items]]
        ingredient_qtys = [_q4(v) for v in ingredient_qtys[:num_items]]
        order_item_qtys = order_item_qtys[:num_items]

        # Create one InventoryItem, MenuItem, Recipe, Ingredient per slot
        inventory_items = []
        menu_items = []
        for i in range(num_items):
            inv = InventoryItem.objects.create(
                branch=self.branch,
                name=f"Inv-{uuid.uuid4().hex[:8]}",
                category="test",
                quantity=initial_qtys[i],
                unit="g",
                purchase_price=_q2("1.00"),
                reorder_threshold=_q4("0.0001"),
            )
            inventory_items.append(inv)

            mi = MenuItem.objects.create(
                branch=self.branch,
                name=f"Dish-{uuid.uuid4().hex[:8]}",
                price=_q2("10.00"),
                prep_time_minutes=5,
                status="available",
            )
            menu_items.append(mi)

            recipe = Recipe.objects.create(
                menu_item=mi,
                method="Test method",
                cook_time_minutes=5,
            )

            Ingredient.objects.create(
                recipe=recipe,
                inventory_item=inv,
                quantity=ingredient_qtys[i],
                unit="g",
            )

        # Create a single order with all OrderItems
        order = Order.objects.create(
            branch=self.branch,
            table=self.table,
            status="confirmed",
            total_amount=_q2("50.00"),
        )

        for i in range(num_items):
            OrderItem.objects.create(
                order=order,
                menu_item=menu_items[i],
                quantity=order_item_qtys[i],
                unit_price=_q2("10.00"),
            )

        # Run deduction
        with patch(
            "apps.inventory.tasks.check_inventory_thresholds.delay"
        ):
            deduct_inventory.run(str(order.id))

        # Verify formula for every inventory item
        for i in range(num_items):
            inventory_items[i].refresh_from_db()
            expected = initial_qtys[i] - ingredient_qtys[i] * order_item_qtys[i]
            self.assertEqual(
                inventory_items[i].quantity,
                expected,
                msg=(
                    f"Item {i}: Expected {expected}, got {inventory_items[i].quantity}. "
                    f"initial={initial_qtys[i]}, ingredient_qty={ingredient_qtys[i]}, "
                    f"order_item_qty={order_item_qtys[i]}"
                ),
            )

    # -----------------------------------------------------------------------
    # 22c — Negative quantities are allowed (Requirement 11.7)
    # -----------------------------------------------------------------------

    @given(
        initial_qty=st.decimals(
            min_value="0.0001",
            max_value="10.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
        ingredient_qty=st.decimals(
            min_value="10.0001",
            max_value="999.9999",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
        order_item_qty=order_item_qty_strategy,
    )
    @settings(max_examples=500)
    def test_property_22c_deduction_below_zero_allowed(
        self,
        initial_qty,
        ingredient_qty,
        order_item_qty,
    ):
        """
        **Validates: Requirements 11.2**

        Sub-property 22c: When a deduction drives the inventory below zero,
        the formula still applies exactly and negative quantities are stored
        without error (Requirement 11.7). The deduction is not blocked.
        """
        initial_qty = _q4(initial_qty)
        ingredient_qty = _q4(ingredient_qty)

        # initial_qty < ingredient_qty * order_item_qty  → result will be negative
        inventory_item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"NegItem-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=initial_qty,
            unit="ml",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("0.0001"),
        )

        menu_item = MenuItem.objects.create(
            branch=self.branch,
            name=f"NegDish-{uuid.uuid4().hex[:8]}",
            price=_q2("15.00"),
            prep_time_minutes=3,
            status="available",
        )

        recipe = Recipe.objects.create(
            menu_item=menu_item,
            method="Test method",
            cook_time_minutes=3,
        )

        Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=ingredient_qty,
            unit="ml",
        )

        order = Order.objects.create(
            branch=self.branch,
            table=self.table,
            status="confirmed",
            total_amount=_q2("15.00"),
        )

        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=order_item_qty,
            unit_price=_q2("15.00"),
        )

        # Run deduction — must not raise even though result is negative
        with patch(
            "apps.inventory.tasks.check_inventory_thresholds.delay"
        ):
            deduct_inventory.run(str(order.id))

        inventory_item.refresh_from_db()
        expected = initial_qty - ingredient_qty * order_item_qty

        # Result must be negative (confirming this is the below-zero case)
        self.assertLess(
            inventory_item.quantity,
            Decimal("0"),
            msg="Expected negative quantity after deduction",
        )

        # Formula still holds exactly
        self.assertEqual(
            inventory_item.quantity,
            expected,
            msg=(
                f"Expected {expected}, got {inventory_item.quantity}. "
                f"initial={initial_qty}, ingredient_qty={ingredient_qty}, "
                f"order_item_qty={order_item_qty}"
            ),
        )
