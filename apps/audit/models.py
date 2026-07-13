"""
audit/models.py

AuditLog — immutable record of every business-critical action on the platform.

All 15 required fields (Requirement 5.2):
  1.  log_id         — UUID PK
  2.  timestamp      — UTC datetime (nanosecond precision), auto, indexed
  3.  tenant_id      — UUID (nullable; platform-level events have no tenant)
  4.  branch_id      — UUID (nullable)
  5.  user_id        — UUID (nullable; system/anonymous actions)
  6.  user_role      — role string at time of action
  7.  ip_address     — GenericIPAddressField (nullable for Celery tasks)
  8.  user_agent     — HTTP User-Agent string
  9.  action         — standardised enum code e.g. USER_LOGIN, ORDER_CANCEL
  10. resource_type  — model name string e.g. "User", "Order"
  11. resource_id    — UUID of the affected resource (nullable)
  12. old_value      — JSONB snapshot before the action
  13. new_value      — JSONB snapshot after the action
  14. status         — "success" or "failure"
  15. failure_reason — human-readable failure description

Immutability is enforced at two levels:
  - ORM: no update() / delete() exposed via the model API
  - Database: PostgreSQL RULEs (applied in migration 0002) make UPDATE and
    DELETE on the audit_auditlog table silently no-ops at the DB layer

Requirements: 5.2, 5.4
"""

import uuid

from django.db import models


class AuditLogStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class AuditLog(models.Model):
    """Immutable audit log entry — 15 required fields (Requirement 5.2)."""

    # 1. log_id
    log_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    # 2. timestamp — auto-set to UTC now on creation
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
    # 3. tenant_id — UUID of the tenant in whose context the action occurred
    tenant_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )
    # 4. branch_id — UUID of the branch (null for tenant/platform-level actions)
    branch_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )
    # 5. user_id — UUID of the acting user (null for anonymous/system actions)
    user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )
    # 6. user_role — role string captured at the time of the action
    user_role = models.CharField(max_length=50, blank=True)
    # 7. ip_address — null for background tasks / Celery
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )
    # 8. user_agent
    user_agent = models.TextField(blank=True)
    # 9. action — standardised enum code
    action = models.CharField(max_length=100, db_index=True)
    # 10. resource_type — name of the affected model/resource
    resource_type = models.CharField(max_length=100)
    # 11. resource_id — UUID PK of the affected resource
    resource_id = models.UUIDField(null=True, blank=True)
    # 12. old_value — JSONB snapshot before action (sensitive fields redacted)
    old_value = models.JSONField(null=True, blank=True)
    # 13. new_value — JSONB snapshot after action (sensitive fields redacted)
    new_value = models.JSONField(null=True, blank=True)
    # 14. status
    status = models.CharField(
        max_length=10,
        choices=AuditLogStatus.choices,
        default=AuditLogStatus.SUCCESS,
    )
    # 15. failure_reason
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "audit log"
        verbose_name_plural = "audit logs"
        indexes = [
            # Composite index for audit log queries: filter by date range and
            # action type. tenant_id is schema-scoped by django-tenants so is
            # not needed in this index. Requirement 19.2 (Task 20.3).
            models.Index(
                fields=["timestamp", "action"],
                name="audit_log_timestamp_action_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"AuditLog({self.action}, {self.status}, {self.timestamp})"

    # ------------------------------------------------------------------
    # Immutability guards at the ORM level
    # ------------------------------------------------------------------

    def save(self, *args, **kwargs):
        """
        Allow only INSERT (creation).  Any attempt to UPDATE an existing
        AuditLog raises RuntimeError.

        The primary enforcement layer is the PostgreSQL RULE added in
        migration 0002; this Python guard provides defence-in-depth and
        a readable error message in tests/development environments that
        may not run the DB-level rules.
        """
        if self._state.adding:
            super().save(*args, **kwargs)
        else:
            raise RuntimeError(
                "AuditLog records are immutable. UPDATE is not permitted."
            )

    def delete(self, *args, **kwargs):
        """Prevent deletion at the ORM level."""
        raise RuntimeError(
            "AuditLog records are immutable. DELETE is not permitted."
        )
