"""
expenses/serializers.py

ExpenseSerializer for full CRUD on Expense records.

`branch` is read-only — it is always injected from the URL parameter
`branch_pk` in the ViewSet's `perform_create`, never supplied by the client.

Requirements: 12.1, 12.2, 12.3
"""

from rest_framework import serializers

from apps.expenses.models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    """
    Full serializer for Expense.

    Read-only fields:
        id, branch, created_at, updated_at

    Writable fields (on create / partial_update):
        description, category, amount, date_incurred, notes,
        reference_number, attachment
    """

    branch_id = serializers.UUIDField(source="branch.id", read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "branch_id",
            "description",
            "category",
            "amount",
            "date_incurred",
            "notes",
            "reference_number",
            "attachment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "branch_id", "created_at", "updated_at"]
