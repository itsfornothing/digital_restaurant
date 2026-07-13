"""
kitchen/urls.py

URL routing for the Kitchen API.

Registered routes:
    GET /api/v1/menu-items/{id}/recipe/ — MenuItemRecipeView

Requirements: 10.5, 10.6
"""

from django.urls import path

from apps.kitchen.views import MenuItemRecipeView

urlpatterns = [
    path(
        "menu-items/<uuid:pk>/recipe/",
        MenuItemRecipeView.as_view(),
        name="menu-item-recipe",
    ),
]
