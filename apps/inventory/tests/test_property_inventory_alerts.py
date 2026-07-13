"""
Property-Based Tests: Inventory Alert Generation Correctness (Property 23)

For any InventoryItem, alerts are generated when and only when the correct
threshold condition is met:
  - Low Stock:      0 < quantity <= reorder_threshold        (Req 11.3)
  - Out of Stock:   quantity <= 0                            (Req 11.5)
  - Expiry Warning: expiration_date within 3 days of today   (Req 11.4)

When quantity <= 0, Out of Stock fires (not Low Stock — takes precedence).
Expiry Warning is independent of stock level.

Sub-properties tested:
  23a — Low Stock fires when and only when 0 < quantity <= reorder_threshold
  23b — Out of Stock fires when and only when quantity <= 0
  23c — Expiry Warning fires when and only when expiration_date within 3 days
  23d — Expiry Warning is independent of stock level
  23e — No alerts fire when all conditions are clear

Validates: Requirements 11.3, 11.4, 11.5
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import call, patch

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.branches.models import Branch
from apps.inventory.models import InventoryItem
from apps.inventory.tasks import check_inventory_thresholds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q4(value) -> Decimal:
    """Return value as Decimal quantized to 4 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _q2(value) -> Decimal:
    """Return value as Decimal quantized to 2 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _alert_types(mock_delay) -> list[str]:
    """Extract all alert_type values from the mock's call list."""
    return [c.kwargs["alert_type"] for c in mock_delay.call_args_list]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Positive reorder_threshold in a moderate range
