from rest_framework import mixins, viewsets

from apps.branches.models import Branch
from apps.webhooks.models import WebhookConfig
from apps.webhooks.serializers import WebhookConfigSerializer
from shared.permissions import IsBranchManager


class WebhookConfigViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = WebhookConfigSerializer
    permission_classes = [IsBranchManager]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        branch_pk = self.kwargs.get("branch_pk")
        qs = WebhookConfig.objects.select_related("branch")
        if branch_pk:
            qs = qs.filter(branch_id=branch_pk)
        return qs

    def perform_create(self, serializer):
        branch_pk = self.kwargs.get("branch_pk")
        branch = Branch.objects.get(pk=branch_pk)
        serializer.save(branch=branch)
