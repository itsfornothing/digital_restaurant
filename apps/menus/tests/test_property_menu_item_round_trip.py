"""
Property-Based Tests: Menu Item Round-Trip Integrity (Property 19)

For any valid MenuItem creation payload, saving and then retrieving that
MenuItem shall produce a record with all fields — name, price, description,
dietary_tags, nutritional values — identical to the input payload.

Sub-properties tested:
  19a — All scalar fields survive save+retrieve unchanged
  19b — Amharic Unicode fields round-trip without loss
  19c — NutritionProfile decimal fields and allergens survive save+retrieve
  19d — Any valid dietary_tags subset is stored and retrieved unchanged

Validates: Requirements 9.1
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.menus.models import DIETARY_TAGS, MenuItem, NutritionProfile

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

amharic_text = st.text(
    alphabet=st.characters(min_codepoint=0x1200, max_codepoint=0x137F),
    min_size=1,
    max_size=50,
)

name_strategy = st.one_of(
    st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
            whitelist_characters=" -",
        ),
    ).filter(lambda s: s.strip()),
    amharic_text,
)

description_strategy = st.text(
    min_size=0,
    max_size=500,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po", "Pd"),
        whitelist_characters=" -.,!?",
    ),
)

price_strategy = st.decimals(
    min_value="0.01",
    max_value="9999.99",
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

status_strategy = st.sampled_from(
    ["available", "unavailable", "seasonal", "archived"]
)

dietary_tags_strategy = st.lists(
    st.sampled_from(DIETARY_TAGS),
    min_size=0,
    max_size=len(DIETARY_TAGS),
    unique=True,
)

prep_time_strategy = st.integers(min_value=1, max_value=32767)

optional_decimal = st.one_of(
    st.none(),
    st.decimals(
        min_value="0.01",
        max_value="9999.99",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)

allergens_strategy = st.lists(
    st.text(
        min_size=1,
        max_size=30,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
    ),
    max_size=5,
    unique=True,
)


# ---------------------------------------------------------------------------
# Helper: quantize a Decimal (or None) to 2 decimal places
# ---------------------------------------------------------------------------

def _q2(value) -> Decimal | None:
    """Return value quantized to 2 decimal places, or None."""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Property 19 Test Class
# ---------------------------------------------------------------------------


class TestPropertyMenuItemRoundTrip(TestCase):
    """
    Property 19: Menu Item Round-Trip Integrity

    For any valid MenuItem creation payload, saving and then retrieving
    that MenuItem shall produce a record with all fields identical to input.

    Validates: Requirements 9.1
    """

    def setUp(self):
        """Create a shared Branch for all property iterations."""
        from apps.branches.models import Branch

        self.branch = Branch.objects.create(
            name="Property Test Branch",
            address="Test Street",
            phone="0911000000",
            email="property@test.com",
        )

    # -----------------------------------------------------------------------
    # 19a — Scalar fields round-trip
    # -----------------------------------------------------------------------

    @given(
        name=name_strategy,
        description=description_strategy,
        price=price_strategy,
        prep_time_minutes=prep_time_strategy,
        status=status_strategy,
        dietary_tags=dietary_tags_strategy,
    )
    @settings(max_examples=500)
    def test_property_19a_scalar_fields_round_trip(
        self,
        name,
        description,
        price,
        prep_time_minutes,
        status,
        dietary_tags,
    ):
        """
        **Validates: Requirements 9.1**

        Sub-property 19a: All scalar fields survive save+retrieve unchanged.

        For any generated name, description, price, prep_time_minutes, status,
        and dietary_tags, saving a MenuItem and calling refresh_from_db()
        produces a record with each field exactly matching the input.
        """
        assume(name.strip())

        # Append a short uuid suffix to avoid any possible name collisions
        # across Hypothesis iterations within the same transaction.
        unique_name = f"{name[:191]}-{uuid.uuid4().hex[:8]}"

        expected_price = _q2(price)

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            description=description,
            price=expected_price,
            prep_time_minutes=prep_time_minutes,
            status=status,
            dietary_tags=dietary_tags,
        )
        item.refresh_from_db()

        self.assertEqual(item.name, unique_name)
        self.assertEqual(item.description, description)
        self.assertEqual(item.price, expected_price)
        self.assertEqual(item.prep_time_minutes, prep_time_minutes)
        self.assertEqual(item.status, status)
        # Order-insensitive comparison for dietary_tags (tested strictly in 19d)
        self.assertEqual(set(item.dietary_tags), set(dietary_tags))

    # -----------------------------------------------------------------------
    # 19b — Amharic Unicode round-trip
    # -----------------------------------------------------------------------

    @given(
        amharic_name=amharic_text,
        amharic_description=amharic_text,
    )
    @settings(max_examples=500)
    def test_property_19b_amharic_unicode_round_trip(
        self,
        amharic_name,
        amharic_description,
    ):
        """
        **Validates: Requirements 9.1**

        Sub-property 19b: Amharic Unicode fields round-trip without loss.

        For any Ethiopic string (U+1200–U+137F) used as the name or
        description, storing and retrieving the MenuItem returns byte-for-byte
        identical Unicode text.
        """
        unique_name = f"{amharic_name[:191]}-{uuid.uuid4().hex[:8]}"

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            description=amharic_description,
            price=_q2(Decimal("50.00")),
            prep_time_minutes=10,
            status="available",
        )
        item.refresh_from_db()

        self.assertEqual(item.name, unique_name)
        self.assertEqual(item.description, amharic_description)

    # -----------------------------------------------------------------------
    # 19c — NutritionProfile decimal fields and allergens round-trip
    # -----------------------------------------------------------------------

    @given(
        calories_kcal=optional_decimal,
        protein_g=optional_decimal,
        carbs_g=optional_decimal,
        fat_g=optional_decimal,
        saturated_fat_g=optional_decimal,
        sugar_g=optional_decimal,
        sodium_mg=optional_decimal,
        fibre_g=optional_decimal,
        allergens=allergens_strategy,
    )
    @settings(max_examples=500)
    def test_property_19c_nutrition_profile_round_trip(
        self,
        calories_kcal,
        protein_g,
        carbs_g,
        fat_g,
        saturated_fat_g,
        sugar_g,
        sodium_mg,
        fibre_g,
        allergens,
    ):
        """
        **Validates: Requirements 9.1**

        Sub-property 19c: NutritionProfile decimal fields and allergens
        survive save+retrieve.

        For any combination of optional decimal nutritional values and an
        allergens list, saving a NutritionProfile and calling refresh_from_db()
        produces values equal to the input (to 2 decimal places).
        """
        unique_name = f"NutriItem-{uuid.uuid4().hex[:8]}"

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(Decimal("30.00")),
            prep_time_minutes=5,
            status="available",
        )

        # Quantize all optional decimals before writing
        q_calories = _q2(calories_kcal)
        q_protein = _q2(protein_g)
        q_carbs = _q2(carbs_g)
        q_fat = _q2(fat_g)
        q_saturated_fat = _q2(saturated_fat_g)
        q_sugar = _q2(sugar_g)
        q_sodium = _q2(sodium_mg)
        q_fibre = _q2(fibre_g)

        profile = NutritionProfile.objects.create(
            menu_item=item,
            calories_kcal=q_calories,
            protein_g=q_protein,
            carbs_g=q_carbs,
            fat_g=q_fat,
            saturated_fat_g=q_saturated_fat,
            sugar_g=q_sugar,
            sodium_mg=q_sodium,
            fibre_g=q_fibre,
            allergens=allergens,
        )
        profile.refresh_from_db()

        self.assertEqual(profile.calories_kcal, q_calories)
        self.assertEqual(profile.protein_g, q_protein)
        self.assertEqual(profile.carbs_g, q_carbs)
        self.assertEqual(profile.fat_g, q_fat)
        self.assertEqual(profile.saturated_fat_g, q_saturated_fat)
        self.assertEqual(profile.sugar_g, q_sugar)
        self.assertEqual(profile.sodium_mg, q_sodium)
        self.assertEqual(profile.fibre_g, q_fibre)
        self.assertEqual(profile.allergens, allergens)

    # -----------------------------------------------------------------------
    # 19d — dietary_tags subset round-trip (order-insensitive)
    # -----------------------------------------------------------------------

    @given(tags=dietary_tags_strategy)
    @settings(max_examples=500)
    def test_property_19d_dietary_tags_subset_round_trip(self, tags):
        """
        **Validates: Requirements 9.1**

        Sub-property 19d: Any valid dietary_tags subset is stored and
        retrieved unchanged (order-insensitive comparison).

        For any non-empty or empty subset of the valid DIETARY_TAGS list,
        the stored list — when compared as a set — must equal the input set.
        """
        unique_name = f"TagItem-{uuid.uuid4().hex[:8]}"

        item = MenuItem.objects.create(
            branch=self.branch,
            name=unique_name,
            price=_q2(Decimal("25.00")),
            prep_time_minutes=5,
            status="available",
            dietary_tags=tags,
        )
        item.refresh_from_db()

        self.assertEqual(set(item.dietary_tags), set(tags))
        # Also verify no extra or missing tags have appeared
        self.assertEqual(len(item.dietary_tags), len(tags))
