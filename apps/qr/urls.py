"""
qr/urls.py

URL patterns for the QR code management API (staff-facing).

  GET    /api/v1/branches/{branch_pk}/qr-codes/       — list QR codes for branch
  POST   /api/v1/branches/{branch_pk}/qr-codes/       — generate QR code for a table
  POST   /api/v1/qr-codes/{pk}/regenerate/            — regenerate (invalidates prior code)

All endpoints require IsBranchManager permission.

Requirements: 14.1, 14.3
"""

from django.urls import path

from apps.qr.views import QRCodeListCreateView, QRCodeRegenerateView

urlpatterns = [
    # Branch-nested: list all QR codes for a branch, or generate one for a table
    path(
        "branches/<uuid:branch_pk>/qr-codes/",
        QRCodeListCreateView.as_view(),
        name="branch-qr-code-list",
    ),
    # QR code regeneration (standalone — not nested under branch)
    path(
        "qr-codes/<uuid:pk>/regenerate/",
        QRCodeRegenerateView.as_view(),
        name="qr-code-regenerate",
    ),
]
