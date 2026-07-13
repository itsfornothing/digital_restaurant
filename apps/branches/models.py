"""
branches/models.py

Branch, Table and Room models for the Operations layer.

Branch represents a physical restaurant location belonging to a tenant.
Table represents a seating table within a Branch.
Room represents a private dining room within a Branch.

Both models live in tenant schemas (TENANT_APPS).

Requirements: 8.1, 8.2, 8.3, 8.6
"""

import uuid

from django.db import models


class Branch(models.Model):
    """
    Represents a physical restaurant location owned by a Tenant.

    All operational data (orders, inventory, expenses, income, menus, QR codes)
    is scoped to a Branch record, ensuring complete isolation between locations
    within the same tenant (Requirement 8.2).

    Fields:
        id           — UUID primary key
        name         — Human-readable branch name (e.g. "Downtown Branch")
        address      — Full physical address (free text)
        phone        — Contact phone number
        email        — Contact email address
        timezone     — Optional IANA timezone override (e.g. "Africa/Addis_Ababa").
                       Falls back to the tenant's TenantConfig.timezone when blank.
        currency     — Optional ISO 4217 currency code override (e.g. "ETB", "USD").
                       Falls back to the tenant's TenantConfig.currency when blank.
        opening_hours — JSON dict mapping day-of-week (lowercase, e.g. "monday") to
                        {"open": "HH:MM", "close": "HH:MM"} objects.
                        Example:
                        {
                            "monday":    {"open": "08:00", "close": "22:00"},
                            "saturday":  {"open": "09:00", "close": "23:00"},
                            "sunday":    {"open": "10:00", "close": "20:00"}
                        }
        created_at   — Auto-set creation timestamp
        is_active    — Soft-delete flag; inactive branches are excluded from limits
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    address = models.TextField()
    phone = models.CharField(max_length=30)
    email = models.EmailField()
    timezone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="IANA timezone string (e.g. 'Africa/Addis_Ababa'). "
                  "Leave blank to inherit from TenantConfig.",
    )
    currency = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="ISO 4217 currency code (e.g. 'ETB'). "
                  "Leave blank to inherit from TenantConfig.",
    )
    opening_hours = models.JSONField(
        default=dict,
        help_text=(
            "Per-day opening hours. Keys are lowercase day names "
            "(monday–sunday); values are {open: HH:MM, close: HH:MM} dicts."
        ),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "branches"
        verbose_name = "Branch"
        verbose_name_plural = "Branches"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Table(models.Model):
    """
    Represents a physical dining table within a Branch.

    Tables are linked to QR codes so customers can scan and place orders.
    Deleting a Branch cascades to remove all its Tables (and QR codes, via
    their own cascade).

    Fields:
        id         — UUID primary key
        branch     — FK to the owning Branch (CASCADE delete)
        number     — Table identifier as displayed to staff/customers (e.g. "7", "T-12")
        seat_count — Seating capacity of the table
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="tables",
    )
    number = models.CharField(
        max_length=20,
        help_text="Table identifier displayed to staff and customers (e.g. '7', 'T-12').",
    )
    seat_count = models.PositiveSmallIntegerField(
        default=1,
        help_text="Number of seats at this table.",
    )

    class Meta:
        app_label = "branches"
        verbose_name = "Table"
        verbose_name_plural = "Tables"
        unique_together = [("branch", "number")]
        ordering = ["number"]

    def __str__(self) -> str:
        return f"Table {self.number} ({self.branch.name})"


class Room(models.Model):
    """
    Represents a private dining room within a Branch.

    Rooms are linked to QR codes so customers can scan and place orders,
    identical to how Tables work.  Deleting a Branch cascades to remove
    all its Rooms (and QR codes, via their own cascade).

    Fields:
        id       — UUID primary key
        branch   — FK to the owning Branch (CASCADE delete)
        name     — Room identifier (e.g. "VIP Room 1", "Private Dining")
        capacity — Maximum number of guests the room can seat
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    name = models.CharField(
        max_length=100,
        help_text="Room identifier (e.g. 'VIP Room 1', 'Private Dining').",
    )
    capacity = models.PositiveSmallIntegerField(
        default=1,
        help_text="Maximum number of guests this room can seat.",
    )

    class Meta:
        app_label = "branches"
        verbose_name = "Room"
        verbose_name_plural = "Rooms"
        unique_together = [("branch", "name")]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"Room {self.name} ({self.branch.name})"
