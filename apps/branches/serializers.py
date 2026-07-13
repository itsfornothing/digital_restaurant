"""
branches/serializers.py

DRF serializers for Branch and Table models.

BranchSerializer  — full CRUD serializer for Branch.
BranchListSerializer — lightweight read-only serializer for list views.
TableSerializer   — CRUD serializer for Table, nested under a Branch.

Requirements: 8.1
"""

from rest_framework import serializers

from apps.branches.models import Branch, Room, Table


# ---------------------------------------------------------------------------
# Table serializer
# ---------------------------------------------------------------------------

class TableSerializer(serializers.ModelSerializer):
    """
    Serializer for Table objects.

    ``branch`` is a read-only field populated automatically from the URL
    kwargs in the nested view (BranchViewSet.perform_create sets it).
    The ``branch_id`` field is exposed as a UUID in responses.
    """

    branch_id = serializers.UUIDField(source="branch.id", read_only=True)

    class Meta:
        model = Table
        fields = ["id", "branch_id", "number", "seat_count"]
        read_only_fields = ["id", "branch_id"]


# ---------------------------------------------------------------------------
# Branch serializers
# ---------------------------------------------------------------------------

class BranchSerializer(serializers.ModelSerializer):
    """
    Full serializer for Branch — used on create (POST) and update (PATCH).

    Validates:
      - ``name`` is non-empty
      - ``email`` is a valid email address
      - ``opening_hours`` is a dict (JSON object); no deep-schema validation
        here — that is handled at the application layer

    The ``tables`` field exposes nested Table records as a read-only list,
    populated on retrieve (GET /api/v1/branches/{id}/).
    """

    tables = TableSerializer(many=True, read_only=True)

    class Meta:
        model = Branch
        fields = [
            "id",
            "name",
            "address",
            "phone",
            "email",
            "timezone",
            "currency",
            "opening_hours",
            "is_active",
            "created_at",
            "tables",
        ]
        read_only_fields = ["id", "created_at", "tables"]

    def validate_opening_hours(self, value):
        """
        Ensure opening_hours is a dict.  Individual day entries may be empty
        (no hours configured for that day) or must have 'open' and 'close' keys.
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "opening_hours must be a JSON object mapping day names to "
                "{'open': 'HH:MM', 'close': 'HH:MM'} dicts."
            )
        valid_days = {
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        }
        for day, hours in value.items():
            if day not in valid_days:
                raise serializers.ValidationError(
                    f"Invalid day key: '{day}'. Must be one of {sorted(valid_days)}."
                )
            if hours and isinstance(hours, dict):
                if "open" not in hours or "close" not in hours:
                    raise serializers.ValidationError(
                        f"Day '{day}' must have both 'open' and 'close' keys."
                    )
        return value

    def validate_currency(self, value):
        """Currency code must be exactly 3 uppercase letters when provided."""
        if value and (len(value) != 3 or not value.isalpha()):
            raise serializers.ValidationError(
                "currency must be a valid 3-letter ISO 4217 code (e.g. 'ETB', 'USD')."
            )
        return value.upper() if value else value


class RoomSerializer(serializers.ModelSerializer):
    branch_id = serializers.UUIDField(source="branch.id", read_only=True)

    class Meta:
        model = Room
        fields = ["id", "branch_id", "name", "capacity"]
        read_only_fields = ["id", "branch_id"]


class BranchListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for list views.

    Excludes nested tables to keep the list response compact.
    """

    class Meta:
        model = Branch
        fields = [
            "id",
            "name",
            "address",
            "phone",
            "email",
            "timezone",
            "currency",
            "is_active",
            "created_at",
        ]
        read_only_fields = fields
