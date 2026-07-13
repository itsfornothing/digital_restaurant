"""
tenants/models.py — Tenant and Domain models using django-tenants mixins.

The Tenant model lives in the public schema (SHARED_APPS).
The Domain model provides hostname → tenant resolution.

Note: subscription FK to billing.TenantSubscription is deferred to Task 8
when the billing models are created.
"""

import uuid

from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Tenant(TenantMixin):
    """
    Represents a restaurant business operating on the platform.

    TenantMixin provides:
      - schema_name (CharField, unique) — the PostgreSQL schema name
      - auto_create_schema (bool, default True) — create schema on save()
      - auto_drop_schema (bool, default False) — drop schema on delete()
    """

    # Human-readable restaurant business name
    name = models.CharField(max_length=200)

    # URL-safe unique identifier used in subdomain routing and schema naming
    slug = models.SlugField(unique=True)

    # Only active tenants are allowed to receive traffic (enforced in middleware)
    is_active = models.BooleanField(default=False)

    # When this tenant record was created on the platform
    created_at = models.DateTimeField(auto_now_add=True)

    # subscription FK added in Task 8 when billing.TenantSubscription is implemented:
    # subscription = models.ForeignKey(
    #     'billing.TenantSubscription',
    #     null=True,
    #     blank=True,
    #     on_delete=models.SET_NULL,
    #     related_name='tenant',
    # )

    # TenantMixin requires auto_create_schema = True (the default) so that
    # calling tenant.save() triggers automatic PostgreSQL schema creation.
    auto_create_schema = True

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    def __str__(self) -> str:
        return f"{self.name} ({self.schema_name})"


class Domain(DomainMixin):
    """
    Maps a hostname to a Tenant.

    DomainMixin provides:
      - domain (CharField, unique) — the hostname (e.g. "acme.platform.com")
      - tenant (ForeignKey → Tenant)
      - is_primary (BooleanField) — whether this is the tenant's primary domain
    """

    class Meta:
        verbose_name = "Domain"
        verbose_name_plural = "Domains"

    def __str__(self) -> str:
        return self.domain


class PlatformAuditLogStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class PlatformAuditLog(models.Model):
    """
    Immutable audit log for platform-level events (public schema).

    Mirrors the 15-field structure of the tenant-scoped AuditLog but lives
    in the public schema so that events like TENANT_SUSPEND can be recorded
    even when the tenant schema is inaccessible or being deactivated.

    Fields (Requirement 5.2):
        1.  log_id         — UUID PK
        2.  timestamp      — auto UTC datetime, indexed
        3.  tenant_id      — nullable UUID
        4.  branch_id      — nullable UUID
        5.  user_id        — nullable UUID
        6.  user_role      — role string at time of action
        7.  ip_address     — nullable GenericIPAddressField
        8.  user_agent     — text
        9.  action         — standardised enum code
        10. resource_type  — model name string
        11. resource_id    — nullable UUID
        12. old_value      — nullable JSONField
        13. new_value      — nullable JSONField
        14. status         — "success" | "failure"
        15. failure_reason — text
    """

    log_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    branch_id = models.UUIDField(null=True, blank=True, db_index=True)
    user_id = models.UUIDField(null=True, blank=True, db_index=True)
    user_role = models.CharField(max_length=50, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    action = models.CharField(max_length=100, db_index=True)
    resource_type = models.CharField(max_length=100)
    resource_id = models.UUIDField(null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=10,
        choices=PlatformAuditLogStatus.choices,
        default=PlatformAuditLogStatus.SUCCESS,
    )
    failure_reason = models.TextField(blank=True)

    class Meta:
        app_label = "tenants"
        ordering = ["-timestamp"]
        verbose_name = "platform audit log"
        verbose_name_plural = "platform audit logs"
        indexes = [
            models.Index(
                fields=["timestamp", "action"],
                name="plat_audit_log_ts_action_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"PlatformAuditLog({self.action}, {self.status}, {self.timestamp})"

    def save(self, *args, **kwargs):
        if self._state.adding:
            super().save(*args, **kwargs)
        else:
            raise RuntimeError(
                "PlatformAuditLog records are immutable. UPDATE is not permitted."
            )

    def delete(self, *args, **kwargs):
        raise RuntimeError(
            "PlatformAuditLog records are immutable. DELETE is not permitted."
        )
