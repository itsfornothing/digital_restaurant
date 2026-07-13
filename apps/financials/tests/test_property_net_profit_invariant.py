"""
apps/financials/tests/test_property_net_profit_invariant.py

Property-Based Tests: Net Profit Financial Invariant (Property 25)

For any branch and any arbitrary sequence of income and expense events,
net_profit == sum(income) - sum(expenses) must hold at all times —
immediately after every income or expense change.

Sub-properties tested:
  25a — Single income: net_profit == income amount
  25b — Single expense: net_profit == -expense amount (negative profit)
  25c — N income events: net_profit == sum of all income amounts
  25d — N income + M expense events: net_profit == sum(income) - sum(expenses)
  25e — Invariant holds immediately after adding a new income record
  25f — Invariant holds immediately after adding a new expense record
  25g — Interleaved arbitrary sequence: invariant holds after every operation

Validates: Requirements 13.3
"""

import uuid
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.financials.services import FinancialService

# ---------------------------------------------------------------------------
# Monetary amount strategy — positive decimals with 2 decimal places
# ---------------------------------------------------------------------------

amount_strategy = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

TWOPLACES = Decimal("0.01")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_branch():
    """Create a fresh Branch for isolation."""
    from apps.branches.models import Branch
    return Branch.objects.create(name=f"PBT-Branch-{uuid.uuid4().hex[:8]}")


def _add_income(branch, amount, date_val=None):
    """Add an Income record."""
    from apps.financials.models import Income
    return Income.objects.create(
        branch=branch,
        source="other",
        amount=amount,
        date=date_val or date.today(),
    )


def _add_expense(branch, amount, date_val=None):
    """Add an Expense record."""
    from apps.expenses.models import Expense
    return Expense.objects.create(
        branch=branch,
        description="PBT expense",
        category="miscellaneous",
        amount=amount,
        date_incurred=date_val or date.today(),
    )


def _compute_profit(branch, period="daily"):
    """Clear cache and compute profit for a branch."""
    cache.clear()
    return FinancialService.compute_profit(branch, period)


# ---------------------------------------------------------------------------
# Property 25 Tests
# ---------------------------------------------------------------------------

