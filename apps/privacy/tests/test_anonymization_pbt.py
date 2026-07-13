"""
Property-Based Tests: Customer Data Anonymization (Property 29)

# Feature: restaurant-platform, Property 29: Customer Data Anonymization

Property 29: For any order ≥ 30 days old, name and phone are null (empty
string); financial fields remain intact.

Specifically:

  29a — PII Erasure on Old Orders: For any order whose placed_at is ≥ 30
        days before now, running anonymize_old_orders sets customer_name and
        customer_phone to '' and is_anonymized to True.

  29b — Recent Orders Untouched: For any order whose placed_at is < 30 days
        before now, running anonymize_old_orders leaves customer_name,
        customer_phone, and is_anonymized unchanged.

  29c — Financial Field Preservation: For ALL orders (old and recent),
        total_amount, table_id, branch_id, and placed_at are never modified
        by anonymize_old_orders.

  29d — Idempotency: Running anonymize_old_orders twice on already-anonymized
        orders produces the same result as running it once; is_anonymized
        remains True and no error is raised.

  29e — Mixed-Age Batch: Given a batch containing both old and recent orders,
        exactly the old ones are anonymized and exactly the recent ones are
        preserved — no cross-contamination.

Validates: Requirements 15.3, 15.4
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from django.utils import timezone

from apps.branches.models import Branch, Table
from apps.orders.models import Order
from apps.privacy.tasks import anonymize_old_orders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q2(value) -> Decimal:
    """Return value as Decimal quantized to 2 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _make_branch():
    """Create a minimal Branch for test use."""
    return Branch.objects.create(
        name=f"Anon Test Branch {uuid.uuid4().hex[:6]}",
        address="1 Privacy Lane",
        phone="0911111111",
        email=f"anon-{uuid.uuid4().hex[:6]}@test.com",
    )


def _make_table(branch):
    """Create a Table belonging to *branch*."""
    return Table.objects.create(
        branch=branch,
        number=str(uuid.uuid4().hex[:4]),
        seat_count=2,
    )


def _create_order(branch, table, placed_at, customer_name="", customer_phone=""):
    """
    Create an Order bypassing auto_now_add so we can back-date placed_at.
    """
    order = Order(
        branch=branch,
        table=table,
        status="served",
        customer_name=customer_name,
        customer_phone=customer_phone,
        is_anonymized=False,
        total_amount=_q2("50.00"),
    )
    # Set a unique order_number manually to avoid collisions.
    order.order_number = f"BR-TEST-{uuid.uuid4().hex[:12].upper()}"
    order.save()
    # Bypass auto_now_add by calling update() on the queryset.
    Order.objects.filter(pk=order.pk).update(placed_at=placed_at)
    order.refresh_from_db()
    return order


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Customer name: printable text, 1–100 chars, non-empty
customer_name_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters=" -'.",
    ),
    min_size=1,
    max_size=100,
).filter(lambda s: s.strip() != "")

# Customer phone: digits and common separators
customer_phone_strategy = st.from_regex(
    r"\+?[0-9]{7,15}", fullmatch=True
)

# Age of order in days — "old" means ≥ 30 days
old_order_age_days = st.integers(min_value=30, max_value=3650)  # 30 days to 10 years

# Age of order in days — "recent" means < 30 days
recent_order_age_days = st.integers(min_value=0, max_value=29)

