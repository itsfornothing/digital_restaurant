"""
orders/serializers.py

DRF serializers for Order and OrderItem models.
"""

from rest_framework import serializers

from apps.orders.models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    """Serializer for OrderItem — used in nested Order representations."""

    menu_item_name = serializers.CharField(
        source="menu_item.name", read_only=True
    )

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "menu_item",
            "menu_item_name",
            "quantity",
            "unit_price",
            "special_instructions",
        ]
        read_only_fields = ["id", "unit_price"]


class OrderSerializer(serializers.ModelSerializer):
    """Full serializer for Order, including nested items."""

    items = OrderItemSerializer(many=True, read_only=True)
    table_number = serializers.CharField(source="table.number", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "branch",
            "table",
            "table_number",
            "status",
            "customer_name",
            "customer_phone",
            "is_anonymized",
            "placed_at",
            "total_amount",
            "items",
        ]
        read_only_fields = [
            "id",
            "order_number",
            "placed_at",
            "is_anonymized",
        ]


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Validates the payload for PATCH /api/v1/orders/{id}/status/."""

    status = serializers.ChoiceField(
        choices=[
            "confirmed",
            "received",
            "preparing",
            "ready",
            "served",
            "cancelled",
        ]
    )
