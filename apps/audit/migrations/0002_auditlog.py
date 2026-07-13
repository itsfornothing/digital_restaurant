"""
Migration 0002: Create AuditLog table and add PostgreSQL immutability rules.

The PostgreSQL RULEs created here prevent any UPDATE or DELETE statement
(including raw SQL) on the audit_auditlog table, satisfying Requirement 5.4.

Dependencies: apps.audit 0001_initial (empty placeholder migration).

Requirements: 5.2, 5.4
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def _is_postgresql(schema_editor):
    """Return True if the current DB backend is PostgreSQL."""
    return schema_editor.connection.vendor == "postgresql"


def _add_immutability_rules(apps, schema_editor):
    """
    Create PostgreSQL RULEs that prevent UPDATE/DELETE on audit_auditlog.
    No-op on non-PostgreSQL backends (e.g. SQLite in unit tests).
    """
    if not _is_postgresql(schema_editor):
        return
    schema_editor.execute(
        "CREATE OR REPLACE RULE protect_audit_log_update "
        "AS ON UPDATE TO audit_auditlog DO INSTEAD NOTHING"
    )
    schema_editor.execute(
        "CREATE OR REPLACE RULE protect_audit_log_delete "
        "AS ON DELETE TO audit_auditlog DO INSTEAD NOTHING"
    )


def _remove_immutability_rules(apps, schema_editor):
    """Drop the immutability RULEs (reverse migration). No-op on SQLite."""
    if not _is_postgresql(schema_editor):
        return
    schema_editor.execute(
        "DROP RULE IF EXISTS protect_audit_log_update ON audit_auditlog"
    )
    schema_editor.execute(
        "DROP RULE IF EXISTS protect_audit_log_delete ON audit_auditlog"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        # -----------------------------------------------------------------
        # 1. Create the AuditLog table with all 15 required fields.
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                # 1. UUID PK
                (
                    "log_id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                # 2. UTC timestamp
                (
                    "timestamp",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                # 3. Tenant (nullable)
                (
                    "tenant_id",
                    models.UUIDField(blank=True, null=True, db_index=True),
                ),
                # 4. Branch (nullable)
                (
                    "branch_id",
                    models.UUIDField(blank=True, null=True, db_index=True),
                ),
                # 5. User (nullable)
                (
                    "user_id",
                    models.UUIDField(blank=True, null=True, db_index=True),
                ),
                # 6. User role at time of action
                (
                    "user_role",
                    models.CharField(blank=True, max_length=50),
                ),
                # 7. IP address (nullable)
                (
                    "ip_address",
                    models.GenericIPAddressField(blank=True, null=True),
                ),
                # 8. User-Agent
                (
                    "user_agent",
                    models.TextField(blank=True),
                ),
                # 9. Action enum code
                (
                    "action",
                    models.CharField(db_index=True, max_length=100),
                ),
                # 10. Resource type
                (
                    "resource_type",
                    models.CharField(max_length=100),
                ),
                # 11. Resource ID (nullable)
                (
                    "resource_id",
                    models.UUIDField(blank=True, null=True),
                ),
                # 12. Old value snapshot
                (
                    "old_value",
                    models.JSONField(blank=True, null=True),
                ),
                # 13. New value snapshot
                (
                    "new_value",
                    models.JSONField(blank=True, null=True),
                ),
                # 14. Status (success / failure)
                (
                    "status",
                    models.CharField(
                        choices=[("success", "Success"), ("failure", "Failure")],
                        default="success",
                        max_length=10,
                    ),
                ),
                # 15. Failure reason
                (
                    "failure_reason",
                    models.TextField(blank=True),
                ),
            ],
            options={
                "verbose_name": "audit log",
                "verbose_name_plural": "audit logs",
                "ordering": ["-timestamp"],
            },
        ),
        # -----------------------------------------------------------------
        # 2. Add PostgreSQL RULEs to enforce immutability at the DB layer.
        #
        #    These rules silently discard UPDATE and DELETE statements on the
        #    audit_auditlog table, regardless of which database user or role
        #    issues the command.  This prevents accidental or malicious
        #    tampering even by users with direct database access.
        #
        #    Requirement 5.4: "no user — including Super_Admin — may modify or
        #    delete any audit log record through any API or database interface."
        #
        #    Uses a RunPython operation so we can guard against non-PostgreSQL
        #    databases (SQLite is used in unit tests).
        # -----------------------------------------------------------------
        migrations.RunPython(
            code=_add_immutability_rules,
            reverse_code=_remove_immutability_rules,
        ),
    ]