threshold_strategy = st.decimals(
    min_value="0.0001",
    max_value="100.0000",
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Property 23 Test Class
# ---------------------------------------------------------------------------


class TestPropertyInventoryAlertGeneration(TestCase):
    """
    Property 23: Inventory Alert Generation Correctness

    Validates: Requirements 11.3, 11.4, 11.5
    """

    def setUp(self):
        """Create a shared Branch reused across all Hypothesis iterations."""
        self.branch = Branch.objects.create(
            name=f"Alert Test Branch {uuid.uuid4().hex[:6]}",
            address="456 Alert Avenue",
            phone="0911000002",
            email="alerts@test.com",
        )

    # -----------------------------------------------------------------------
    # 23a — Low Stock fires iff 0 < quantity <= reorder_threshold
    # -----------------------------------------------------------------------

    @given(
        reorder_threshold=threshold_strategy,
        qty_below=st.decimals(
            min_value="0.0001",
            max_value="100.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23a_low_stock_fires_when_quantity_at_or_below_threshold(
        self,
        reorder_threshold,
        qty_below,
    ):
        """
        **Validates: Requirements 11.3**

        Sub-property 23a (positive case): When 0 < quantity <= reorder_threshold,
        a low_stock alert fires and no out_of_stock alert fires.
        """
        reorder_threshold = _q4(reorder_threshold)
        # Clamp qty_below so it is in (0, reorder_threshold]
        quantity = min(_q4(qty_below), reorder_threshold)
        # Ensure strictly positive
        if quantity <= Decimal("0"):
            quantity = Decimal("0.0001")
        if quantity > reorder_threshold:
            reorder_threshold = quantity

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"LowStk-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=reorder_threshold,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertIn(
            "low_stock", fired,
            msg=f"Expected low_stock alert. qty={quantity}, threshold={reorder_threshold}",
        )
        self.assertNotIn(
            "out_of_stock", fired,
            msg=f"out_of_stock must not fire when qty > 0. qty={quantity}",
        )

    @given(
        reorder_threshold=threshold_strategy,
        qty_above=st.decimals(
            min_value="0.0001",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23a_low_stock_does_not_fire_when_quantity_above_threshold(
        self,
        reorder_threshold,
        qty_above,
    ):
        """
        **Validates: Requirements 11.3**

        Sub-property 23a (negative case): When quantity > reorder_threshold,
        neither low_stock nor out_of_stock alerts fire.
        """
        reorder_threshold = _q4(reorder_threshold)
        # Ensure qty_above is strictly greater than threshold
        quantity = _q4(qty_above) + reorder_threshold + Decimal("0.0001")

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"AbvStk-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=reorder_threshold,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertNotIn(
            "low_stock", fired,
            msg=f"low_stock must not fire when qty > threshold. qty={quantity}, threshold={reorder_threshold}",
        )
        self.assertNotIn(
            "out_of_stock", fired,
            msg=f"out_of_stock must not fire when qty > threshold. qty={quantity}",
        )

    # -----------------------------------------------------------------------
    # 23b — Out of Stock fires iff quantity <= 0
    # -----------------------------------------------------------------------

    @given(
        quantity=st.decimals(
            min_value="-999.9999",
            max_value="0.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23b_out_of_stock_fires_when_quantity_at_or_below_zero(
        self,
        quantity,
    ):
        """
        **Validates: Requirements 11.5**

        Sub-property 23b (positive case): When quantity <= 0, out_of_stock fires
        and low_stock does NOT fire (out_of_stock takes precedence).
        """
        quantity = _q4(quantity)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"OoS-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            # Set a very low threshold so that only out_of_stock applies
            reorder_threshold=_q4("0.0001"),
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertIn(
            "out_of_stock", fired,
            msg=f"Expected out_of_stock alert. qty={quantity}",
        )
        self.assertNotIn(
            "low_stock", fired,
            msg=f"low_stock must not fire when qty <= 0 (out_of_stock takes precedence). qty={quantity}",
        )

    @given(
        quantity=st.decimals(
            min_value="0.0001",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23b_out_of_stock_does_not_fire_when_quantity_positive(
        self,
        quantity,
    ):
        """
        **Validates: Requirements 11.5**

        Sub-property 23b (negative case): When quantity > 0, out_of_stock does NOT fire.
        """
        quantity = _q4(quantity)
        # Set threshold well above quantity so only out_of_stock / low_stock
        # distinction is being tested (low_stock may or may not fire here)
        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"PosQty-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("0.0001"),
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertNotIn(
            "out_of_stock", fired,
            msg=f"out_of_stock must not fire when qty > 0. qty={quantity}",
        )

    # -----------------------------------------------------------------------
    # 23c — Expiry Warning fires iff expiration_date within 3 days
    # -----------------------------------------------------------------------

    @given(
        days_offset=st.integers(min_value=-5, max_value=3),
    )
    @settings(max_examples=500)
    def test_property_23c_expiry_warning_fires_within_3_days(
        self,
        days_offset,
    ):
        """
        **Validates: Requirements 11.4**

        Sub-property 23c (positive case): When expiration_date is within 3 days
        of today (days_offset in [-5, 3]), expiry_warning fires.
        Uses normal stock level so stock alerts don't interfere.
        """
        today = date.today()
        expiration_date = today + timedelta(days=days_offset)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"ExpSoon-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=_q4("100.0000"),
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("1.0000"),
            expiration_date=expiration_date,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertIn(
            "expiry_warning", fired,
            msg=f"Expected expiry_warning alert. days_offset={days_offset}, expiration_date={expiration_date}",
        )

    @given(
        days_offset=st.integers(min_value=4, max_value=30),
    )
    @settings(max_examples=500)
    def test_property_23c_expiry_warning_does_not_fire_beyond_3_days(
        self,
        days_offset,
    ):
        """
        **Validates: Requirements 11.4**

        Sub-property 23c (negative case): When expiration_date is more than 3
        days away (days_offset >= 4), expiry_warning does NOT fire.
        """
        today = date.today()
        expiration_date = today + timedelta(days=days_offset)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"ExpFar-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=_q4("100.0000"),
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("1.0000"),
            expiration_date=expiration_date,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertNotIn(
            "expiry_warning", fired,
            msg=f"expiry_warning must not fire when expiration is {days_offset} days away.",
        )

    @given(
        quantity=st.decimals(
            min_value="0.0001",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23c_expiry_warning_not_fired_when_no_expiration_date(
        self,
        quantity,
    ):
        """
        **Validates: Requirements 11.4**

        Sub-property 23c (no expiration_date): When expiration_date is None,
        expiry_warning never fires regardless of stock level.
        """
        quantity = _q4(quantity)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"NoExp-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=_q4("1.0000"),
            expiration_date=None,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        self.assertNotIn(
            "expiry_warning", fired,
            msg="expiry_warning must not fire when expiration_date is None.",
        )

    # -----------------------------------------------------------------------
    # 23d — Expiry Warning is independent of stock level
    # -----------------------------------------------------------------------

    @given(
        quantity=st.decimals(
            min_value="-10.0000",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
        reorder_threshold=threshold_strategy,
        days_offset=st.integers(min_value=-5, max_value=3),
    )
    @settings(max_examples=500)
    def test_property_23d_expiry_warning_independent_of_stock_level(
        self,
        quantity,
        reorder_threshold,
        days_offset,
    ):
        """
        **Validates: Requirements 11.4**

        Sub-property 23d: expiry_warning fires whenever expiration_date is
        within 3 days regardless of stock level. Additionally:
        - out_of_stock fires iff quantity <= 0
        - low_stock fires iff 0 < quantity <= reorder_threshold

        Both out_of_stock and expiry_warning can fire simultaneously.
        Both low_stock and expiry_warning can fire simultaneously.
        """
        quantity = _q4(quantity)
        reorder_threshold = _q4(reorder_threshold)
        today = date.today()
        expiration_date = today + timedelta(days=days_offset)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"IndExp-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=reorder_threshold,
            expiration_date=expiration_date,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]
        fired = [c.kwargs["alert_type"] for c in item_calls]

        # Expiry warning ALWAYS fires here (days_offset <= 3)
        self.assertIn(
            "expiry_warning", fired,
            msg=f"expiry_warning must fire. qty={quantity}, days_offset={days_offset}",
        )

        # Stock level rules still apply independently
        if quantity <= Decimal("0"):
            self.assertIn(
                "out_of_stock", fired,
                msg=f"out_of_stock must fire. qty={quantity}",
            )
            self.assertNotIn(
                "low_stock", fired,
                msg=f"low_stock must not fire when qty <= 0. qty={quantity}",
            )
        elif quantity <= reorder_threshold:
            self.assertIn(
                "low_stock", fired,
                msg=f"low_stock must fire. qty={quantity}, threshold={reorder_threshold}",
            )
            self.assertNotIn(
                "out_of_stock", fired,
                msg=f"out_of_stock must not fire when qty > 0. qty={quantity}",
            )
        else:
            self.assertNotIn(
                "low_stock", fired,
                msg=f"low_stock must not fire. qty={quantity}, threshold={reorder_threshold}",
            )
            self.assertNotIn(
                "out_of_stock", fired,
                msg=f"out_of_stock must not fire. qty={quantity}",
            )

    # -----------------------------------------------------------------------
    # 23e — No alerts fire when all conditions are clear
    # -----------------------------------------------------------------------

    @given(
        reorder_threshold=threshold_strategy,
        qty_above=st.decimals(
            min_value="0.0001",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
        days_far=st.integers(min_value=4, max_value=365),
    )
    @settings(max_examples=500)
    def test_property_23e_no_alerts_when_all_conditions_clear(
        self,
        reorder_threshold,
        qty_above,
        days_far,
    ):
        """
        **Validates: Requirements 11.3, 11.4, 11.5**

        Sub-property 23e: When quantity > reorder_threshold AND expiration_date
        is more than 3 days in the future (or None), zero alert calls are made.
        """
        reorder_threshold = _q4(reorder_threshold)
        # Ensure quantity is strictly above threshold
        quantity = _q4(qty_above) + reorder_threshold + Decimal("0.0001")
        today = date.today()
        expiration_date = today + timedelta(days=days_far)

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"Clear-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=reorder_threshold,
            expiration_date=expiration_date,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]

        self.assertEqual(
            len(item_calls),
            0,
            msg=(
                f"Expected zero alerts but got: "
                f"{[c.kwargs['alert_type'] for c in item_calls]}. "
                f"qty={quantity}, threshold={reorder_threshold}, days_far={days_far}"
            ),
        )

    @given(
        reorder_threshold=threshold_strategy,
        qty_above=st.decimals(
            min_value="0.0001",
            max_value="9999.0000",
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=500)
    def test_property_23e_no_alerts_when_conditions_clear_no_expiration(
        self,
        reorder_threshold,
        qty_above,
    ):
        """
        **Validates: Requirements 11.3, 11.4, 11.5**

        Sub-property 23e (no expiration_date variant): When quantity >
        reorder_threshold and expiration_date is None, zero alerts fire.
        """
        reorder_threshold = _q4(reorder_threshold)
        quantity = _q4(qty_above) + reorder_threshold + Decimal("0.0001")

        item = InventoryItem.objects.create(
            branch=self.branch,
            name=f"ClearNoExp-{uuid.uuid4().hex[:8]}",
            category="test",
            quantity=quantity,
            unit="kg",
            purchase_price=_q2("1.00"),
            reorder_threshold=reorder_threshold,
            expiration_date=None,
        )

        with patch("apps.inventory.tasks.send_inventory_alert.delay") as mock_delay:
            check_inventory_thresholds.run(str(self.branch.id))

        item_calls = [
            c for c in mock_delay.call_args_list
            if c.kwargs.get("item_id") == str(item.id)
        ]

        self.assertEqual(
            len(item_calls),
            0,
            msg=(
                f"Expected zero alerts but got: "
                f"{[c.kwargs['alert_type'] for c in item_calls]}. "
                f"qty={quantity}, threshold={reorder_threshold}"
            ),
        )
