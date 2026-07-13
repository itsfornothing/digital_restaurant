"""
inventory/urls.py

URL patterns for the Inventory API.

  GET    /api/v1/branches/{branch_pk}/inventory/              — list items (IsBranchStaff)
  POST   /api/v1/branches/{branch_pk}/inventory/              — create item (IsBranchManager)
  GET    /api/v1/inventory/{pk}/                              — retrieve item (IsBranchStaff)
  PATCH  /api/v1/inventory/{pk}/                              — partial update (IsBranchManager)
  GET    /api/v1/branches/{branch_pk}/inventory/report/       — report (IsBranchManager)
  GET    /api/v1/branches/{branch_pk}/suppliers/              — list suppliers (IsBranchManager)
  POST   /api/v1/branches/{branch_pk}/suppliers/              — create supplier (IsBranchManager)

Requirements: 11.1, 4.2
"""

from django.urls import path

from apps.inventory.views import InventoryReportView, InventoryViewSet, SupplierViewSet

# ---------------------------------------------------------------------------
# InventoryViewSet actions
# ---------------------------------------------------------------------------
inventory_list = InventoryViewSet.as_view({"get": "list", "post": "create"})
inventory_detail = InventoryViewSet.as_view({"get": "retrieve", "patch": "partial_update"})
inventory_export = InventoryViewSet.as_view({"get": "export_csv"})

# ---------------------------------------------------------------------------
# SupplierViewSet actions
# ---------------------------------------------------------------------------
supplier_list = SupplierViewSet.as_view({"get": "list", "post": "create"})

urlpatterns = [
    # Inventory items nested under a branch (list + create)
    path(
        "branches/<uuid:branch_pk>/inventory/",
        inventory_list,
        name="branch-inventory-list",
    ),
    # Inventory CSV export
    path(
        "branches/<uuid:branch_pk>/inventory/export-csv/",
        inventory_export,
        name="branch-inventory-export-csv",
    ),
    # Inventory report for a branch
    path(
        "branches/<uuid:branch_pk>/inventory/report/",
        InventoryReportView.as_view(),
        name="branch-inventory-report",
    ),
    # Inventory item detail and partial update (not nested)
    path(
        "inventory/<uuid:pk>/",
        inventory_detail,
        name="inventory-item-detail",
    ),
    # Suppliers nested under a branch
    path(
        "branches/<uuid:branch_pk>/suppliers/",
        supplier_list,
        name="branch-supplier-list",
    ),
]
