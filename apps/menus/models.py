"""
menus/models.py

MenuItem, Category, NutritionProfile, Recipe, and Ingredient models.

All models live in tenant schemas (TENANT_APPS).

Dietary tags (Requirements 9.6, 14.6):
    Vegetarian, Vegan, Gluten-Free, Dairy-Free, Halal, Low-Carb, Keto,
    High-Protein, Spicy, Seafood, Desserts, Beverages, Children's Meals

Requirements: 9.1, 9.3, 9.6, 9.7, 14.5, 14.6
"""

import uuid

from django.db import models

from shared.storage import R2Storage

# ---------------------------------------------------------------------------
# Valid dietary tag values (Requirement 9.6, 14.6)
# ---------------------------------------------------------------------------

DIETARY_TAG_CHOICES = [
    ("vegetarian", "Vegetarian"),
    ("vegan", "Vegan"),
    ("gluten_free", "Gluten-Free"),
    ("dairy_free", "Dairy-Free"),
    ("halal", "Halal"),
    ("low_carb", "Low-Carb"),
    ("keto", "Keto"),
    ("high_protein", "High-Protein"),
    ("spicy", "Spicy"),
    ("seafood", "Seafood"),
    ("desserts", "Desserts"),
    ("beverages", "Beverages"),
    ("childrens_meals", "Children's Meals"),
]

DIETARY_TAGS = [tag for tag, _ in DIETARY_TAG_CHOICES]

# ---------------------------------------------------------------------------
# MenuItem availability status choices
# ---------------------------------------------------------------------------

MENU_ITEM_STATUS_CHOICES = [
    ("available", "Available"),
    ("unavailable", "Unavailable"),
    ("seasonal", "Seasonal"),
    ("archived", "Archived"),
]


class Category(models.Model):
    """
    Menu category grouping MenuItems within a Branch.

    A Branch_Manager creates categories (e.g. "Starters", "Mains", "Drinks")
    and assigns them to MenuItems.  A single MenuItem can belong to multiple
    categories (M2M from MenuItem side).

    Fields:
        id     — UUID primary key
        branch — FK to the owning Branch (CASCADE delete)
        name   — Human-readable category name (e.g. "Starters")
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=200)
    name_am = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Amharic translation of the category name.",
    )

    class Meta:
        app_label = "menus"
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]
        unique_together = [("branch", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.branch_id})"


class MenuItem(models.Model):
    """
    A dish or drink available on a Branch's menu.

    Customers see only items with status='available'.  Items with
    status='unavailable' or status='seasonal' are hidden from the customer
    menu but remain visible to staff.  Archived items (is_archived=True) are
    completely hidden from customers while preserving historical order
    associations (Requirement 9.3).

    Fields:
        id                — UUID primary key
        branch            — FK to owning Branch
        name              — Display name (supports Amharic Unicode)
        description       — Optional long description
        image             — Optional image stored in Cloudflare R2
        price             — Decimal price in branch currency
        prep_time_minutes — Estimated preparation time in minutes
        status            — One of: available / unavailable / seasonal / archived
        dietary_tags      — JSON list of dietary tag strings (Req 9.6)
        categories        — M2M to Category
        is_archived       — Soft-archive flag; archived items are excluded from
                            customer menus and billing counts (Req 9.3, 9.5)
        created_at        — Auto-set creation timestamp
        updated_at        — Auto-updated on every save
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="menu_items",
    )
    name = models.CharField(max_length=200)
    name_am = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Amharic translation of the menu item name.",
    )
    description = models.TextField(blank=True, default="")
    description_am = models.TextField(
        blank=True, default="",
        help_text="Amharic translation of the description.",
    )
    image = models.ImageField(
        upload_to="menu_items/",
        storage=R2Storage(),
        null=True,
        blank=True,
        help_text="Local/R2 file upload (used when R2 is configured)",
    )
    # External image URL — used when images are uploaded to Cloudinary or
    # another CDN. When set, takes precedence over the `image` field for display.
    external_image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Cloudinary or other CDN URL for the menu item image",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price in the branch's configured currency.",
    )
    prep_time_minutes = models.PositiveSmallIntegerField(
        help_text="Estimated preparation time in minutes.",
    )
    status = models.CharField(
        max_length=20,
        choices=MENU_ITEM_STATUS_CHOICES,
        default="available",
        db_index=True,
    )
    dietary_tags = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of dietary tag strings. Valid values: "
            + ", ".join(DIETARY_TAGS)
        ),
    )
    categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name="menu_items",
    )
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Archived items are hidden from customers but retained for "
            "historical order records (Requirement 9.3)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "menus"
        verbose_name = "Menu Item"
        verbose_name_plural = "Menu Items"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.branch_id})"


