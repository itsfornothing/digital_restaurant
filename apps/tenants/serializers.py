"""
tenants/serializers.py — Serializers for tenant provisioning API endpoints.

Requirements: 1.2, 1.4, 1.5, 1.6
"""

from rest_framework import serializers

from apps.tenants.models import Tenant


class CreateTenantSerializer(serializers.Serializer):
    """
    Validates the request body for POST /api/v1/tenants/.

    Expected payload::

        {
            "name": "Green Leaf",
            "slug": "greenleaf",
            "plan_id": 1,
            "owner_email": "owner@greenleaf.et"
        }
    """

    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=50)
    plan_id = serializers.IntegerField(min_value=1)
    owner_email = serializers.EmailField()


class TenantSerializer(serializers.ModelSerializer):
    """
    Read serializer for Tenant instances returned in API responses.
    """

    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "schema_name", "is_active", "created_at"]
        read_only_fields = fields
