"""
audit/views.py

AuditLogViewSet — read-only, scope-enforcing ViewSet for AuditLog entries.

Permission matrix (Requirement 4.2):
  Super_Admin    → read all AuditLogs platform-wide (no filter)
  Tenant_Owner   → read AuditLogs scoped to own tenant schema
  Branch_Manager → read AuditLogs scoped to their branch

No create / update / destroy actions are exposed — AuditLogs are immutable
(Requirement 5.4).

Query parameters:
  ?from=<ISO datetime>      filter timestamp >= from
  ?to=<ISO datetime>        filter timestamp <= to
  ?action=<code>            exact match on action field
  ?user_id=<uuid>           exact match on user_id field
  ?resource_type=<str>      exact match on resource_type field

Pagination: cursor-based (shared.pagination.CursorPagination) — no OFFSET.

Requirements: 5.5, 5.6, 5.7
"""

import logging

from rest_framework import viewsets
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from shared.pagination import CursorPagination
from shared.permissions import (
    AuditLogMixin,
    IsAuditLogReader,
)
from apps.authentication.models import UserRole

logger = logging.getLogger(__name__)


class AuditLogCursorPagination(CursorPagination):
    """Audit-log-specific cursor pagination ordered by descending timestamp."""

    ordering = "-timestamp"


class AuditLogViewSet(AuditLogMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for AuditLog entries.

    Allowed roles:
        Super_Admin    — read all logs platform-wide (no tenant/branch filter)
        Tenant_Owner   — read logs scoped to own tenant schema
        Branch_Manager — read logs scoped to own branch

    No write operations are permitted (immutability enforced at ORM + DB).

    Requirements: 4.2, 5.4, 5.5, 5.6, 5.7
    """

    serializer_class = AuditLogSerializer
    permission_classes = [IsAuditLogReader]
    pagination_class = AuditLogCursorPagination

    def get_queryset(self):
        """
        Return an AuditLog queryset scoped by the requesting user's role.

        Scope rules:
          - Super_Admin    → no additional filter (all entries in current schema)
          - Tenant_Owner   → no additional filter (already in tenant schema via
                             django-tenants; all entries here belong to this tenant)
          - Branch_Manager → filter by branch_id == user.branch_id
        """
        user = self.request.user
        qs = AuditLog.objects.all()

        if user.role == UserRole.BRANCH_MANAGER:
            qs = qs.filter(branch_id=user.branch_id)

        # Apply query parameter filters
        qs = self._apply_filters(qs)

        return qs

    def _apply_filters(self, qs):
        """Apply query string filters to the queryset."""
        params = self.request.query_params

        from_dt = params.get("from")
        to_dt = params.get("to")
        action = params.get("action")
        user_id = params.get("user_id")
        resource_type = params.get("resource_type")

        if from_dt:
            try:
                from django.utils.dateparse import parse_datetime
                from django.utils.timezone import make_aware
                import django.utils.timezone as tz

                dt = parse_datetime(from_dt)
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = make_aware(dt)
                    qs = qs.filter(timestamp__gte=dt)
            except Exception as exc:
                logger.debug("Invalid 'from' filter value: %s — %s", from_dt, exc)

        if to_dt:
            try:
                from django.utils.dateparse import parse_datetime
                from django.utils.timezone import make_aware

                dt = parse_datetime(to_dt)
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = make_aware(dt)
                    qs = qs.filter(timestamp__lte=dt)
            except Exception as exc:
                logger.debug("Invalid 'to' filter value: %s — %s", to_dt, exc)

        if action:
            qs = qs.filter(action=action)

        if user_id:
            qs = qs.filter(user_id=user_id)

        if resource_type:
            qs = qs.filter(resource_type=resource_type)

        return qs