# Total amount (financial field)
total_amount_strategy = st.decimals(
    min_value="1.00",
    max_value="9999.99",
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Number of orders in batch tests
batch_size_strategy = st.integers(min_value=1, max_value=8)


# ---------------------------------------------------------------------------
# Property 29 Test Class
# ---------------------------------------------------------------------------


class TestPropertyCustomerDataAnonymization(TestCase):
    """
    Property 29: Customer Data Anonymization

    **Validates: Requirements 15.3, 15.4**
    """

    def setUp(self):
        """Create a shared Branch and Table reused across all Hypothesis iterations."""
        self.branch = _make_branch()
        self.table = _make_table(self.branch)

    # -----------------------------------------------------------------------
    # 29a — PII Erasure on Old Orders
    # -----------------------------------------------------------------------

    @given(
        age_days=old_order_age_days,
        customer_name=customer_name_strategy,
        customer_phone=customer_phone_strategy,
        total_amount=total_amount_strategy,
    )
    @settings(max_examples=200)
    def test_property_29a_pii_erased_for_orders_30_days_or_older(
        self,
        age_days,
        customer_name,
        customer_phone,
        total_amount,
    ):
        """
        **Validates: Requirements 15.3, 15.4**

        Sub-property 29a: For any order whose placed_at is ≥ 30 days ago,
        running anonymize_old_orders must set customer_name = '',
        customer_phone = '', and is_anonymized = True.
        """
        placed_at = timezone.now() - timedelta(days=age_days)
        order = _create_order(
            self.branch,
            self.table,
            placed_at=placed_at,
            customer_name=customer_name,
            customer_phone=customer_phone,
        )
        # Record financial snapshot before anonymization
        amount_before = order.total_amount
        table_id_before = order.table_id
        branch_id_before = order.branch_id
        placed_at_before = order.placed_at

        anonymize_old_orders.run()

        order.refresh_from_db()

        # PII must be cleared
        self.assertEqual(
            order.customer_name,
            "",
            msg=(
                f"customer_name should be '' after anonymization "
                f"(age_days={age_days}), got '{order.customer_name}'"
            ),
        )
        self.assertEqual(
            order.customer_phone,
            "",
            msg=(
                f"customer_phone should be '' after anonymization "
                f"(age_days={age_days}), got '{order.customer_phone}'"
            ),
        )
        self.assertTrue(
            order.is_anonymized,
            msg=(
                f"is_anonymized should be True after anonymization "
                f"(age_days={age_days})"
            ),
        )

        # Financial / operational fields must be preserved (Requirement 15.4)
        self.assertEqual(
            order.total_amount,
            amount_before,
            msg=f"total_amount must not change. before={amount_before}, after={order.total_amount}",
        )
        self.assertEqual(
            order.table_id,
            table_id_before,
            msg="table FK must not change after anonymization",
        )
        self.assertEqual(
            order.branch_id,
            branch_id_before,
            msg="branch FK must not change after anonymization",
        )
        self.assertEqual(
            order.placed_at,
            placed_at_before,
            msg="placed_at must not change after anonymization",
        )

    # -----------------------------------------------------------------------
    # 29b — Recent Orders Untouched
    # -----------------------------------------------------------------------

    @given(
        age_days=recent_order_age_days,
        customer_name=customer_name_strategy,
        customer_phone=customer_phone_strategy,
    )
    @settings(max_examples=200)
    def test_property_29b_recent_orders_not_anonymized(
        self,
        age_days,
        customer_name,
        customer_phone,
    ):
        """
        **Validates: Requirements 15.3**

        Sub-property 29b: For any order whose placed_at is < 30 days ago,
        running anonymize_old_orders must NOT alter customer_name,
        customer_phone, or is_anonymized.
        """
        placed_at = timezone.now() - timedelta(days=age_days)
        order = _create_order(
            self.branch,
            self.table,
            placed_at=placed_at,
            customer_name=customer_name,
            customer_phone=customer_phone,
        )

        name_before = order.customer_name
        phone_before = order.customer_phone

        anonymize_old_orders.run()

        order.refresh_from_db()

        self.assertEqual(
            order.customer_name,
            name_before,
            msg=(
                f"customer_name must not be changed for recent orders "
                f"(age_days={age_days}). Expected '{name_before}', "
                f"got '{order.customer_name}'"
            ),
        )
        self.assertEqual(
            order.customer_phone,
            phone_before,
            msg=(
                f"customer_phone must not be changed for recent orders "
                f"(age_days={age_days}). Expected '{phone_before}', "
                f"got '{order.customer_phone}'"
            ),
        )
        self.assertFalse(
            order.is_anonymized,
            msg=(
                f"is_anonymized must remain False for orders < 30 days old "
                f"(age_days={age_days})"
            ),
        )

    # -----------------------------------------------------------------------
    # 29c — Financial Field Preservation for ALL orders
    # -----------------------------------------------------------------------

    @given(
        age_days=st.one_of(old_order_age_days, recent_order_age_days),
        total_amount=total_amount_strategy,
    )
    @settings(max_examples=200)
    def test_property_29c_financial_fields_preserved_for_all_orders(
        self,
        age_days,
        total_amount,
    ):
        """
        **Validates: Requirements 15.4**

        Sub-property 29c: Regardless of order age, total_amount, table_id,
        branch_id, and placed_at are never modified by anonymize_old_orders.
        """
        placed_at = timezone.now() - timedelta(days=age_days)
        order = _create_order(
            self.branch,
            self.table,
            placed_at=placed_at,
            customer_name="Test Customer",
            customer_phone="+251911000000",
        )
        # Override total_amount with the generated value
        order.total_amount = _q2(total_amount)
        order.save(update_fields=["total_amount"])

        amount_before = order.total_amount
        table_id_before = order.table_id
        branch_id_before = order.branch_id
        placed_at_before = order.placed_at

        anonymize_old_orders.run()

        order.refresh_from_db()

        self.assertEqual(
            order.total_amount,
            amount_before,
            msg=(
                f"total_amount changed unexpectedly. "
                f"before={amount_before}, after={order.total_amount}, "
                f"age_days={age_days}"
            ),
        )
        self.assertEqual(
            order.table_id,
            table_id_before,
            msg=f"table FK changed unexpectedly (age_days={age_days})",
        )
        self.assertEqual(
            order.branch_id,
            branch_id_before,
            msg=f"branch FK changed unexpectedly (age_days={age_days})",
        )
        self.assertEqual(
            order.placed_at,
            placed_at_before,
            msg=f"placed_at changed unexpectedly (age_days={age_days})",
        )

    # -----------------------------------------------------------------------
    # 29d — Idempotency
    # -----------------------------------------------------------------------

    @given(
        age_days=old_order_age_days,
        customer_name=customer_name_strategy,
        customer_phone=customer_phone_strategy,
    )
    @settings(max_examples=200)
    def test_property_29d_idempotent_for_already_anonymized_orders(
        self,
        age_days,
        customer_name,
        customer_phone,
    ):
        """
        **Validates: Requirements 15.3**

        Sub-property 29d: Running anonymize_old_orders twice on an order that
        was already anonymized must produce the same final state (is_anonymized=True,
        customer_name='', customer_phone='') without errors.
        """
        placed_at = timezone.now() - timedelta(days=age_days)
        order = _create_order(
            self.branch,
            self.table,
            placed_at=placed_at,
            customer_name=customer_name,
            customer_phone=customer_phone,
        )

        # First run
        anonymize_old_orders.run()
        order.refresh_from_db()

        self.assertTrue(order.is_anonymized)
        self.assertEqual(order.customer_name, "")
        self.assertEqual(order.customer_phone, "")

        # Second run — must be a no-op without error
        anonymize_old_orders.run()
        order.refresh_from_db()

        self.assertTrue(
            order.is_anonymized,
            msg="is_anonymized must remain True after second run",
        )
        self.assertEqual(
            order.customer_name,
            "",
            msg="customer_name must remain '' after second run",
        )
        self.assertEqual(
            order.customer_phone,
            "",
            msg="customer_phone must remain '' after second run",
        )

    # -----------------------------------------------------------------------
    # 29e — Mixed-Age Batch: old anonymized, recent preserved
    # -----------------------------------------------------------------------

    @given(
        num_old=batch_size_strategy,
        num_recent=batch_size_strategy,
        old_ages=st.lists(old_order_age_days, min_size=8, max_size=8),
        recent_ages=st.lists(recent_order_age_days, min_size=8, max_size=8),
        names=st.lists(customer_name_strategy, min_size=8, max_size=8),
        phones=st.lists(customer_phone_strategy, min_size=8, max_size=8),
    )
    @settings(max_examples=200)
    def test_property_29e_mixed_batch_old_anonymized_recent_preserved(
        self,
        num_old,
        num_recent,
        old_ages,
        recent_ages,
        names,
        phones,
    ):
        """
        **Validates: Requirements 15.3, 15.4**

        Sub-property 29e: In a batch of mixed-age orders, anonymize_old_orders
        anonymizes exactly the old orders (placed_at ≥ 30 days) and leaves
        recent orders (placed_at < 30 days) completely untouched.
        """
        now = timezone.now()

        # Create old orders
        old_orders = []
        for i in range(num_old):
            placed_at = now - timedelta(days=old_ages[i])
            o = _create_order(
                self.branch,
                self.table,
                placed_at=placed_at,
                customer_name=names[i],
                customer_phone=phones[i],
            )
            old_orders.append(o)

        # Create recent orders
        recent_orders = []
        for i in range(num_recent):
            placed_at = now - timedelta(days=recent_ages[i])
            o = _create_order(
                self.branch,
                self.table,
                placed_at=placed_at,
                customer_name=names[i],
                customer_phone=phones[i],
            )
            recent_orders.append((o, names[i], phones[i]))

        anonymize_old_orders.run()

        # All old orders must be anonymized
        for order in old_orders:
            order.refresh_from_db()
            self.assertEqual(
                order.customer_name,
                "",
                msg=f"Old order {order.pk}: customer_name should be '' after anonymization",
            )
            self.assertEqual(
                order.customer_phone,
                "",
                msg=f"Old order {order.pk}: customer_phone should be '' after anonymization",
            )
            self.assertTrue(
                order.is_anonymized,
                msg=f"Old order {order.pk}: is_anonymized should be True",
            )

        # All recent orders must be untouched
        for order, original_name, original_phone in recent_orders:
            order.refresh_from_db()
            self.assertEqual(
                order.customer_name,
                original_name,
                msg=(
                    f"Recent order {order.pk}: customer_name changed unexpectedly. "
                    f"Expected '{original_name}', got '{order.customer_name}'"
                ),
            )
            self.assertEqual(
                order.customer_phone,
                original_phone,
                msg=(
                    f"Recent order {order.pk}: customer_phone changed unexpectedly. "
                    f"Expected '{original_phone}', got '{order.customer_phone}'"
                ),
            )
            self.assertFalse(
                order.is_anonymized,
                msg=f"Recent order {order.pk}: is_anonymized must remain False",
            )
