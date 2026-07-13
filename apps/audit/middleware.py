"""
audit/middleware.py

Re-exports AuditLogMiddleware so it can be referenced in settings as:
    "apps.audit.middleware.AuditLogMiddleware"

The implementation lives in apps.audit.decorators to keep the decorator
and middleware context store co-located.
"""

from apps.audit.decorators import AuditLogMiddleware  # noqa: F401
