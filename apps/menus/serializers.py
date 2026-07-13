"""
menus/serializers.py

DRF serializers for MenuItem, Category, NutritionProfile, Recipe, and Ingredient.

Hierarchy:
  - IngredientSerializer     — single recipe ingredient (nested under RecipeSerializer)
  - RecipeSerializer         — cooking recipe with nested ingredients
  - NutritionProfileSerializer — nutritional values (nested under MenuItemSerializer)
  - MenuItemListSerializer   — lightweight read serializer for list views
  - MenuItemSerializer       — full CRUD serializer for create / update / retrieve

Requirements: 9.1, 9.2, 9.4, 9.5, 9.6, 14.5
"""

from __future__ import annotations

from django.utils.translation import get_language
from rest_framework import serializers

from apps.menus.models import (
    Category,
    Ingredient,
    MenuItem,
    NutritionProfile,
    Recipe,
)


# ---------------------------------------------------------------------------
# Category serializer
# ---------------------------------------------------------------------------


class CategorySerializer(serializers.ModelSerializer):
    """
    Minimal serializer for Category — used when embedding categories in
    MenuItem responses and for list/create of categories under a branch.
    """

    name_translated = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "name_am", "name_translated"]
        read_only_fields = ["id"]

    def get_name_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.name_am:
            return obj.name_am
        return obj.name


# ---------------------------------------------------------------------------
# IngredientSerializer (nested inside RecipeSerializer)
# ---------------------------------------------------------------------------


class IngredientSerializer(serializers.ModelSerializer):
    """
    Serializes a single Ingredient row.

    ``inventory_item`` is exposed as a UUID primary key so consumers can
    resolve the inventory item independently.  It is writable on create/update.
    ``inventory_item_name`` is a read-only helper for display in the UI.
    """

    inventory_item_id = serializers.UUIDField(source="inventory_item.id", read_only=True)
    inventory_item_name = serializers.CharField(source="inventory_item.name", read_only=True)

    class Meta:
        model = Ingredient
        fields = ["id", "inventory_item_id", "inventory_item_name", "inventory_item", "quantity", "unit"]
        read_only_fields = ["id", "inventory_item_id", "inventory_item_name"]
        extra_kwargs = {
            "inventory_item": {"write_only": True},
        }


# ---------------------------------------------------------------------------
# RecipeSerializer (nested inside MenuItemSerializer)
# ---------------------------------------------------------------------------


class RecipeSerializer(serializers.ModelSerializer):
    """
    Serializes a Recipe with its nested ingredient list.

    On write (create/update of a MenuItem with an embedded recipe dict), the
    recipe and its ingredients are created/updated by MenuItemSerializer's
    ``create`` / ``update`` methods — this serializer handles validation only.
    """

    ingredients = IngredientSerializer(many=True, required=False, default=list)

    class Meta:
        model = Recipe
        fields = ["id", "method", "cook_time_minutes", "ingredients"]
        read_only_fields = ["id"]


# ---------------------------------------------------------------------------
# NutritionProfileSerializer (nested inside MenuItemSerializer)
# ---------------------------------------------------------------------------


class NutritionProfileSerializer(serializers.ModelSerializer):
    """
    Serializes all nutritional macro/micro fields.  Every field is optional.
    """

    class Meta:
        model = NutritionProfile
        fields = [
            "calories_kcal",
            "protein_g",
            "carbs_g",
            "fat_g",
            "saturated_fat_g",
            "sugar_g",
            "sodium_mg",
            "fibre_g",
            "allergens",
        ]


# ---------------------------------------------------------------------------
# MenuItemListSerializer — lightweight, no nested recipe
# ---------------------------------------------------------------------------


class MenuItemListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for GET /api/v1/branches/{id}/menu-items/ list view.

    Omits the full recipe to keep the payload compact.  Includes a summary of
    categories (IDs + names) and the nutrition profile.
    """

    name_translated = serializers.SerializerMethodField()
    description_translated = serializers.SerializerMethodField()
    categories = CategorySerializer(many=True, read_only=True)
    nutrition = NutritionProfileSerializer(read_only=True)

    def get_name_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.name_am:
            return obj.name_am
        return obj.name

    def get_description_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.description_am:
            return obj.description_am
        return obj.description

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "branch",
            "name",
            "name_am",
            "name_translated",
            "description",
            "description_am",
            "description_translated",
            "price",
            "prep_time_minutes",
            "status",
            "dietary_tags",
            "categories",
            "nutrition",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# MenuItemSerializer — full CRUD
# ---------------------------------------------------------------------------


class MenuItemSerializer(serializers.ModelSerializer):
    """
    Full serializer for MenuItem create / update / retrieve.

    Nested write support:
      - ``nutrition``  (optional) — creates / updates the NutritionProfile
      - ``recipe``     (optional) — creates / updates the Recipe + Ingredients
      - ``categories`` (optional) — list of category UUIDs; categories must
                                    belong to the same branch as the MenuItem

    Image handling:
      - ``image`` is write-only on upload (avoids returning large URLs in write
        responses); the URL is included in read responses via ``image_url``.

    Requirements: 9.1
    """

    # Writable category IDs — validated to belong to same branch
    category_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        write_only=True,
        help_text="List of Category UUIDs to assign.  Must belong to the same branch.",
    )
    # Translated fields
    name_translated = serializers.SerializerMethodField()
    description_translated = serializers.SerializerMethodField()
    # Read-only category summary in responses
    categories = CategorySerializer(many=True, read_only=True)

    # Nested optional objects
    nutrition = NutritionProfileSerializer(required=False, allow_null=True)
    recipe = RecipeSerializer(required=False, allow_null=True)

    # Image: write-only on upload, expose URL in read
    image = serializers.ImageField(required=False, allow_null=True, write_only=True)
    # image_url: writable — accepts a Cloudinary/CDN URL sent by the client
    image_url = serializers.SerializerMethodField(read_only=True)
    # Write-only field to receive the Cloudinary URL from the frontend
    external_image_url = serializers.URLField(
        required=False, allow_blank=True, write_only=False, default=""
    )

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "branch",
            "name",
            "name_am",
            "name_translated",
            "description",
            "description_am",
            "description_translated",
            "image",
            "image_url",
            "external_image_url",
            "price",
            "prep_time_minutes",
            "status",
            "dietary_tags",
            "category_ids",
            "categories",
            "nutrition",
            "recipe",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "branch", "categories", "created_at", "updated_at"]

    def get_image_url(self, obj) -> str | None:
        """Return the best available image URL.

        Priority:
        1. external_image_url (Cloudinary CDN URL) — set by the frontend
        2. image field (local filesystem or R2) — set by multipart upload
        """
        # 1. Cloudinary / external CDN URL
        if getattr(obj, "external_image_url", None):
            return obj.external_image_url

        # 2. Local / R2 ImageField
        if obj.image and obj.image.name:
            request = self.context.get("request")
            if request is not None:
                try:
                    return request.build_absolute_uri(obj.image.url)
                except Exception:
                    pass
            try:
                return obj.image.url
            except Exception:
                pass
        return None

    def get_name_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.name_am:
            return obj.name_am
        return obj.name

    def get_description_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.description_am:
            return obj.description_am
        return obj.description

    def validate_dietary_tags(self, value):
        """Validate that all supplied dietary tags are in the allowed list."""
        from apps.menus.models import DIETARY_TAGS
        invalid = set(value) - set(DIETARY_TAGS)
        if invalid:
            raise serializers.ValidationError(
                f"Invalid dietary tags: {sorted(invalid)}. "
                f"Valid values are: {sorted(DIETARY_TAGS)}."
            )
        return value

    def validate_category_ids(self, value):
        """Validate that category UUIDs exist.  Branch scope check in validate()."""
        if not value:
            return value
        existing_count = Category.objects.filter(id__in=value).count()
        if existing_count != len(set(str(v) for v in value)):
            raise serializers.ValidationError(
                "One or more category IDs do not exist."
            )
        return value

    def validate(self, attrs):
        """Cross-field validation: categories must belong to the same branch."""
        category_ids = attrs.get("category_ids", [])
        # On update, branch is from the instance; on create, it comes from view kwargs
        branch = (
            getattr(self.instance, "branch", None)
            or self.context.get("branch")
        )
        if category_ids and branch is not None:
            wrong_branch = Category.objects.filter(id__in=category_ids).exclude(
                branch=branch
            )
            if wrong_branch.exists():
                raise serializers.ValidationError(
                    {"category_ids": "All categories must belong to the same branch as the menu item."}
                )
        return attrs

    def create(self, validated_data):
        """
        Create a MenuItem with optional nested NutritionProfile and Recipe.

        Pops nested data before calling ``MenuItem.objects.create``, then
        creates the related objects.
        """
        category_ids = validated_data.pop("category_ids", [])
        nutrition_data = validated_data.pop("nutrition", None)
        recipe_data = validated_data.pop("recipe", None)

        menu_item = MenuItem.objects.create(**validated_data)

        # Assign categories
        if category_ids:
            menu_item.categories.set(category_ids)

        # Create nutrition profile
        if nutrition_data is not None:
            NutritionProfile.objects.create(menu_item=menu_item, **nutrition_data)

        # Create recipe with ingredients
        if recipe_data is not None:
            ingredients_data = recipe_data.pop("ingredients", [])
            recipe = Recipe.objects.create(menu_item=menu_item, **recipe_data)
            for ing_data in ingredients_data:
                # IngredientSerializer uses source="inventory_item.id" for read;
                # write uses the actual FK field "inventory_item"
                Ingredient.objects.create(recipe=recipe, **ing_data)

        return menu_item

    def update(self, instance, validated_data):
        """
        Partial update a MenuItem, including nested nutrition and recipe.

        - For nutrition: create if not exists, update if exists, delete if None passed.
        - For recipe: create/update recipe and replace all existing ingredients.
        - For category_ids: replace the M2M set.
        """
        category_ids = validated_data.pop("category_ids", None)
        nutrition_data = validated_data.pop("nutrition", _UNSET)
        recipe_data = validated_data.pop("recipe", _UNSET)

        # Update scalar fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update categories
        if category_ids is not None:
            instance.categories.set(category_ids)

        # Update nutrition profile
        if nutrition_data is not _UNSET:
            if nutrition_data is None:
                NutritionProfile.objects.filter(menu_item=instance).delete()
            else:
                NutritionProfile.objects.update_or_create(
                    menu_item=instance,
                    defaults=nutrition_data,
                )

        # Update recipe
        if recipe_data is not _UNSET:
            if recipe_data is None:
                Recipe.objects.filter(menu_item=instance).delete()
            else:
                ingredients_data = recipe_data.pop("ingredients", None)
                recipe, _ = Recipe.objects.update_or_create(
                    menu_item=instance,
                    defaults=recipe_data,
                )
                if ingredients_data is not None:
                    # Replace all ingredients
                    recipe.ingredients.all().delete()
                    for ing_data in ingredients_data:
                        Ingredient.objects.create(recipe=recipe, **ing_data)

        return instance


# ---------------------------------------------------------------------------
# RecipeDetailSerializer — used for GET /api/v1/menu-items/{id}/recipe/
# ---------------------------------------------------------------------------


class RecipeDetailSerializer(serializers.ModelSerializer):
    """
    Full recipe detail serializer including all ingredients.
    Used by the recipe endpoint consumed by the KDS (Requirement 10.5).
    """

    ingredients = IngredientSerializer(many=True, read_only=True)
    menu_item_id = serializers.UUIDField(source="menu_item.id", read_only=True)
    menu_item_name = serializers.CharField(source="menu_item.name", read_only=True)

    class Meta:
        model = Recipe
        fields = [
            "id",
            "menu_item_id",
            "menu_item_name",
            "method",
            "cook_time_minutes",
            "ingredients",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# RecipeUpdateSerializer — PATCH /api/v1/menu-items/{id}/recipe/
# ---------------------------------------------------------------------------


class RecipeUpdateSerializer(serializers.ModelSerializer):
    """
    Allows partial update of recipe method and cook time.
    """

    class Meta:
        model = Recipe
        fields = ["method", "cook_time_minutes"]


# ---------------------------------------------------------------------------
# AddIngredientSerializer — POST /api/v1/menu-items/{id}/recipe/ingredients/
# ---------------------------------------------------------------------------


class AddIngredientSerializer(serializers.Serializer):
    inventory_item_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=4)
    unit = serializers.CharField(max_length=20)


# Sentinel for distinguishing "not passed" from "passed as None" in update()
_UNSET = object()
