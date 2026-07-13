"""
apps/menus/tests/test_menu_models.py

Unit tests for menu models: Category, MenuItem, NutritionProfile, Recipe, Ingredient.

Tests verify:
  - Model creation with required fields
  - __str__ representations
  - Field defaults
  - Relationships (OneToOne, M2M, FK)
  - Cascade / protect delete behaviour

Requirements: 9.1, 9.3, 9.6
"""

import decimal
import uuid

import pytest
from django.db import IntegrityError

from apps.branches.models import Branch
from apps.inventory.models import InventoryItem, Supplier
from apps.menus.models import (
    DIETARY_TAGS,
    Category,
    Ingredient,
    MenuItem,
    NutritionProfile,
    Recipe,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def branch(db):
    return Branch.objects.create(
        name="Test Branch",
        address="123 Main Street",
        phone="0911000001",
        email="branch@test.com",
    )


@pytest.fixture
def category(branch):
    return Category.objects.create(branch=branch, name="Starters")


@pytest.fixture
def menu_item(branch):
    return MenuItem.objects.create(
        branch=branch,
        name="Injera",
        price=decimal.Decimal("45.00"),
        prep_time_minutes=15,
        status="available",
    )


@pytest.fixture
def nutrition_profile(menu_item):
    return NutritionProfile.objects.create(
        menu_item=menu_item,
        calories_kcal=decimal.Decimal("350.00"),
        protein_g=decimal.Decimal("12.50"),
        carbs_g=decimal.Decimal("60.00"),
        fat_g=decimal.Decimal("5.00"),
        saturated_fat_g=decimal.Decimal("1.50"),
        sugar_g=decimal.Decimal("2.00"),
        sodium_mg=decimal.Decimal("800.00"),
        fibre_g=decimal.Decimal("4.00"),
        allergens=["gluten"],
    )


@pytest.fixture
def recipe(menu_item):
    return Recipe.objects.create(
        menu_item=menu_item,
        method="Mix teff flour with water. Ferment 2–3 days. Pour on hot clay plate.",
        cook_time_minutes=5,
    )


@pytest.fixture
def supplier(branch):
    return Supplier.objects.create(
        branch=branch,
        name="Addis Suppliers",
        contact="0912000001",
    )


@pytest.fixture
def inventory_item(branch, supplier):
    return InventoryItem.objects.create(
        branch=branch,
        name="Teff Flour",
        category="Grain",
        quantity=decimal.Decimal("50.0000"),
        unit="kg",
        purchase_price=decimal.Decimal("120.00"),
        supplier=supplier,
        reorder_threshold=decimal.Decimal("10.0000"),
    )


# ---------------------------------------------------------------------------
# Category model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCategoryModel:

    def test_create_category(self, category):
        assert category.pk is not None
        assert isinstance(category.id, uuid.UUID)
        assert category.name == "Starters"

    def test_category_str(self, category):
        assert str(category) == f"Starters ({category.branch_id})"

    def test_category_uuid_pk(self, branch):
        cat = Category.objects.create(branch=branch, name="Mains")
        assert isinstance(cat.id, uuid.UUID)

    def test_category_unique_name_per_branch(self, branch):
        Category.objects.create(branch=branch, name="Desserts")
        with pytest.raises(IntegrityError):
            Category.objects.create(branch=branch, name="Desserts")

    def test_category_same_name_different_branches(self, branch, db):
        branch2 = Branch.objects.create(
            name="Branch 2", address="2 St", phone="0900000002", email="b2@test.com"
        )
        Category.objects.create(branch=branch, name="Drinks")
        # Should not raise — different branch
        cat2 = Category.objects.create(branch=branch2, name="Drinks")
        assert cat2.pk is not None

    def test_category_cascade_delete_with_branch(self, branch):
        cat = Category.objects.create(branch=branch, name="Soups")
        branch_id = branch.id
        branch.delete()
        assert Category.objects.filter(id=cat.id).count() == 0

    def test_category_ordering(self, branch):
        Category.objects.create(branch=branch, name="Zebra")
        Category.objects.create(branch=branch, name="Apple")
        names = list(Category.objects.filter(branch=branch).values_list("name", flat=True))
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# MenuItem model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMenuItemModel:

    def test_create_menu_item(self, menu_item):
        assert menu_item.pk is not None
        assert isinstance(menu_item.id, uuid.UUID)
        assert menu_item.name == "Injera"

    def test_menu_item_str(self, menu_item):
        assert str(menu_item) == f"Injera ({menu_item.branch_id})"

    def test_menu_item_uuid_pk(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="Tibs",
            price=decimal.Decimal("120.00"),
            prep_time_minutes=20,
        )
        assert isinstance(item.id, uuid.UUID)

    def test_menu_item_default_status(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="Default Status Item",
            price=decimal.Decimal("50.00"),
            prep_time_minutes=10,
        )
        assert item.status == "available"

    def test_menu_item_default_is_archived(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="Not Archived",
            price=decimal.Decimal("30.00"),
            prep_time_minutes=5,
        )
        assert item.is_archived is False

    def test_menu_item_default_description(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="No Description",
            price=decimal.Decimal("25.00"),
            prep_time_minutes=5,
        )
        assert item.description == ""

    def test_menu_item_default_dietary_tags(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="No Tags",
            price=decimal.Decimal("25.00"),
            prep_time_minutes=5,
        )
        assert item.dietary_tags == []

    def test_menu_item_timestamps(self, menu_item):
        assert menu_item.created_at is not None
        assert menu_item.updated_at is not None

    def test_menu_item_image_null_blank(self, branch):
        # Image is optional — null/blank allowed
        item = MenuItem.objects.create(
            branch=branch,
            name="No Image",
            price=decimal.Decimal("50.00"),
            prep_time_minutes=10,
        )
        assert item.image.name in (None, "")

    def test_menu_item_status_choices(self, branch):
        for status_val in ("available", "unavailable", "seasonal", "archived"):
            item = MenuItem.objects.create(
                branch=branch,
                name=f"Item {status_val}",
                price=decimal.Decimal("10.00"),
                prep_time_minutes=5,
                status=status_val,
            )
            item.refresh_from_db()
            assert item.status == status_val

    def test_menu_item_dietary_tags_stored_as_list(self, branch):
        tags = ["vegetarian", "vegan", "halal"]
        item = MenuItem.objects.create(
            branch=branch,
            name="Veggie Dish",
            price=decimal.Decimal("80.00"),
            prep_time_minutes=12,
            dietary_tags=tags,
        )
        item.refresh_from_db()
        assert item.dietary_tags == tags

    def test_menu_item_all_dietary_tags_valid(self):
        """Verify all 13 dietary tags from Requirement 9.6 are present."""
        expected = {
            "vegetarian", "vegan", "gluten_free", "dairy_free", "halal",
            "low_carb", "keto", "high_protein", "spicy", "seafood",
            "desserts", "beverages", "childrens_meals",
        }
        assert set(DIETARY_TAGS) == expected

    def test_menu_item_category_assignment(self, menu_item, category):
        menu_item.categories.add(category)
        assert category in menu_item.categories.all()

    def test_menu_item_multiple_categories(self, branch, menu_item):
        cat1 = Category.objects.create(branch=branch, name="Breakfast")
        cat2 = Category.objects.create(branch=branch, name="Traditional")
        menu_item.categories.add(cat1, cat2)
        assert menu_item.categories.count() == 2

    def test_menu_item_cascade_delete_with_branch(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="To Delete",
            price=decimal.Decimal("20.00"),
            prep_time_minutes=5,
        )
        item_id = item.id
        branch.delete()
        assert MenuItem.objects.filter(id=item_id).count() == 0

    def test_menu_item_price_decimal_precision(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="Precise Price",
            price=decimal.Decimal("99.99"),
            prep_time_minutes=10,
        )
        item.refresh_from_db()
        assert item.price == decimal.Decimal("99.99")

    def test_menu_item_archive(self, menu_item):
        menu_item.is_archived = True
        menu_item.save()
        menu_item.refresh_from_db()
        assert menu_item.is_archived is True

    def test_menu_item_description_supports_amharic(self, branch):
        """Req 16.5: Free-text fields must support Amharic Unicode."""
        amharic_name = "ጣፋጭ ምግብ"
        amharic_desc = "ባህላዊ የኢትዮጵያ ምግብ"
        item = MenuItem.objects.create(
            branch=branch,
            name=amharic_name,
            description=amharic_desc,
            price=decimal.Decimal("75.00"),
            prep_time_minutes=20,
        )
        item.refresh_from_db()
        assert item.name == amharic_name
        assert item.description == amharic_desc


# ---------------------------------------------------------------------------
# NutritionProfile model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNutritionProfileModel:

    def test_create_nutrition_profile(self, nutrition_profile):
        assert nutrition_profile.pk is not None
        assert nutrition_profile.calories_kcal == decimal.Decimal("350.00")
        assert nutrition_profile.allergens == ["gluten"]

    def test_nutrition_profile_str(self, nutrition_profile):
        assert "Injera" in str(nutrition_profile)
        assert "Nutrition" in str(nutrition_profile)

    def test_nutrition_profile_all_fields_optional(self, menu_item):
        """All macro/micro fields can be null — only menu_item is required."""
        profile = NutritionProfile.objects.create(menu_item=menu_item)
        assert profile.calories_kcal is None
        assert profile.protein_g is None
        assert profile.carbs_g is None
        assert profile.fat_g is None
        assert profile.saturated_fat_g is None
        assert profile.sugar_g is None
        assert profile.sodium_mg is None
        assert profile.fibre_g is None
        assert profile.allergens == []

    def test_nutrition_profile_one_to_one(self, menu_item, nutrition_profile):
        # menu_item.nutrition reverse accessor must work
        assert menu_item.nutrition == nutrition_profile

    def test_nutrition_profile_cascade_delete(self, menu_item):
        profile = NutritionProfile.objects.create(
            menu_item=menu_item,
            calories_kcal=decimal.Decimal("200.00"),
        )
        profile_id = profile.pk
        menu_item.delete()
        assert NutritionProfile.objects.filter(pk=profile_id).count() == 0

    def test_nutrition_profile_allergens_stored_as_list(self, branch):
        item = MenuItem.objects.create(
            branch=branch,
            name="Allergen Test",
            price=decimal.Decimal("50.00"),
            prep_time_minutes=10,
        )
        allergens = ["gluten", "dairy", "nuts"]
        profile = NutritionProfile.objects.create(
            menu_item=item,
            allergens=allergens,
        )
        profile.refresh_from_db()
        assert profile.allergens == allergens


# ---------------------------------------------------------------------------
# Recipe model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecipeModel:

    def test_create_recipe(self, recipe):
        assert recipe.pk is not None
        assert recipe.cook_time_minutes == 5
        assert "teff" in recipe.method.lower()

    def test_recipe_str(self, recipe):
        assert "Recipe" in str(recipe)
        assert "Injera" in str(recipe)

    def test_recipe_one_to_one(self, menu_item, recipe):
        # menu_item.recipe reverse accessor must work
        assert menu_item.recipe == recipe

    def test_recipe_cascade_delete_with_menu_item(self, menu_item):
        rec = Recipe.objects.create(
            menu_item=menu_item,
            method="Test method",
            cook_time_minutes=10,
        )
        rec_id = rec.pk
        menu_item.delete()
        assert Recipe.objects.filter(pk=rec_id).count() == 0

    def test_recipe_method_supports_amharic(self, branch):
        """Req 16.5: Recipe method text must accept Amharic Unicode."""
        item = MenuItem.objects.create(
            branch=branch,
            name="Doro Wat",
            price=decimal.Decimal("150.00"),
            prep_time_minutes=60,
        )
        amharic_method = "ዶሮ ወጥ ማዘጋጃ ዘዴ"
        rec = Recipe.objects.create(
            menu_item=item,
            method=amharic_method,
            cook_time_minutes=45,
        )
        rec.refresh_from_db()
        assert rec.method == amharic_method


# ---------------------------------------------------------------------------
# Ingredient model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIngredientModel:

    def test_create_ingredient(self, recipe, inventory_item):
        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("0.5000"),
            unit="kg",
        )
        assert ingredient.pk is not None
        assert ingredient.quantity == decimal.Decimal("0.5000")
        assert ingredient.unit == "kg"

    def test_ingredient_str(self, recipe, inventory_item):
        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("2.0000"),
            unit="cups",
        )
        s = str(ingredient)
        assert "2.0000" in s
        assert "cups" in s

    def test_ingredient_fk_to_recipe(self, recipe, inventory_item):
        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("1.0000"),
            unit="g",
        )
        assert ingredient.recipe == recipe
        assert ingredient in recipe.ingredients.all()

    def test_ingredient_cascade_delete_with_recipe(self, recipe, inventory_item):
        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("1.0000"),
            unit="piece",
        )
        ing_id = ingredient.pk
        recipe.delete()
        assert Ingredient.objects.filter(pk=ing_id).count() == 0

    def test_ingredient_protect_inventory_item_deletion(self, recipe, inventory_item):
        """
        Deleting an InventoryItem referenced by an Ingredient must raise
        ProtectedError (on_delete=PROTECT — Requirement 9.7).
        """
        from django.db.models.deletion import ProtectedError

        Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("0.2500"),
            unit="kg",
        )
        with pytest.raises(ProtectedError):
            inventory_item.delete()

    def test_multiple_ingredients_per_recipe(self, recipe, branch, supplier):
        item2 = InventoryItem.objects.create(
            branch=branch,
            name="Salt",
            quantity=decimal.Decimal("5.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("10.00"),
            reorder_threshold=decimal.Decimal("1.0000"),
        )
        item3 = InventoryItem.objects.create(
            branch=branch,
            name="Water",
            quantity=decimal.Decimal("100.0000"),
            unit="litres",
            purchase_price=decimal.Decimal("2.00"),
            reorder_threshold=decimal.Decimal("20.0000"),
        )
        Ingredient.objects.create(
            recipe=recipe, inventory_item=item2, quantity=decimal.Decimal("0.0100"), unit="kg"
        )
        Ingredient.objects.create(
            recipe=recipe, inventory_item=item3, quantity=decimal.Decimal("0.5000"), unit="litres"
        )
        assert recipe.ingredients.count() == 2

    def test_ingredient_quantity_decimal_precision(self, recipe, inventory_item):
        """Quantity stores up to 4 decimal places."""
        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item=inventory_item,
            quantity=decimal.Decimal("1.2345"),
            unit="g",
        )
        ingredient.refresh_from_db()
        assert ingredient.quantity == decimal.Decimal("1.2345")


