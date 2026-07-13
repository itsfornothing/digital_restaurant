"""
qr/models.py

QRCode model — one QR code per table, per generation event.

Each time a Branch_Manager regenerates the QR code for a table, all prior
QRCode records for that table are deactivated (is_active=False) and a fresh
record with a new UUID token is created.  The customer-facing scan endpoint
validates tokens against this table; an inactive token raises QRCodeInvalid.

Design reference: apps/qr/ — C1: QR-Based Digital Ordering
Requirements: 14.1, 14.3
"""

import uuid

from django.db import models


class QRCode(models.Model):
    """
    Represents a single QR code issued for a specific Table or Room.

    Each QR code links to exactly one location — either a Table or a Room
    (never both).  The `table` FK is set for table QR codes; `room` FK is
    set for room QR codes; the other remains null.

    Fields:
        id         — UUID primary key (immutable, set once at creation)
        table      — FK to the Table this code is for (null for room codes)
        room       — FK to the Room this code is for (null for table codes)
        token      — UUID used as the scannable token in the QR image URL;
                     unique across all QR codes in the tenant schema so that
                     a token unambiguously identifies a single QRCode record
        is_active  — Only the most recently generated code for a location is
                     active; prior codes are deactivated by QRService.generate_qr
        image_url  — Public URL of the rendered QR image stored locally
                      (set after save; may be empty string if save failed)
        created_at — UTC timestamp of creation (auto-set)

    Requirement 14.3: When a QR is regenerated, all prior QRCodes for the
    location are set to is_active=False by QRService before the new one is saved.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        "branches.Table",
        on_delete=models.CASCADE,
        related_name="qr_codes",
        null=True,
        blank=True,
    )
    room = models.ForeignKey(
        "branches.Room",
        on_delete=models.CASCADE,
        related_name="qr_codes",
        null=True,
        blank=True,
    )
    token = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        help_text=(
            "Unique scannable token encoded in the QR image URL. "
            "Each generation event produces a fresh UUID."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "True for the most-recently generated QR code for this location. "
            "Older codes are set to False by QRService.generate_qr."
        ),
    )
    image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Public URL of the QR image stored locally.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "qr"
        verbose_name = "QR Code"
        verbose_name_plural = "QR Codes"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        if self.table_id:
            return f"QRCode({self.token}, table={self.table_id}, {status})"
        if self.room_id:
            return f"QRCode({self.token}, room={self.room_id}, {status})"
        return f"QRCode({self.token}, unlinked, {status})"
