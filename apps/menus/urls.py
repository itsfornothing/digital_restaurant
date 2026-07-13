"""
menus/urls.py

URL patterns for the Menu Items API.

  GET    /api/v1/branches/{branch_pk}/menu-items/            — list menu items (IsBranchStaff)
  POST   /api/v1/branches/{branch_pk}/menu-items/            — create menu item (IsBranchManager)
  GET    /api/v1/menu-items/{pk}/                            — retrieve detail (IsBranchStaff)
  PATCH  /api/v1/menu-items/{pk}/                            — partial update (IsBranchManager)
  POST   /api/v1/menu-items/{pk}/archive/                    — archive item (IsBranchManager)
  GET    /api/v1/menu-items/{menu_item_pk}/recipe/           — get recipe (IsBranchStaff)

  GET    /api/v1/branches/{branch_pk}/categories/            — list categories (IsBranchStaff)
  POST   /api/v1/branches/{branch_pk}/categories/            — create category (IsBranchManager)

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 10.5
"""

from django.urls import path

from apps.menus.views import CategoryViewSet, MenuItemViewSet, RecipeDetailViewSet

# ---------------------------------------------------------------------------
# MenuItemViewSet actions
# ---------------------------------------------------------------------------
menu_item_list = MenuItemViewSet.as_view({"get": "list", "post": "create"})
menu_item_detail = MenuItemViewSet.as_view({"get": "retrieve", "patch": "partial_update"})
menu_item_archive = MenuItemViewSet.as_view({"post": "archive"})
menu_item_export = MenuItemViewSet.as_view({"get": "export_csv"})

# ---------------------------------------------------------------------------
# RecipeDetailViewSet actions
# ---------------------------------------------------------------------------
recipe_detail = RecipeDetailViewSet.as_view({
    "get": "retrieve",
    "post": "create",
    "patch": "partial_update",
})
recipe_add_ingredient = RecipeDetailViewSet.as_view({
    "post": "add_ingredient",
})
recipe_remove_ingredient = RecipeDetailViewSet.as_view({
    "delete": "remove_ingredient",
})

# ---------------------------------------------------------------------------
# CategoryViewSet actions
# ---------------------------------------------------------------------------
category_list = CategoryViewSet.as_view({"get": "list", "post": "create"})

urlpatterns = [
    # Menu items nested under a branch (list + create)
    path(
        "branches/<uuid:branch_pk>/menu-items/",
        menu_item_list,
        name="branch-menu-item-list",
    ),
    # Menu items CSV export
    path(
        "branches/<uuid:branch_pk>/menu-items/export-csv/",
        menu_item_export,
        name="branch-menu-item-export-csv",
    ),
    # Menu item detail and partial update (not nested — uses item's own PK)
    path(
        "menu-items/<uuid:pk>/",
        menu_item_detail,
        name="menu-item-detail",
    ),
    # Archive action
    path(
        "menu-items/<uuid:pk>/archive/",
        menu_item_archive,
        name="menu-item-archive",
    ),
    # Recipe detail nested under a menu item
    path(
        "menu-items/<uuid:menu_item_pk>/recipe/",
        recipe_detail,
        name="menu-item-recipe",
    ),
    # Add ingredient to recipe
    path(
        "menu-items/<uuid:menu_item_pk>/recipe/ingredients/",
        recipe_add_ingredient,
        name="menu-item-recipe-add-ingredient",
    ),
    # Remove ingredient from recipe
    path(
        "menu-items/<uuid:menu_item_pk>/recipe/ingredients/<uuid:pk>/",
        recipe_remove_ingredient,
        name="menu-item-recipe-remove-ingredient",
    ),
    # Categories nested under a branch
    path(
        "branches/<uuid:branch_pk>/categories/",
        category_list,
        name="branch-category-list",
    ),
]
