"""
kitchen/views.py

KDS (Kitchen Display System) views.

Endpoints:
    GET /api/v1/menu-items/{id}/recipe/
        Returns the recipe method, cook time, and ingredient list for a MenuItem.
        Used by the KitchenDisplay React component's recipe viewer modal.
        Permission: IsKitchenStaff | IsBranchManager | IsReceptionist

Requirements: 10.5, 10.6
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import UserRole
from apps.menus.models import MenuItem
from shared.permissions import _get_user


# ---------------------------------------------------------------------------
# Composite permission: Kitchen staff, Branch Manager, or Receptionist
# ---------------------------------------------------------------------------

class _RecipeReaderPermission(BasePermission):
    """
    Allows recipe access for Kitchen_Staff, Branch_Manager, and Receptionist.

    Kitchen_Staff need the recipe to know how to prepare each dish (Req 10.5).
    Branch_Manager and Receptionist may also view recipes for reference.
    """

    message = "You must be Kitchen Staff, a Branch Manager, or a Receptionist to view recipes."

    def has_permission(self, request, view) -> bool:
        user = _get_user(request)
        if user is None:
            return False
        return user.is_active and user.role in (
            UserRole.KITCHEN_STAFF,
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
        )


# ---------------------------------------------------------------------------
# Recipe detail view
# ---------------------------------------------------------------------------

class MenuItemRecipeView(APIView):
    """
    GET /api/v1/menu-items/{id}/recipe/

    Returns recipe details for the specified MenuItem, including:
      - method       : step-by-step preparation instructions
      - cook_time_minutes: total cook time
      - ingredients  : list of {name, quantity, unit} from recipe Ingredients

    Returns 404 if the MenuItem has no Recipe attached.

    Requirements: 10.5, 10.6
    """

    permission_classes = [_RecipeReaderPermission]

    def get(self, request, pk):
        menu_item = get_object_or_404(MenuItem, pk=pk)

        # Fetch the OneToOne recipe; 404 if not configured
        recipe = getattr(menu_item, "recipe", None)
        if recipe is None:
            return Response(
                {"detail": "No recipe configured for this menu item."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Build ingredients list from related Ingredient records
        ingredients = [
            {
                "name": ingredient.inventory_item.name,
                "quantity": str(ingredient.quantity),
                "unit": ingredient.unit,
            }
            for ingredient in recipe.ingredients.select_related("inventory_item").all()
        ]

        return Response(
            {
                "menu_item_id": str(menu_item.id),
                "menu_item_name": menu_item.name,
                "method": recipe.method,
                "cook_time_minutes": recipe.cook_time_minutes,
                "ingredients": ingredients,
            },
            status=status.HTTP_200_OK,
        )
