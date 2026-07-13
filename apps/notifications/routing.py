"""
Django Channels WebSocket URL routing for real-time notifications.

Channel groups defined in the design:
  - branch_{branch_id}_kitchen   → KDS (new_order, order_cancelled)
  - branch_{branch_id}_reception → Reception dashboard (new_order, order_status_changed)
  - branch_{branch_id}_manager   → Branch manager (inventory alerts, report_ready)
  - order_{order_id}_customer    → Customer order tracker (order_status_changed)
  - branch_{branch_id}_inventory → Inventory alerts (low_stock, expiry_warning, out_of_stock)

URL patterns (Task 17.3):
  ws/kitchen/              → KitchenConsumer
  ws/reception/            → ReceptionConsumer
  ws/manager/              → ManagerConsumer
  ws/order/<order_id>/     → CustomerOrderConsumer
  ws/inventory/            → InventoryConsumer

Requirements: 17.1, 17.2, 17.3, 17.4
"""

from django.urls import re_path

from apps.notifications.consumers import (
    CustomerMenuConsumer,
    CustomerOrderConsumer,
    InventoryConsumer,
    KitchenConsumer,
    ManagerConsumer,
    ReceptionConsumer,
)

websocket_urlpatterns = [
    re_path(r"^ws/kitchen/$", KitchenConsumer.as_asgi()),
    # KDS React frontend connects with branch ID in the URL (legacy)
    re_path(
        r"^ws/branch/(?P<branch_id>[0-9a-f-]+)/kitchen/$",
        KitchenConsumer.as_asgi(),
    ),
    re_path(r"^ws/reception/$", ReceptionConsumer.as_asgi()),
    # Legacy: branch-prefixed route for React frontend compatibility
    re_path(
        r"^ws/branch/(?P<branch_id>[0-9a-f-]+)/reception/$",
        ReceptionConsumer.as_asgi(),
    ),
    re_path(r"^ws/manager/$", ManagerConsumer.as_asgi()),
    re_path(
        r"^ws/order/(?P<order_id>[0-9a-f-]+)/$",
        CustomerOrderConsumer.as_asgi(),
    ),
    re_path(r"^ws/inventory/$", InventoryConsumer.as_asgi()),
    re_path(r"^ws/customer/menu/$", CustomerMenuConsumer.as_asgi()),
]
