"""
Migration 0003 — Add composite index on AuditLog(timestamp, action).

This index optimises audit log queries that filter by date range and action
type — the most common filtering pattern for Super_Admin, Tenant_Owner, and
Branch_Manager audit log views.

tenant_id is already schema-scoped by django-tenants (each tenant schema
has its own audit_auditlog table), so a tenant_id column in the index is
not needed.

Index name: audit_log_timestamp_action_idx

Requirements: 19.2 (Task 20.3)
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_auditlog"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["timestamp", "action"],
                name="audit_log_timestamp_action_idx",
            ),
        ),
    ]
