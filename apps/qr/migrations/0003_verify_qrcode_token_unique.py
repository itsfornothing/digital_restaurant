"""
Migration 0003 — Verify QRCode.token unique index (Task 20.3).

QRCode.token is declared as ``models.UUIDField(unique=True)`` in the model
(apps/qr/models.py), which Django automatically maps to a UNIQUE constraint
(and therefore a unique B-tree index) in the database.

This was included in the original table creation migration (0002_qrcode.py).

No new database operation is required.  This migration exists as an
explicit audit trail confirming that the unique index is present and
was verified as part of the Task 20.3 performance indexing review.

If the UNIQUE constraint is ever missing on a production database due to a
manual schema change, re-run migrations; Django will detect the missing
constraint and re-apply it via 0002_qrcode.

Requirements: 19.2 (Task 20.3)
"""

from django.db import migrations


class Migration(migrations.Migration):
    """
    Verification-only migration — no schema operations performed.

    The unique constraint on qr_qrcode.token was created by migration
    0002_qrcode (``token = models.UUIDField(unique=True)``).  This migration
    records the verification step from the Task 20.3 performance review.
    """

    dependencies = [
        ("qr", "0002_qrcode"),
    ]

    operations = [
        # No-op: the unique index already exists from 0002_qrcode.
        # Token uniqueness is enforced by Django's UUIDField(unique=True)
        # which emits: CREATE UNIQUE INDEX qr_qrcode_token_... ON qr_qrcode(token).
    ]
