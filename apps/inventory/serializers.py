"""
inventory/serializers.py

DRF serializers for InventoryItem and Supplier.

Hierarchy:
  - SupplierSerializer        — full CRUD fields for Supplier
  - InventoryItemListSerializer — lightweight read serializer for list views
  - InventoryItemSerializer   — full CRUD serializer (nested supplier on read,
                                 supplier PK on write)

Requirements: 11.1
"""

from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import InventoryItem, Supplier


# ---------------------------------------------------------------------------
# SupplierSerializer
# ---------------------------------------------------------------------------


class SupplierSerializer(serializers.ModelSerializer):
    """
    Full CRUD serializer for Supplier.

    ``branch`` is read-only; it is set by the view's ``perform_create``.
    """

    class Meta:
        model = Supplier
        fields = ["id", "branch", "name", "contact"]
        read_only_fields = ["id", "branch"]


# ---------------------------------------------------------------------------
# InventoryItemListSerializer — lightweight list view
# ---------------------------------------------------------------------------


class InventoryItemListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for the list action.
    Omits the full nested supplier object to keep payloads compact.
    """

    supplier_name = serializers.CharField(
        source="supplier.name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "branch",
            "name",
            "category",
            "quantity",
            "unit",
            "purchase_price",
            "supplier",
            "supplier_name",
            "expiration_date",
            "reorder_threshold",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# InventoryItemSerializer — full CRUD
# ---------------------------------------------------------------------------


class InventoryItemSerializer(serializers.ModelSerializer):
    """
    Full serializer for InventoryItem create / update / retrieve.

    Read:   ``supplier`` is exposed as a nested SupplierSerializer object.
    Write:  ``supplier_id`` is used to set the FK (UUID PK).

    ``branch`` is read-only; it is injected by the view's ``perform_create``.
    """

    # Nested supplier for read responses
    supplier = SupplierSerializer(read_only=True)

    # Writable FK for create/update — accepts a UUID
    supplier_id = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.all(),
        source="supplier",
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "branch",
            "name",
            "category",
            "quantity",
            "unit",
            "purchase_price",
            "supplier",
            "supplier_id",
            "expiration_date",
            "reorder_threshold",
        ]
        read_only_fields = ["id", "branch", "supplier"]

    def to_representation(self, instance):
        """
        Override to always return the nested supplier in read responses,
        even though ``supplier_id`` is write-only in the fields definition.
        """
        ret = super().to_representation(instance)
        return ret
