"""
menus/views.py

ViewSets for MenuItem, Category, Recipe, and Ingredient management.

Endpoints implemented in Task 10.4:
  GET  /api/v1/branches/{id}/menu-items/   — list menu items (IsBranchManager)
  POST /api/v1/branches/{id}/menu-items/   — create menu item (IsBranchManager)
  PATCH /api/v1/menu-items/{id}/           — partial update (IsBranchManager)
  GET  /api/v1/menu-items/{id}/recipe/     — recipe detail (IsBranchManager, IsKitchenStaff)

Permission matrix (Requirement 4.2):
  MenuItemViewSet:
    create / update / partial_update / archive  → IsBranchManager (or IsTenantOwner)
    list / retrieve                              → IsBranchStaff
  RecipeDetailViewSet:
    retrieve                                     → IsBranchStaff
                                                   (covers Branch_Manager + Kitchen_Staff for KDS)

On save:
  - Cache invalidation: delete Redis key ``menu:branch:{branch_id}`` (Req 9.2)
  - AuditLog entry for price/availability changes (Req 9.4)

Billing enforcement:
  BillingService.check_resource_limit(tenant, 'menu_items') before create (Req 9.5, 2.3)

Requirements: 4.1, 4.2, 4.3, 9.1, 9.2, 9.4, 9.5, 2.3
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.shortcuts import get_object_or_404
from apps.notifications.utils import push_customer_menu_event, push_staff_events
from apps.shared.csv_export import csv_response
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.billing.exceptions import ResourceLimitExceeded as BillingLimitExceeded
from apps.billing.services import BillingService
from apps.branches.models import Branch
from apps.menus.models import Ingredient, MenuItem, Recipe
from apps.menus.serializers import (
    AddIngredientSerializer,
    IngredientSerializer,
    MenuItemListSerializer,
    MenuItemSerializer,
    RecipeDetailSerializer,
    RecipeUpdateSerializer,
)
from shared.exceptions import ResourceLimitExceeded as APIResourceLimitExceeded
from shared.permissions import (
    AuditLogMixin,
    IsBranchManager,
    IsBranchStaff,
    IsTenantOwner,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _invalidate_branch_menu_cache(branch_id: str) -> None:
    """
    Delete the Redis cache entry for a branch's customer menu.

    Cache key: ``menu:branch:{branch_id}``

    This key format matches:
      - CustomerMenuView._menu_cache_key() in apps/qr/customer_views.py
      - _invalidate_menu_cache() in apps/menus/signals.py

    Consistency across all three invalidation paths is required so that
    explicit ViewSet invalidation (create/update/archive) and signal-based
    invalidation both target the same key that CustomerMenuView writes.

    This satisfies Requirement 9.2 — immediate propagation on save.
    """
    cache_key = f"menu:branch:{branch_id}"
    try:
        cache.delete(cache_key)
    except Exception as exc:
        # Cache invalidation failure must never block the save operation.
        logger.warning(
            "Failed to invalidate menu cache for branch %s: %s",
            branch_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

_AUDITABLE_FIELDS = {"price", "status"}


def _snapshot_menu_item(instance: MenuItem) -> dict:
    """Return a JSON-safe snapshot of auditable MenuItem fields."""
    return {
        "id": str(instance.id),
        "name": instance.name,
        "price": str(instance.price),
        "status": instance.status,
        "is_archived": instance.is_archived,
    }


def _write_menu_item_audit(
    request,
    action_code: str,
    resource_id,
    old_value: dict | None,
    new_value: dict | None,
    branch_id=None,
) -> None:
    """
    Write an AuditLog entry for a MenuItem change.

    Silently swallows errors so audit logging never blocks the HTTP response.
    This satisfies Requirement 9.4 — version history via AuditLog.
    """
    try:
        from apps.audit.models import AuditLog

        user = getattr(request, "user", None)
        user_id = str(user.pk) if (user and getattr(user, "is_authenticated", False)) else None
        user_role = getattr(user, "role", "") if user else ""

        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = (
            x_forwarded_for.split(",")[0].strip()
            if x_forwarded_for
            else request.META.get("REMOTE_ADDR", "0.0.0.0")
        )

        AuditLog.objects.create(
            branch_id=branch_id,
            user_id=user_id,
            user_role=user_role,
            ip_address=ip or "0.0.0.0",
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            action=action_code,
            resource_type="MenuItem",
            resource_id=resource_id,
            old_value=old_value,
            new_value=new_value,
            status="success",
            failure_reason="",
        )
    except Exception as exc:
        logger.warning(
            "Failed to write AuditLog for MenuItem action %s: %s",
            action_code,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# MenuItemViewSet
# ---------------------------------------------------------------------------

class MenuItemViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/branches/{branch_pk}/menu-items/        — list menu items
    POST   /api/v1/branches/{branch_pk}/menu-items/        — create menu item
    GET    /api/v1/menu-items/{pk}/                        — retrieve detail
    PATCH  /api/v1/menu-items/{pk}/                        — partial update

    Permission:
      - list / retrieve  → IsBranchStaff (Branch_Manager, Receptionist, Kitchen_Staff)
      - create / update  → IsBranchManager OR IsTenantOwner

    On create/update:
      - BillingService.check_resource_limit(tenant, 'menu_items') before save (Req 9.5)
      - Invalidate Redis cache key ``menu:branch:{branch_id}`` after save (Req 9.2)
      - Write AuditLog entry for price/availability/any attribute changes (Req 9.4)

    Requirements: 9.1, 9.2, 9.4, 9.5, 2.3
    """

    http_method_names = ["get", "post", "patch", "head", "options"]

    # Default permission_classes — overridden per-action via get_permissions()
    # IsBranchManager is the primary write gate; reads allow IsBranchStaff.
    # The permission_classes attribute is inspected by the permission matrix
    # test (shared/tests/test_permission_matrix.py) to verify the default gate.
    permission_classes = [IsBranchManager]

    def get_serializer_class(self):
        if self.action == "list":
            return MenuItemListSerializer
        return MenuItemSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsBranchStaff()]
        # create, partial_update, update — Branch_Manager or Tenant_Owner
        return [_MenuItemWritePermission()]

    def get_queryset(self):
        """
        Scope queryset:
          - For list/create: filter by ``branch_pk`` URL kwarg.
          - For detail (retrieve/partial_update): no branch_pk; filter by
            the user's assigned branch for Branch_Managers.
          - Tenant_Owner / Super_Admin see all items.

        Archived items are hidden by default unless ``?show_archived=1``
        is passed, so staff see a clean active menu (Requirement 9.3).
        """
        from apps.authentication.models import UserRole

        user = self.request.user
        branch_pk = self.kwargs.get("branch_pk")

        qs = MenuItem.objects.select_related("nutrition", "branch").prefetch_related(
            "categories"
        )

        if branch_pk:
            qs = qs.filter(branch_id=branch_pk)
        else:
            # Detail view (no branch_pk in URL): scope to user's branch for
            # branch-scoped roles.
            if hasattr(user, "role") and user.role in (
                UserRole.BRANCH_MANAGER,
                UserRole.RECEPTIONIST,
                UserRole.KITCHEN_STAFF,
            ):
                if user.branch_id:
                    qs = qs.filter(branch_id=user.branch_id)
                else:
                    return qs.none()

        # Hide archived by default unless explicitly requested
        show_archived = self.request.query_params.get("show_archived")
        if show_archived != "1":
            qs = qs.filter(is_archived=False)

        return qs.order_by("name")

    def get_serializer_context(self):
        """Inject branch into serializer context for category validation."""
        ctx = super().get_serializer_context()
        branch_pk = self.kwargs.get("branch_pk")
        if branch_pk:
            try:
                ctx["branch"] = Branch.objects.get(pk=branch_pk)
            except Branch.DoesNotExist:
                pass
        return ctx

    def perform_create(self, serializer):
        """
        1. Verify branch exists.
        2. Enforce billing menu_items limit.
        3. Save the MenuItem attached to the branch.
        4. Invalidate the branch menu cache.
        5. Write AuditLog for the creation.
        """
        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

        # Billing limit check (Req 9.5)
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            try:
                BillingService.check_resource_limit(tenant, "menu_items")
            except BillingLimitExceeded as exc:
                raise APIResourceLimitExceeded(
                    detail=(
                        f"Menu item limit reached: {exc.current_count}/{exc.limit}. "
                        "Upgrade your subscription plan to add more menu items."
                    )
                ) from exc

        instance = serializer.save(branch=branch)

        # Cache invalidation (Req 9.2)
        _invalidate_branch_menu_cache(str(branch.id))

        # Audit log (Req 9.4)
        _write_menu_item_audit(
            request=self.request,
            action_code="MENU_ITEM_CREATE",
            resource_id=instance.id,
            old_value=None,
            new_value=_snapshot_menu_item(instance),
            branch_id=branch.id,
        )

        push_staff_events(str(branch.id), "menu.item_updated", {
            "menu_item_id": str(instance.id),
            "name": instance.name,
            "action": "created",
        })
        push_customer_menu_event(str(branch.id), "menu.item_updated", {
            "menu_item_id": str(instance.id),
            "name": instance.name,
            "action": "created",
        })

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH — partial update with audit logging for price/availability changes.

        Captures old_value BEFORE the update and new_value AFTER, then writes
        an AuditLog entry if price or status changed (Requirement 9.4).
        """
        instance = self.get_object()
        old_snapshot = _snapshot_menu_item(instance)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_instance = serializer.save()

        new_snapshot = _snapshot_menu_item(updated_instance)

        # Cache invalidation (Req 9.2)
        _invalidate_branch_menu_cache(str(updated_instance.branch_id))

        # Determine the most specific audit action code
        changed_fields = {
            k for k in new_snapshot if new_snapshot[k] != old_snapshot.get(k)
        }

        if "price" in changed_fields:
            action_code = "MENU_ITEM_PRICE_CHANGE"
        elif "status" in changed_fields or "is_archived" in changed_fields:
            action_code = "MENU_ITEM_STATUS_CHANGE"
        else:
            action_code = "MENU_ITEM_UPDATE"

        _write_menu_item_audit(
            request=request,
            action_code=action_code,
            resource_id=updated_instance.id,
            old_value=old_snapshot,
            new_value=new_snapshot,
            branch_id=updated_instance.branch_id,
        )

        push_staff_events(str(updated_instance.branch_id), "menu.item_updated", {
            "menu_item_id": str(updated_instance.id),
            "name": updated_instance.name,
            "action": "updated",
        })
        push_customer_menu_event(str(updated_instance.branch_id), "menu.item_updated", {
            "menu_item_id": str(updated_instance.id),
            "name": updated_instance.name,
            "action": "updated",
        })

        return Response(
            self.get_serializer(updated_instance).data,
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Archive action — Task 10.7 stub, wired here for completeness
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None, **kwargs):
        """
        POST /api/v1/menu-items/{id}/archive/

        Sets is_archived=True on the MenuItem.  Archived items are excluded
        from the customer-facing menu but historical order references are
        preserved (Requirement 9.3).
        """
        instance = self.get_object()
        old_snapshot = _snapshot_menu_item(instance)

        instance.is_archived = True
        instance.status = "archived"
        instance.save(update_fields=["is_archived", "status", "updated_at"])

        # Cache invalidation
        _invalidate_branch_menu_cache(str(instance.branch_id))

        # Audit log
        _write_menu_item_audit(
            request=request,
            action_code="MENU_ITEM_ARCHIVE",
            resource_id=instance.id,
            old_value=old_snapshot,
            new_value=_snapshot_menu_item(instance),
            branch_id=instance.branch_id,
        )

        push_staff_events(str(instance.branch_id), "menu.item_updated", {
            "menu_item_id": str(instance.id),
            "name": instance.name,
            "action": "archived",
        })
        push_customer_menu_event(str(instance.branch_id), "menu.item_updated", {
            "menu_item_id": str(instance.id),
            "name": instance.name,
            "action": "archived",
        })

        return Response(
            self.get_serializer(instance).data,
            status=status.HTTP_200_OK,
        )

    # -- CSV export --------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request, branch_pk=None, **kwargs):
        qs = self.get_queryset().prefetch_related("categories")
        rows = []
        for item in qs:
            cats = ", ".join(c.name for c in item.categories.all())
            rows.append({
                "Name": item.name,
                "Description": item.description or "",
                "Price": str(item.price),
                "Status": item.status,
                "Categories": cats,
                "Dietary Tags": ", ".join(item.dietary_tags or []),
                "Prep Time (min)": str(item.prep_time_minutes or ""),
                "Archived": "Yes" if item.is_archived else "No",
            })
        return csv_response(rows, f"menu_items_{branch_pk}.csv")


# ---------------------------------------------------------------------------
# RecipeDetailViewSet — GET /api/v1/menu-items/{menu_item_pk}/recipe/
# ---------------------------------------------------------------------------

class RecipeDetailViewSet(
    AuditLogMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/menu-items/{menu_item_pk}/recipe/                    — retrieve
    PATCH  /api/v1/menu-items/{menu_item_pk}/recipe/                    — partial update
    POST   /api/v1/menu-items/{menu_item_pk}/recipe/ingredients/        — add ingredient
    DELETE /api/v1/menu-items/{menu_item_pk}/recipe/ingredients/{pk}/   — remove ingredient

    Manage a MenuItem's recipe and its ingredient list.
    Permission: IsBranchStaff for retrieve; IsBranchManager for write operations.
    """

    serializer_class = RecipeDetailSerializer
    permission_classes = [IsBranchManager]

    def get_permissions(self):
        """
        Allow Kitchen_Staff (and Receptionist) to read recipes for KDS use
        (Requirement 10.5). IsBranchManager is the class-level default, but
        retrieve is opened to all branch staff.
        """
        if self.action == "retrieve":
            return [IsBranchStaff()]
        return [IsBranchManager()]

    def _scoped_recipe_qs(self):
        """Return a Recipe queryset scoped to the user's branch."""
        from apps.authentication.models import UserRole

        user = self.request.user
        qs = Recipe.objects.select_related("menu_item").prefetch_related(
            "ingredients__inventory_item"
        )

        if hasattr(user, "role") and user.role in (
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
            UserRole.KITCHEN_STAFF,
        ):
            if user.branch_id:
                qs = qs.filter(menu_item__branch_id=user.branch_id)
            else:
                qs = qs.none()
        return qs

    def get_object(self):
        menu_item_pk = self.kwargs.get("menu_item_pk")
        qs = self._scoped_recipe_qs()
        try:
            return qs.get(menu_item_id=menu_item_pk)
        except Recipe.DoesNotExist:
            raise NotFound("Recipe not found for this menu item.")

    def create(self, request, menu_item_pk=None):
        """
        POST /api/v1/menu-items/{menu_item_pk}/recipe/

        Create a new recipe for a menu item. Returns 409 if one already exists.
        """
        from apps.menus.models import MenuItem as _MenuItem
        from apps.authentication.models import UserRole as _UserRole

        existing = self._scoped_recipe_qs().filter(menu_item_id=menu_item_pk).first()
        if existing:
            return Response(
                {"detail": "A recipe already exists for this menu item. Use PATCH to update."},
                status=409,
            )

        elevated = (_UserRole.SUPER_ADMIN, _UserRole.TENANT_OWNER)
        user = request.user
        menu_qs = _MenuItem.objects.all()
        if user and hasattr(user, "role") and user.role not in elevated:
            if user.branch_id:
                menu_qs = menu_qs.filter(branch_id=user.branch_id)
            else:
                menu_qs = menu_qs.none()
        menu_item = get_object_or_404(menu_qs, pk=menu_item_pk)
        serializer = RecipeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipe = serializer.save(menu_item=menu_item)
        push_staff_events(str(menu_item.branch_id), "menu.item_updated", {
            "menu_item_id": str(menu_item.id), "action": "recipe_created",
        })
        return Response(RecipeDetailSerializer(recipe).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH /api/v1/menu-items/{menu_item_pk}/recipe/

        Update recipe method and/or cook_time_minutes.
        """
        instance = self.get_object()
        serializer = RecipeUpdateSerializer(
            instance, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        push_staff_events(str(instance.menu_item.branch_id), "menu.item_updated", {
            "menu_item_id": str(instance.menu_item_id), "action": "recipe_updated",
        })
        return Response(RecipeDetailSerializer(instance).data)

    def add_ingredient(self, request, menu_item_pk=None):
        """
        POST /api/v1/menu-items/{menu_item_pk}/recipe/ingredients/

        Add an ingredient to the recipe.  Creates the Recipe first if it
        does not already exist.
        """
        # Resolve the MenuItem and verify branch scope
        menu_item = get_object_or_404(MenuItem, pk=menu_item_pk)
        self._check_item_branch_scope(menu_item)

        # Get-or-create the Recipe
        recipe, _ = Recipe.objects.get_or_create(
            menu_item=menu_item,
            defaults={"method": "", "cook_time_minutes": 0},
        )

        serializer = AddIngredientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ingredient = Ingredient.objects.create(
            recipe=recipe,
            inventory_item_id=serializer.validated_data["inventory_item_id"],
            quantity=serializer.validated_data["quantity"],
            unit=serializer.validated_data["unit"],
        )
        push_staff_events(str(menu_item.branch_id), "menu.item_updated", {
            "menu_item_id": str(menu_item.id), "action": "ingredient_added",
        })
        return Response(
            IngredientSerializer(ingredient).data,
            status=status.HTTP_201_CREATED,
        )

    def remove_ingredient(self, request, menu_item_pk=None, pk=None):
        """
        DELETE /api/v1/menu-items/{menu_item_pk}/recipe/ingredients/{pk}/

        Remove a single ingredient from the recipe.
        """
        recipe = self.get_object()
        ingredient = get_object_or_404(Ingredient, pk=pk, recipe=recipe)
        menu_item = recipe.menu_item
        ingredient.delete()
        push_staff_events(str(menu_item.branch_id), "menu.item_updated", {
            "menu_item_id": str(menu_item.id), "action": "ingredient_removed",
        })
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_item_branch_scope(self, menu_item: MenuItem) -> None:
        """
        Verify the menu_item belongs to the requesting user's branch.
        Raises NotFound if the user has no access.
        """
        from apps.authentication.models import UserRole

        user = self.request.user
        if hasattr(user, "role") and user.role in (
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
            UserRole.KITCHEN_STAFF,
        ):
            if user.branch_id and str(menu_item.branch_id) != str(user.branch_id):
                raise NotFound("Menu item not found.")


# ---------------------------------------------------------------------------
# CategoryViewSet (stub — full wiring can be expanded in later tasks)
# ---------------------------------------------------------------------------

class CategoryViewSet(
    AuditLogMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET  /api/v1/branches/{branch_pk}/categories/   — list categories
    POST /api/v1/branches/{branch_pk}/categories/   — create category

    Permission: Branch_Manager (write), IsBranchStaff (read)
    """

    from apps.menus.serializers import CategorySerializer as _CategorySerializer
    serializer_class = _CategorySerializer
    permission_classes = [IsBranchManager]

    def get_permissions(self):
        if self.action == "list":
            return [IsBranchStaff()]
        return [IsBranchManager()]

    def get_queryset(self):
        branch_pk = self.kwargs.get("branch_pk")
        from apps.menus.models import Category
        qs = Category.objects.filter(branch_id=branch_pk).order_by("name")
        return qs

    def perform_create(self, serializer):
        branch_pk = self.kwargs.get("branch_pk")
        try:
            branch = Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")
        instance = serializer.save(branch=branch)
        push_staff_events(str(branch.id), "menu.item_updated", {
            "category_id": str(instance.id), "name": instance.name, "action": "created",
        })


# ---------------------------------------------------------------------------
# Internal composite permission
# ---------------------------------------------------------------------------

class _MenuItemWritePermission(IsBranchManager):
    """
    Grants write access to Branch_Manager AND Tenant_Owner.

    Tenant_Owner needs write access for management dashboards even if they
    are not the direct Branch_Manager (Requirement 4.2).
    """

    message = "You must be a Branch Manager or Tenant Owner to modify menu items."

    def has_permission(self, request, view) -> bool:
        if super().has_permission(request, view):
            return True
        return IsTenantOwner().has_permission(request, view)


# ---------------------------------------------------------------------------
# RecipeViewSet — public alias for RecipeDetailViewSet
# ---------------------------------------------------------------------------
#
# The permission matrix tests (shared/tests/test_permission_matrix.py) import
# ``RecipeViewSet`` by name.  ``RecipeDetailViewSet`` is the real implementation;
# this alias makes both names available for import.
#
RecipeViewSet = RecipeDetailViewSet
