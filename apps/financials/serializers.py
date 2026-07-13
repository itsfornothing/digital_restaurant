"""
financials/serializers.py

Serializers for Income, ProfitRecord, and financial dashboard data.

Requirements: 13.1, 13.2
"""

from rest_framework import serializers

from apps.financials.models import Income, ProfitRecord


class IncomeSerializer(serializers.ModelSerializer):
    """
    Full serializer for Income.

    Read-only fields: id, branch_id, created_at.
    `branch` is always injected from the URL in the ViewSet.
    """

    branch_id = serializers.UUIDField(source="branch.id", read_only=True)

    class Meta:
        model = Income
        fields = [
            "id",
            "branch_id",
            "source",
            "order",
            "amount",
            "description",
            "date",
            "created_at",
        ]
        read_only_fields = ["id", "branch_id", "created_at"]


class ProfitRecordSerializer(serializers.ModelSerializer):
    """Read-only serializer for ProfitRecord snapshots."""

    class Meta:
        model = ProfitRecord
        fields = [
            "id",
            "branch",
            "period_type",
            "period_start",
            "period_end",
            "total_income",
            "total_expenses",
            "net_profit",
            "last_updated",
        ]
        read_only_fields = fields
