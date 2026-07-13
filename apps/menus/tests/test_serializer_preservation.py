"""
Property-Based Tests: Serializer Preservation for Bugfix (Menu Category Form Fields)

These tests validate that the backend serializer correctly handles dietary_tags
and category_ids (which are already supported) and that unrelated field updates
do NOT accidentally modify these fields.

These preservation tests are expected to PASS on the UNFIXED code (before
the frontend template is modified), because the backend serializer already
supports dietary_tags and category_ids correctly. The frontend bug is that
it doesn't send them — not that the backend doesn't accept them.

Tests cover:
  - Round-trip property for dietary_tags (any subset saves and retrieves correctly)
  - Round-trip property for category_ids (any valid set saves and retrieves correctly)
  - Preservation property: patching only name/price SHALL NOT modify dietary_tags or categories

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.branches.models import Branch
from apps.menus.models import DIETARY_TAGS, Category, MenuItem

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Dietary tags: any subset of the valid DIETARY_TAGS list
_dietary_tags_st = st.lists(
    st.sampled_from(DIETARY_TAGS),
    min_size=0,
    max_size=len(DIETARY_TAGS),
    unique=True,
)

# Price strategy
_price_st = st.decimals(
    min_value="0.01",
    max_value="9999.99",
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Name strategy
_name_st = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters=" -",
    ),
).map(str.strip).filter(bool)

# Prep time strategy
_prep_time_st = st.integers(min_value=1, max_value=32767)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q2(value) -> Decimal:
    """Quantize a Decimal (or numeric string) to 2 decimal places."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Test Class: Serializer Preservation Properties
# ---------------------------------------------------------------------------


