"""
audit/serializers.py

Read-only serializer for AuditLog entries.

All fields are serialised; no write operations are exposed through the API
(immutability is enforced at both the ORM and DB layer).

Requirements: 5.5, 5.6, 5.7
"""

from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    """
    Read-only serialiser for AuditLog.

    All 15 required fields are included.  No create/update/delete is allowed.
    """

    class Meta:
        model = AuditLog
        fields = [
            "log_id",
            "timestamp",
            "tenant_id",
            "branch_id",
            "user_id",
            "user_role",
            "ip_address",
            "user_agent",
            "action",
            "resource_type",
            "resource_id",
            "old_value",
            "new_value",
            "status",
            "failure_reason",
        ]
        read_only_fields = fields
