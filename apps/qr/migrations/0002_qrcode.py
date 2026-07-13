"""
Migration 0002 — add QRCode model (Task 15.1).

Creates the qr_qrcode table in each tenant schema.

QRCode fields:
  id         — UUID PK (auto-generated)
  table      — FK to branches.Table (CASCADE)
  token      — UUID, unique (the scannable token in the QR URL)
  is_active  — Boolean, default True
  image_url  — URLField, blank=True (populated after R2 upload)
  created_at — DateTimeField, auto_now_add=True

Requirements: 14.1, 14.3
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qr", "0001_initial"),
        ("branches", "0003_full_branch_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="QRCode",
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
                    "table",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qr_codes",
                        to="branches.table",
                    ),
                ),
                (
                    "token",
                    models.UUIDField(
                        unique=True,
                        default=uuid.uuid4,
                        help_text=(
                            "Unique scannable token encoded in the QR image URL. "
                            "Each generation event produces a fresh UUID."
                        ),
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "True for the most-recently generated QR code for this table. "
                            "Older codes are set to False by QRService.generate_qr."
                        ),
                    ),
                ),
                (
                    "image_url",
                    models.URLField(
                        blank=True,
                        default="",
                        max_length=500,
                        help_text="Public URL of the QR image stored in Cloudflare R2.",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
            ],
            options={
                "verbose_name": "QR Code",
                "verbose_name_plural": "QR Codes",
                "ordering": ["-created_at"],
            },
        ),
    ]