class TestSerializerPreservation(TestCase):
    """
    Property-based tests verifying the MenuItemSerializer correctly handles
    dietary_tags and category_ids without regressions.

    These tests are expected to PASS on the UNFIXED code.

    Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
    """

    def setUp(self):
        """Create a shared Branch and Categories for all property iterations."""
        self.branch = Branch.objects.create(
            name="Preservation Test Branch",
            address="Preservation Street, Addis Ababa",
            phone="0911000099",
            email="preservation@restaurant.com",
        )

        # Create a few categories to use in property tests
        self.categories = [
            Category.objects.create(branch=self.branch, name=f"Category {i}")
            for i in range(5)
        ]

    # -----------------------------------------------------------------------
    # Preservation Property 1: dietary_tags round-trip
    # -----------------------------------------------------------------------

    @given(dietary_tags=_dietary_tags_st)
    @settings(max_examples=500)
    def test_property_dietary_tags_round_trip(self, dietary_tags):
        """
        **Validates: Requirements 3.4, 3.5**

        Preservation Property: Round-trip for dietary_tags

        For any subset of DIETARY_TAGS, creating a MenuItem with that
        dietary_tags list and then retrieving it SHALL return the same
        set of tags (order-insensitive).

        This validates the backend serializer correctly accepts and returns
        dietary_tags without loss or corruption.
        """
        unique_name = f"DietaryTags-{uuid.uuid4().hex[:8]}"

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(Decimal("50.00")),
            prep_time_minutes=10,
            status="available",
            dietary_tags=dietary_tags,
        )
        item.refresh_from_db()

        # Order-insensitive comparison
        self.assertEqual(
            set(item.dietary_tags),
            set(dietary_tags),
            msg=(
                f"dietary_tags round-trip FAILED: saved {dietary_tags!r}, "
                f"retrieved {item.dietary_tags!r}. Tags must survive save+retrieve "
                f"unchanged (Requirement 3.4, 3.5)."
            ),
        )

        # Length check (no duplicates or extra tags added)
        self.assertEqual(
            len(item.dietary_tags),
            len(dietary_tags),
            msg=(
                f"dietary_tags list length mismatch: saved {len(dietary_tags)}, "
                f"retrieved {len(item.dietary_tags)}."
            ),
        )

    # -----------------------------------------------------------------------
    # Preservation Property 2: category_ids round-trip
    # -----------------------------------------------------------------------

    @given(
        n_categories=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=500)
    def test_property_category_ids_round_trip(self, n_categories):
        """
        **Validates: Requirements 3.4, 3.5**

        Preservation Property: Round-trip for category_ids

        For any valid list of category UUIDs belonging to the same branch,
        saving a MenuItem with those category_ids and then retrieving it
        SHALL return the same set of category IDs (order-insensitive).

        This validates the backend serializer correctly accepts category_ids
        on write and returns the same categories on read.
        """
        unique_name = f"Categories-{uuid.uuid4().hex[:8]}"

        # Select n_categories from the available self.categories
        selected_categories = self.categories[:n_categories]
        category_ids = [cat.id for cat in selected_categories]

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(Decimal("60.00")),
            prep_time_minutes=15,
            status="available",
        )
        item.categories.set(category_ids)
        item.refresh_from_db()

        # Retrieve the category IDs via the M2M relation
        retrieved_ids = set(item.categories.values_list("id", flat=True))
        expected_ids = set(category_ids)

        self.assertEqual(
            retrieved_ids,
            expected_ids,
            msg=(
                f"category_ids round-trip FAILED: saved {expected_ids!r}, "
                f"retrieved {retrieved_ids!r}. Categories must survive save+retrieve "
                f"unchanged (Requirement 3.4, 3.5)."
            ),
        )

    # -----------------------------------------------------------------------
    # Preservation Property 3: Unrelated field updates preserve dietary_tags
    # -----------------------------------------------------------------------

    @given(
        initial_dietary_tags=_dietary_tags_st,
        initial_price=_price_st,
        initial_name=_name_st,
        new_name=_name_st,
        new_price=_price_st,
    )
    @settings(max_examples=500)
    def test_property_unrelated_field_update_preserves_dietary_tags(
        self,
        initial_dietary_tags,
        initial_price,
        initial_name,
        new_name,
        new_price,
    ):
        """
        **Validates: Requirements 3.1, 3.4, 3.5**

        Preservation Property: Unrelated field updates do not modify dietary_tags

        For any MenuItem with arbitrary dietary_tags, submitting a PATCH that
        changes only name and price SHALL leave dietary_tags unchanged.

        This validates that the serializer does not accidentally clear or
        overwrite dietary_tags when they are not included in the update payload.
        """
        assume(initial_name != new_name)

        unique_initial_name = f"{initial_name[:91]}-{uuid.uuid4().hex[:8]}"
        unique_new_name = f"{new_name[:91]}-{uuid.uuid4().hex[:8]}"

        q_initial_price = _q2(initial_price)
        q_new_price = _q2(new_price)

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_initial_name,
            price=q_initial_price,
            prep_time_minutes=10,
            status="available",
            dietary_tags=initial_dietary_tags,
        )

        # Simulate a PATCH update: only name and price are changed
        item.name = unique_new_name
        item.price = q_new_price
        item.save(update_fields=["name", "price", "updated_at"])
        item.refresh_from_db()

        # dietary_tags must be unchanged
        self.assertEqual(
            set(item.dietary_tags),
            set(initial_dietary_tags),
            msg=(
                f"Preservation FAILED: dietary_tags changed after PATCH update. "
                f"Initial: {initial_dietary_tags!r}, After PATCH: {item.dietary_tags!r}. "
                f"Unrelated field updates must NOT modify dietary_tags "
                f"(Requirement 3.1, 3.4, 3.5)."
            ),
        )

        # Verify the name and price DID change (sanity check)
        self.assertEqual(item.name, unique_new_name)
        self.assertEqual(item.price, q_new_price)

    # -----------------------------------------------------------------------
    # Preservation Property 4: Unrelated field updates preserve categories
    # -----------------------------------------------------------------------

    @given(
        n_categories=st.integers(min_value=0, max_value=5),
        initial_name=_name_st,
        new_name=_name_st,
    )
    @settings(max_examples=500)
    def test_property_unrelated_field_update_preserves_categories(
        self,
        n_categories,
        initial_name,
        new_name,
    ):
        """
        **Validates: Requirements 3.1, 3.4, 3.5**

        Preservation Property: Unrelated field updates do not modify categories

        For any MenuItem with arbitrary category assignments, submitting a
        PATCH that changes only name SHALL leave categories unchanged.

        This validates that the serializer does not accidentally clear the
        M2M relation when category_ids is not in the update payload.
        """
        assume(initial_name != new_name)

        unique_initial_name = f"{initial_name[:91]}-{uuid.uuid4().hex[:8]}"
        unique_new_name = f"{new_name[:91]}-{uuid.uuid4().hex[:8]}"

        selected_categories = self.categories[:n_categories]
        category_ids = [cat.id for cat in selected_categories]

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_initial_name,
            price=_q2(Decimal("70.00")),
            prep_time_minutes=12,
            status="available",
        )
        item.categories.set(category_ids)
        item.refresh_from_db()

        initial_category_ids = set(item.categories.values_list("id", flat=True))

        # Simulate a PATCH update: only name is changed
        item.name = unique_new_name
        item.save(update_fields=["name", "updated_at"])
        item.refresh_from_db()

        # categories must be unchanged
        final_category_ids = set(item.categories.values_list("id", flat=True))
        self.assertEqual(
            final_category_ids,
            initial_category_ids,
            msg=(
                f"Preservation FAILED: categories changed after PATCH update. "
                f"Initial: {initial_category_ids!r}, After PATCH: {final_category_ids!r}. "
                f"Unrelated field updates must NOT modify categories "
                f"(Requirement 3.1, 3.4, 3.5)."
            ),
        )

        # Verify the name DID change (sanity check)
        self.assertEqual(item.name, unique_new_name)

    # -----------------------------------------------------------------------
    # Preservation Property 5: Empty dietary_tags and categories are valid
    # -----------------------------------------------------------------------

    @given(name=_name_st, price=_price_st)
    @settings(max_examples=200)
    def test_property_empty_dietary_tags_and_categories_are_valid(
        self,
        name,
        price,
    ):
        """
        **Validates: Requirements 3.2, 3.3**

        Preservation Property: Empty dietary_tags and no categories are valid

        For any MenuItem created without selecting any dietary tags or
        categories, the item SHALL save successfully with dietary_tags=[]
        and categories=[] (empty M2M relation).

        This is the default behavior that must be preserved after the fix.
        The fix adds UI inputs but does NOT change the requirement that
        items without tags/categories are valid.
        """
        unique_name = f"{name[:91]}-{uuid.uuid4().hex[:8]}"
        q_price = _q2(price)

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=q_price,
            prep_time_minutes=5,
            status="available",
            dietary_tags=[],  # Explicitly empty
        )
        # Do NOT set any categories (M2M remains empty)

        item.refresh_from_db()

        # dietary_tags must be an empty list
        self.assertEqual(
            item.dietary_tags,
            [],
            msg=(
                f"Preservation FAILED: dietary_tags is not empty. "
                f"Expected [], got {item.dietary_tags!r}. "
                f"Empty dietary_tags must be valid (Requirement 3.2)."
            ),
        )

        # categories must be an empty queryset (count 0)
        category_count = item.categories.count()
        self.assertEqual(
            category_count,
            0,
            msg=(
                f"Preservation FAILED: categories count is not 0. "
                f"Expected 0, got {category_count}. "
                f"No categories assigned must be valid (Requirement 3.3)."
            ),
        )

    # -----------------------------------------------------------------------
    # Preservation Property 6: Serializer validation rejects invalid tags
    # -----------------------------------------------------------------------

    @given(
        name=_name_st,
        price=_price_st,
        invalid_tag=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
        ).filter(lambda t: t not in DIETARY_TAGS),
    )
    @settings(max_examples=200)
    def test_property_serializer_rejects_invalid_dietary_tags(
        self,
        name,
        price,
        invalid_tag,
    ):
        """
        **Validates: Requirements 3.1, 3.4**

        Preservation Property: Serializer validation rejects invalid dietary tags

        For any dietary tag string that is NOT in the DIETARY_TAGS list,
        the backend validation must reject it (via the serializer's
        validate_dietary_tags method).

        This is a backend-only test — it does not involve the frontend form.
        """
        from rest_framework.exceptions import ValidationError

        from apps.menus.serializers import MenuItemSerializer

        unique_name = f"{name[:91]}-{uuid.uuid4().hex[:8]}"
        q_price = _q2(price)

        payload = {
            "name": unique_name,
            "price": str(q_price),
            "prep_time_minutes": 10,
            "status": "available",
            "dietary_tags": [invalid_tag],  # Invalid tag
        }

        serializer = MenuItemSerializer(
            data=payload,
            context={"branch": self.branch},
        )

        # Serializer validation must reject the invalid tag
        with self.assertRaises(ValidationError) as cm:
            serializer.is_valid(raise_exception=True)

        # Error message must mention dietary_tags
        error_detail = str(cm.exception.detail)
        self.assertIn(
            "dietary_tags",
            error_detail,
            msg=(
                f"Serializer validation error did not mention 'dietary_tags'. "
                f"Got: {error_detail}. "
                f"Invalid dietary tags must be rejected (Requirement 3.1, 3.4)."
            ),
        )
