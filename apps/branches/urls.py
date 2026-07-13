"""
branches/urls.py

URL patterns for the Branches API.

  GET    /api/v1/branches/                                    — list branches
  POST   /api/v1/branches/                                    — create branch (Tenant_Owner)
  GET    /api/v1/branches/{id}/                               — retrieve branch
  PATCH  /api/v1/branches/{id}/                               — partial update (Tenant_Owner)

  GET    /api/v1/branches/{branch_pk}/tables/                 — list tables in branch
  POST   /api/v1/branches/{branch_pk}/tables/                 — create table (Tenant_Owner)
  GET    /api/v1/branches/{branch_pk}/tables/{id}/            — retrieve table
  PATCH  /api/v1/branches/{branch_pk}/tables/{id}/            — update table (Tenant_Owner)
  DELETE /api/v1/branches/{branch_pk}/tables/{id}/            — delete table (Tenant_Owner)

Requirements: 8.1, 8.3
"""

from django.urls import path

from apps.branches.views import BranchViewSet, RoomViewSet, TableViewSet
from apps.orders.views import OrderViewSet

# ---------------------------------------------------------------------------
# Branch actions
# ---------------------------------------------------------------------------
branch_list = BranchViewSet.as_view({"get": "list", "post": "create"})
branch_detail = BranchViewSet.as_view({"get": "retrieve", "patch": "partial_update"})

# ---------------------------------------------------------------------------
# Table actions (nested under a branch)
# ---------------------------------------------------------------------------
table_list = TableViewSet.as_view({"get": "list", "post": "create"})
table_detail = TableViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"})

# ---------------------------------------------------------------------------
# Room actions (nested under a branch)
# ---------------------------------------------------------------------------
room_list = RoomViewSet.as_view({"get": "list", "post": "create"})
room_detail = RoomViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"})

# ---------------------------------------------------------------------------
# Orders nested under a branch (read-only — for KDS, Receptionist, Manager)
# ---------------------------------------------------------------------------
branch_order_list = OrderViewSet.as_view({"get": "list"})
branch_order_export = OrderViewSet.as_view({"get": "export_csv"})

urlpatterns = [
    # Branches
    path("branches/", branch_list, name="branch-list"),
    path("branches/<uuid:pk>/", branch_detail, name="branch-detail"),
    # Tables nested under branches
    path("branches/<uuid:branch_pk>/tables/", table_list, name="branch-table-list"),
    path("branches/<uuid:branch_pk>/tables/<uuid:pk>/", table_detail, name="branch-table-detail"),
    # Rooms nested under branches
    path("branches/<uuid:branch_pk>/rooms/", room_list, name="branch-room-list"),
    path("branches/<uuid:branch_pk>/rooms/<uuid:pk>/", room_detail, name="branch-room-detail"),
    # Orders nested under a branch
    path("branches/<uuid:branch_pk>/orders/", branch_order_list, name="branch-order-list"),
    path("branches/<uuid:branch_pk>/orders/export-csv/", branch_order_export, name="branch-order-export-csv"),
]
