"""
audit/urls.py

Registers the AuditLogViewSet router.

Routes:
    GET /api/v1/audit-logs/        — list audit log entries (paginated, filtered)
    GET /api/v1/audit-logs/{id}/   — retrieve a single audit log entry

No write routes are registered (AuditLogs are immutable).

Requirements: 5.5, 5.6, 5.7
"""

from rest_framework.routers import DefaultRouter

from apps.audit.views import AuditLogViewSet

router = DefaultRouter()
router.register(r"audit-logs", AuditLogViewSet, basename="auditlog")

urlpatterns = router.urls
