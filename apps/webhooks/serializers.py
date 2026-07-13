import secrets

from rest_framework import serializers

from apps.webhooks.models import WebhookConfig


class WebhookConfigSerializer(serializers.ModelSerializer):
    branch_id = serializers.UUIDField(source="branch.id", read_only=True)

    class Meta:
        model = WebhookConfig
        fields = [
            "id", "branch", "branch_id", "url", "secret",
            "events", "is_active", "created_at",
            "last_triggered_at", "failure_count",
        ]
        read_only_fields = [
            "id", "branch_id", "secret", "created_at",
            "last_triggered_at", "failure_count",
        ]
        extra_kwargs = {
            "branch": {"write_only": True},
        }

    def create(self, validated_data):
        validated_data["secret"] = secrets.token_hex(16)
        return super().create(validated_data)
