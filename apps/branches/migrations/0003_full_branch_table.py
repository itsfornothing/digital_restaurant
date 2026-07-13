"""
Migration 0003 — full Branch and Table models (Task 10.1).

Replaces the stub Branch model from 0002_stub_branch with the complete schema,
and adds the Table model.

Changes vs. 0002_stub_branch:
  Branch:
    - Change PK from BigAutoField → UUIDField (primary_key=True)
    - Drop 'name' default
    - Add: address, phone, email, timezone, currency, opening_hours,
            is_active, created_at

  Table:
    - New model: id (UUID PK), branch FK, number, seat_count
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branches", "0002_stub_branch"),
    ]

    operations = [
        # ------------------------------------------------------------------ #
        # 1. Drop the stub Branch table and recreate with full schema
        # ------------------------------------------------------------------ #
        migrations.DeleteModel(name="Branch"),
        migrations.CreateModel(
            name="Branch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("address", models.TextField()),
                ("phone", models.CharField(max_length=30)),
                ("email", models.EmailField(max_length=254)),
                (
                    "timezone",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="IANA timezone string (e.g. 'Africa/Addis_Ababa'). Leave blank to inherit from TenantConfig.",
                        max_length=50,
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="ISO 4217 currency code (e.g. 'ETB'). Leave blank to inherit from TenantConfig.",
                        max_length=3,
                    ),
                ),
                (
                    "opening_hours",
                    models.JSONField(
                        default=dict,
                        help_text="Per-day opening hours. Keys are lowercase day names (monday–sunday); values are {open: HH:MM, close: HH:MM} dicts.",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
            ],
            options={
                "verbose_name": "Branch",
                "verbose_name_plural": "Branches",
                "ordering": ["name"],
            },
        ),
        # ------------------------------------------------------------------ #
        # 2. Create Table model
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="Table",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tables",
                        to="branches.branch",
                    ),
                ),
                (
                    "number",
                    models.CharField(
                        help_text="Table identifier displayed to staff and customers (e.g. '7', 'T-12').",
                        max_length=20,
                    ),
                ),
                (
                    "seat_count",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Number of seats at this table.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Table",
                "verbose_name_plural": "Tables",
                "ordering": ["number"],
                "unique_together": {("branch", "number")},
            },
        ),
    ]
