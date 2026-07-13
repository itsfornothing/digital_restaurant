"""
Initial migration for apps.menus.

Creates all menu-related models:
  - Category
  - MenuItem (with M2M to Category)
  - NutritionProfile (OneToOne → MenuItem)
  - Recipe (OneToOne → MenuItem)
  - Ingredient (FK → Recipe, FK → inventory.InventoryItem)

Requirements: 9.1, 9.3, 9.6, 9.7
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

import shared.storage


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("branches", "0003_full_branch_table"),
        ("inventory", "0002_inventoryitem_supplier"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Category
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Category",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="categories",
                        to="branches.branch",
                    ),
                ),
            ],
            options={
                "verbose_name": "Category",
                "verbose_name_plural": "Categories",
                "ordering": ["name"],
                "app_label": "menus",
            },
        ),
        migrations.AlterUniqueTogether(
            name="category",
            unique_together={("branch", "name")},
        ),
        # ------------------------------------------------------------------
        # MenuItem
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="MenuItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        storage=shared.storage.R2Storage(),
                        upload_to="",
                        help_text="Optional product image stored in Cloudflare R2.",
                    ),
                ),
                (
                    "price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        help_text="Price in the branch's configured currency.",
                    ),
                ),
                (
                    "prep_time_minutes",
                    models.PositiveSmallIntegerField(
                        help_text="Estimated preparation time in minutes.",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("seasonal", "Seasonal"),
                            ("archived", "Archived"),
                        ],
                        db_index=True,
                        default="available",
                        max_length=20,
                    ),
                ),
                (
                    "dietary_tags",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="List of dietary tag strings.",
                    ),
                ),
                (
                    "is_archived",
                    models.BooleanField(
                        db_index=True,
                        default=False,
                        help_text=(
                            "Archived items are hidden from customers but retained "
                            "for historical order records (Requirement 9.3)."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="menu_items",
                        to="branches.branch",
                    ),
                ),
                (
                    "categories",
                    models.ManyToManyField(
                        blank=True,
                        related_name="menu_items",
                        to="menus.category",
                    ),
                ),
            ],
            options={
                "verbose_name": "Menu Item",
                "verbose_name_plural": "Menu Items",
                "ordering": ["name"],
                "app_label": "menus",
            },
        ),
        # ------------------------------------------------------------------
        # NutritionProfile
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="NutritionProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "calories_kcal",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "protein_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "carbs_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "fat_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "saturated_fat_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "sugar_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "sodium_mg",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "fibre_g",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=7, null=True
                    ),
                ),
                (
                    "allergens",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="List of allergen strings (e.g. ['gluten', 'dairy']).",
                    ),
                ),
                (
                    "menu_item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nutrition",
                        to="menus.menuitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Nutrition Profile",
                "verbose_name_plural": "Nutrition Profiles",
                "app_label": "menus",
            },
        ),
        # ------------------------------------------------------------------
        # Recipe
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Recipe",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "method",
                    models.TextField(
                        help_text="Step-by-step preparation instructions.",
                    ),
                ),
                (
                    "cook_time_minutes",
                    models.PositiveSmallIntegerField(
                        help_text="Total cook time in minutes.",
                    ),
                ),
                (
                    "menu_item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recipe",
                        to="menus.menuitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Recipe",
                "verbose_name_plural": "Recipes",
                "app_label": "menus",
            },
        ),
        # ------------------------------------------------------------------
        # Ingredient
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Ingredient",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=4,
                        max_digits=10,
                        help_text="Amount of this ingredient required per serving.",
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        max_length=20,
                        help_text="Unit of measure (e.g. 'g', 'kg', 'ml', 'pieces').",
                    ),
                ),
                (
                    "recipe",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ingredients",
                        to="menus.recipe",
                    ),
                ),
                (
                    "inventory_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="recipe_ingredients",
                        to="inventory.inventoryitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ingredient",
                "verbose_name_plural": "Ingredients",
                "ordering": ["id"],
                "app_label": "menus",
            },
        ),
    ]