# ---------------------------------------------------------------------------
# Full model integration: MenuItem with all related models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMenuItemFullIntegration:
    """
    Tests that a MenuItem can be created with Category, NutritionProfile,
    Recipe, and Ingredients all linked correctly (Requirement 9.1).
    """

    def test_full_menu_item_creation(self, branch, supplier):
        # Create inventory items
        flour = InventoryItem.objects.create(
            branch=branch,
            name="Wheat Flour",
            quantity=decimal.Decimal("20.0000"),
            unit="kg",
            purchase_price=decimal.Decimal("80.00"),
            reorder_threshold=decimal.Decimal("5.0000"),
        )
        water = InventoryItem.objects.create(
            branch=branch,
            name="Water",
            quantity=decimal.Decimal("100.0000"),
            unit="litres",
            purchase_price=decimal.Decimal("1.00"),
            reorder_threshold=decimal.Decimal("10.0000"),
        )

        # Create category
        cat = Category.objects.create(branch=branch, name="Breads")

        # Create menu item
        item = MenuItem.objects.create(
            branch=branch,
            name="Ambasha",
            description="Traditional Ethiopian celebration bread.",
            price=decimal.Decimal("35.00"),
            prep_time_minutes=90,
            status="available",
            dietary_tags=["vegetarian", "vegan"],
        )
        item.categories.add(cat)

        # Add nutrition profile
        nutrition = NutritionProfile.objects.create(
            menu_item=item,
            calories_kcal=decimal.Decimal("280.00"),
            protein_g=decimal.Decimal("8.00"),
            carbs_g=decimal.Decimal("55.00"),
            fat_g=decimal.Decimal("3.00"),
            allergens=["gluten"],
        )

        # Add recipe with ingredients
        rec = Recipe.objects.create(
            menu_item=item,
            method="Mix flour and water. Knead. Bake at 180°C for 40 mins.",
            cook_time_minutes=40,
        )
        Ingredient.objects.create(
            recipe=rec,
            inventory_item=flour,
            quantity=decimal.Decimal("0.5000"),
            unit="kg",
        )
        Ingredient.objects.create(
            recipe=rec,
            inventory_item=water,
            quantity=decimal.Decimal("0.3000"),
            unit="litres",
        )

        # Verify all associations
        item.refresh_from_db()
        assert item.categories.count() == 1
        assert item.nutrition.calories_kcal == decimal.Decimal("280.00")
        assert item.recipe.cook_time_minutes == 40
        assert item.recipe.ingredients.count() == 2
        assert item.dietary_tags == ["vegetarian", "vegan"]
        assert isinstance(item.id, uuid.UUID)