class TestPropertyNetProfitInvariant(TestCase):
    """
    **Validates: Requirements 13.3**

    Property-based tests confirming that FinancialService.compute_profit
    always satisfies net_profit == sum(income) - sum(expenses) for any
    arbitrary combination of income and expense records.
    """

    # -----------------------------------------------------------------------
    # 25a — Single income: net_profit == income amount
    # -----------------------------------------------------------------------

    @given(amount=amount_strategy)
    @settings(max_examples=200)
    def test_property_25a_single_income_equals_net_profit(self, amount):
        """
        **Validates: Requirements 13.3**

        For any positive decimal income amount added to a branch today,
        compute_profit("daily") must return net_profit == income amount.
        """
        assume(amount > Decimal("0"))
        branch = _make_branch()
        _add_income(branch, amount)

        result = _compute_profit(branch, "daily")
        net_profit = Decimal(result["net_profit"])

        self.assertEqual(
            net_profit.quantize(TWOPLACES),
            amount.quantize(TWOPLACES),
            f"Single income {amount}: expected net_profit={amount}, got {net_profit}",
        )

    # -----------------------------------------------------------------------
    # 25b — Single expense: net_profit == -expense amount
    # -----------------------------------------------------------------------

    @given(amount=amount_strategy)
    @settings(max_examples=200)
    def test_property_25b_single_expense_equals_negative_net_profit(self, amount):
        """
        **Validates: Requirements 13.3**

        For any positive decimal expense amount added to a branch today,
        compute_profit("daily") must return net_profit == -expense amount.
        """
        assume(amount > Decimal("0"))
        branch = _make_branch()
        _add_expense(branch, amount)

        result = _compute_profit(branch, "daily")
        net_profit = Decimal(result["net_profit"])
        expected = (-amount).quantize(TWOPLACES)

        self.assertEqual(
            net_profit.quantize(TWOPLACES),
            expected,
            f"Single expense {amount}: expected net_profit={expected}, got {net_profit}",
        )

    # -----------------------------------------------------------------------
    # 25c — N income events: net_profit == sum of all income amounts
    # -----------------------------------------------------------------------

    @given(amounts=st.lists(amount_strategy, min_size=0, max_size=15))
    @settings(max_examples=200)
    def test_property_25c_n_income_events_sum_invariant(self, amounts):
        """
        **Validates: Requirements 13.3**

        For any list of N positive income amounts, net_profit must equal
        the sum of those amounts.
        """
        branch = _make_branch()
        for amt in amounts:
            _add_income(branch, amt)

        result = _compute_profit(branch, "daily")
        net_profit = Decimal(result["net_profit"])
        expected = sum(amounts, Decimal("0.00")).quantize(TWOPLACES)

        self.assertEqual(
            net_profit.quantize(TWOPLACES),
            expected,
            f"N={len(amounts)} income events summing to {expected}: "
            f"got net_profit={net_profit}",
        )

    # -----------------------------------------------------------------------
    # 25d — N income + M expense events: core invariant
    # -----------------------------------------------------------------------

    @given(
        income_amounts=st.lists(amount_strategy, min_size=0, max_size=15),
        expense_amounts=st.lists(amount_strategy, min_size=0, max_size=15),
    )
    @settings(max_examples=500)
    def test_property_25d_n_income_m_expense_invariant(
        self, income_amounts, expense_amounts
    ):
        """
        **Validates: Requirements 13.3**

        For any list of income amounts and expense amounts,
        net_profit must equal sum(income_amounts) - sum(expense_amounts).
        This is the core net profit financial invariant.
        """
        branch = _make_branch()
        for amt in income_amounts:
            _add_income(branch, amt)
        for amt in expense_amounts:
            _add_expense(branch, amt)

        result = _compute_profit(branch, "daily")
        net_profit = Decimal(result["net_profit"])

        expected_income = sum(income_amounts, Decimal("0.00"))
        expected_expense = sum(expense_amounts, Decimal("0.00"))
        expected = (expected_income - expected_expense).quantize(TWOPLACES)

        self.assertEqual(
            net_profit.quantize(TWOPLACES),
            expected,
            f"N={len(income_amounts)} incomes, M={len(expense_amounts)} expenses: "
            f"expected net_profit={expected}, got {net_profit}",
        )

    # -----------------------------------------------------------------------
    # 25e — Invariant holds immediately after adding a new income record
    # -----------------------------------------------------------------------

    @given(
        income_amounts=st.lists(amount_strategy, min_size=0, max_size=10),
        expense_amounts=st.lists(amount_strategy, min_size=0, max_size=10),
        new_income=amount_strategy,
    )
    @settings(max_examples=200)
    def test_property_25e_invariant_after_new_income(
        self, income_amounts, expense_amounts, new_income
    ):
        """
        **Validates: Requirements 13.3**

        Start with known income and expenses, compute profit. Add one more
        income record. After clearing cache, compute profit again. The
        difference must equal exactly the new income amount.
        """
        assume(new_income > Decimal("0"))
        branch = _make_branch()

        for amt in income_amounts:
            _add_income(branch, amt)
        for amt in expense_amounts:
            _add_expense(branch, amt)

        result_before = _compute_profit(branch, "daily")
        profit_before = Decimal(result_before["net_profit"])

        _add_income(branch, new_income)

        result_after = _compute_profit(branch, "daily")
        profit_after = Decimal(result_after["net_profit"])

        delta = (profit_after - profit_before).quantize(TWOPLACES)
        expected_delta = new_income.quantize(TWOPLACES)

        self.assertEqual(
            delta,
            expected_delta,
            f"After adding income {new_income}: profit changed by {delta}, "
            f"expected change {expected_delta}",
        )

    # -----------------------------------------------------------------------
    # 25f — Invariant holds immediately after adding a new expense record
    # -----------------------------------------------------------------------

    @given(
        income_amounts=st.lists(amount_strategy, min_size=0, max_size=10),
        expense_amounts=st.lists(amount_strategy, min_size=0, max_size=10),
        new_expense=amount_strategy,
    )
    @settings(max_examples=200)
    def test_property_25f_invariant_after_new_expense(
        self, income_amounts, expense_amounts, new_expense
    ):
        """
        **Validates: Requirements 13.3**

        Start with known income and expenses, compute profit. Add one more
        expense record. After clearing cache, compute profit again. The
        decrease must equal exactly the new expense amount.
        """
        assume(new_expense > Decimal("0"))
        branch = _make_branch()

        for amt in income_amounts:
            _add_income(branch, amt)
        for amt in expense_amounts:
            _add_expense(branch, amt)

        result_before = _compute_profit(branch, "daily")
        profit_before = Decimal(result_before["net_profit"])

        _add_expense(branch, new_expense)

        result_after = _compute_profit(branch, "daily")
        profit_after = Decimal(result_after["net_profit"])

        delta = (profit_after - profit_before).quantize(TWOPLACES)
        expected_delta = (-new_expense).quantize(TWOPLACES)

        self.assertEqual(
            delta,
            expected_delta,
            f"After adding expense {new_expense}: profit changed by {delta}, "
            f"expected change {expected_delta}",
        )

    # -----------------------------------------------------------------------
    # 25g — Interleaved arbitrary sequence: invariant holds after every op
    # -----------------------------------------------------------------------

    @given(
        ops=st.lists(
            st.tuples(
                st.sampled_from(["income", "expense"]),
                amount_strategy,
            ),
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=500)
    def test_property_25g_interleaved_sequence_invariant(self, ops):
        """
        **Validates: Requirements 13.3**

        For any sequence of (op_type, amount) where op_type is 'income' or
        'expense', after processing the entire sequence, net_profit ==
        sum(income_amounts) - sum(expense_amounts). The invariant is checked
        after EACH operation, not just at the end.
        """
        branch = _make_branch()
        running_income = Decimal("0.00")
        running_expense = Decimal("0.00")

        for op_type, amount in ops:
            assume(amount > Decimal("0"))

            if op_type == "income":
                _add_income(branch, amount)
                running_income += amount
            else:
                _add_expense(branch, amount)
                running_expense += amount

            # Assert invariant holds after EACH operation
            result = _compute_profit(branch, "daily")
            net_profit = Decimal(result["net_profit"])
            expected = (running_income - running_expense).quantize(TWOPLACES)

            self.assertEqual(
                net_profit.quantize(TWOPLACES),
                expected,
                f"After op=({op_type!r}, {amount}): "
                f"expected net_profit={expected}, got {net_profit}. "
                f"running_income={running_income}, running_expense={running_expense}",
            )