class NutritionProfile(models.Model):
    """
    Nutritional information for a MenuItem (Requirement 9.1, 14.5).

    All macro/micro fields are optional (null=True) so that a MenuItem can
    be saved without complete nutritional data.  The allergens field stores
    a JSON list of allergen strings (e.g. ["gluten", "dairy"]).

    Fields:
        menu_item        — OneToOne FK to MenuItem (CASCADE delete)
        calories_kcal    — Total calories (kcal)
        protein_g        — Protein (grams)
        carbs_g          — Total carbohydrates (grams)
        fat_g            — Total fat (grams)
        saturated_fat_g  — Saturated fat (grams)
        sugar_g          — Sugar (grams)
        sodium_mg        — Sodium (milligrams)
        fibre_g          — Dietary fibre (grams)
        allergens        — JSON list of allergen labels
    """

    menu_item = models.OneToOneField(
        MenuItem,
        on_delete=models.CASCADE,
        related_name="nutrition",
    )
    calories_kcal = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    protein_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    carbs_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    fat_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    saturated_fat_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    sugar_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    sodium_mg = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    fibre_g = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    allergens = models.JSONField(
        default=list,
        blank=True,
        help_text="List of allergen strings (e.g. ['gluten', 'dairy']).",
    )

    class Meta:
        app_label = "menus"
        verbose_name = "Nutrition Profile"
        verbose_name_plural = "Nutrition Profiles"

    def __str__(self) -> str:
        return f"Nutrition: {self.menu_item.name}"


class Recipe(models.Model):
    """
    Cooking recipe associated with a MenuItem (Requirement 9.1, 9.7, 10.5).

    Linked ingredient list enables automatic inventory deduction when an
    order transitions to Preparing (Requirement 9.7, 11.2).

    Fields:
        menu_item         — OneToOne FK to MenuItem (CASCADE delete)
        method            — Full preparation method / instructions
        cook_time_minutes — Total cooking time in minutes
    """

    menu_item = models.OneToOneField(
        MenuItem,
        on_delete=models.CASCADE,
        related_name="recipe",
    )
    method = models.TextField(
        help_text="Step-by-step preparation instructions.",
    )
    cook_time_minutes = models.PositiveSmallIntegerField(
        help_text="Total cook time in minutes.",
    )

    class Meta:
        app_label = "menus"
        verbose_name = "Recipe"
        verbose_name_plural = "Recipes"

    def __str__(self) -> str:
        return f"Recipe: {self.menu_item.name}"


class Ingredient(models.Model):
    """
    A single ingredient entry within a Recipe.

    Each Ingredient links a Recipe to an InventoryItem so that inventory can
    be automatically deducted when an order enters the Preparing state
    (Requirements 9.7, 11.2).

    The FK to ``inventory.InventoryItem`` uses a string reference so that this
    migration can be created before the InventoryItem model is fully defined
    in Task 12.  ``on_delete=PROTECT`` prevents accidental inventory item
    deletion when recipes reference it.

    Fields:
        recipe          — FK to owning Recipe (CASCADE delete)
        inventory_item  — FK to inventory.InventoryItem (PROTECT)
        quantity        — Amount of the inventory item required (up to 4 d.p.)
        unit            — Unit of measure (e.g. "g", "kg", "ml", "pieces")
    """

    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="ingredients",
    )
    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.PROTECT,
        related_name="recipe_ingredients",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text="Amount of this ingredient required per serving.",
    )
    unit = models.CharField(
        max_length=20,
        help_text="Unit of measure (e.g. 'g', 'kg', 'ml', 'pieces').",
    )

    class Meta:
        app_label = "menus"
        verbose_name = "Ingredient"
        verbose_name_plural = "Ingredients"
        ordering = ["id"]

    def __str__(self) -> str:
        return (
            f"{self.quantity} {self.unit} of "
            f"{getattr(self.inventory_item, 'name', self.inventory_item_id)}"
            f" (Recipe: {self.recipe_id})"
        )
