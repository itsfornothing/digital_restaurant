"""
billing/views.py

ViewSets / APIViews for subscription plan and usage management.

Endpoints:
    GET  /api/v1/plans/                    → SubscriptionPlanViewSet.list
    POST /api/v1/plans/                    → SubscriptionPlanViewSet.create
    PATCH /api/v1/plans/{id}/              → SubscriptionPlanViewSet.partial_update

    POST /api/v1/tenants/{id}/subscription/ → AssignSubscriptionView
    GET  /api/v1/tenants/{id}/usage/        → TenantUsageView

All endpoints require IsSuperAdmin permission.

Requirements: 2.1, 2.2, 2.5, 2.6, 4.1, 4.2, 4.3
"""

from __future__ import annotations

import logging
from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.exceptions import ResourceLimitExceeded as DomainResourceLimitExceeded
from apps.billing.models import SubscriptionPlan, TenantSubscription
from apps.billing.serializers import (
    SubscriptionPlanSerializer,
    TenantSubscriptionSerializer,
    TenantUsageSerializer,
)
from apps.billing.services import BillingService
from apps.tenants.models import Tenant
from shared.permissions import (
    AuditLogMixin,
    IsSuperAdmin,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resource_limit_exceeded_response(exc: DomainResourceLimitExceeded) -> Response:
    """
    Convert a domain ResourceLimitExceeded into HTTP 402 with standard body.

    Response body::

        {
            "error": "RESOURCE_LIMIT_EXCEEDED",
            "resource_type": "branches",
            "current": 5,
            "limit": 5
        }
    """
    return Response(
        {
            "error": "RESOURCE_LIMIT_EXCEEDED",
            "resource_type": exc.resource_type,
            "current": exc.current_count,
            "limit": exc.limit,
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


# ---------------------------------------------------------------------------
# SubscriptionPlanViewSet
# ---------------------------------------------------------------------------


class SubscriptionPlanViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    ViewSet for SubscriptionPlan management.

    Allowed roles: Super_Admin only.

    Actions:
        list            — GET  /api/v1/plans/
        create          — POST /api/v1/plans/
        partial_update  — PATCH /api/v1/plans/{id}/

    Requirements: 2.1, 2.6, 4.2
    """

    permission_classes = [IsSuperAdmin]
    serializer_class = SubscriptionPlanSerializer
    queryset = SubscriptionPlan.objects.all().order_by("price_etb")

    # ------------------------------------------------------------------
    # GET /api/v1/plans/
    # ------------------------------------------------------------------

    def list(self, request):
        """Return all subscription plans ordered by price."""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # POST /api/v1/plans/
    # ------------------------------------------------------------------

    def create(self, request):
        """Create a new subscription plan."""
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid request data.",
                        "details": serializer.errors,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan = serializer.save()
        return Response(
            self.get_serializer(plan).data,
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # PATCH /api/v1/plans/{id}/
    # ------------------------------------------------------------------

    def partial_update(self, request, pk=None):
        """Partially update an existing subscription plan."""
        plan = get_object_or_404(SubscriptionPlan, pk=pk)
        serializer = self.get_serializer(plan, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid request data.",
                        "details": serializer.errors,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan = serializer.save()
        return Response(self.get_serializer(plan).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# AssignSubscriptionView
# ---------------------------------------------------------------------------


class AssignSubscriptionView(APIView):
    """
    POST /api/v1/tenants/{id}/subscription/

    Assigns (creates or replaces) a SubscriptionPlan for a tenant.

    Request body::

        {
            "plan_id": 1,
            "status": "active",              (optional, defaults to "active")
            "current_period_start": "2025-01-01",  (optional, defaults to today)
            "current_period_end": "2026-01-01"
        }

    Returns 201 Created on first assignment, 200 OK on update.
    Immediately applies the new plan limits / feature flags (Req 2.7).

    Requirements: 2.2, 2.7
    """

    permission_classes = [IsSuperAdmin]

    def post(self, request, pk=None):
        tenant = get_object_or_404(Tenant, pk=pk)

        # Determine whether this is a create or update
        try:
            existing_subscription = TenantSubscription.objects.get(tenant=tenant)
        except TenantSubscription.DoesNotExist:
            existing_subscription = None

        serializer = TenantSubscriptionSerializer(
            instance=existing_subscription,
            data=request.data,
        )
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid request data.",
                        "details": serializer.errors,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Inject defaults
        validated = serializer.validated_data
        if "current_period_start" not in validated:
            validated["current_period_start"] = date.today()

        if existing_subscription is None:
            # Create
            subscription = TenantSubscription.objects.create(
                tenant=tenant,
                plan=validated["plan"],
                status=validated.get("status", TenantSubscription.Status.ACTIVE),
                current_period_start=validated["current_period_start"],
                current_period_end=validated["current_period_end"],
            )
            response_status = status.HTTP_201_CREATED
        else:
            # Update in-place so new limits apply immediately (Req 2.7)
            existing_subscription.plan = validated["plan"]
            existing_subscription.status = validated.get(
                "status", existing_subscription.status
            )
            existing_subscription.current_period_start = validated[
                "current_period_start"
            ]
            existing_subscription.current_period_end = validated["current_period_end"]
            existing_subscription.save()
            subscription = existing_subscription
            response_status = status.HTTP_200_OK

        out_serializer = TenantSubscriptionSerializer(subscription)
        return Response(out_serializer.data, status=response_status)


# ---------------------------------------------------------------------------
# TenantUsageView
# ---------------------------------------------------------------------------


class TenantUsageView(APIView):
    """
    GET /api/v1/tenants/{id}/usage/

    Returns usage metrics for the requested tenant.

    Response body::

        {
            "tenant_id": "...",
            "plan": "Starter",
            "branches": {"used": 2, "limit": 5},
            "menu_items": {"used": 20, "limit": 50},
            "staff_accounts": {"used": 3, "limit": 10},
            "subscription_status": "active"
        }

    Requirements: 2.5
    """

    permission_classes = [IsSuperAdmin]

    def get(self, request, pk=None):
        tenant = get_object_or_404(Tenant, pk=pk)

        try:
            subscription = TenantSubscription.objects.select_related("plan").get(
                tenant=tenant
            )
        except TenantSubscription.DoesNotExist:
            return Response(
                {
                    "error": {
                        "code": "NO_SUBSCRIPTION",
                        "message": "This tenant has no active subscription.",
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        plan = subscription.plan

        # Gather current counts via BillingService helper.
        # BillingService._get_current_count requires the correct tenant schema
        # to be active on the DB connection.  In tests (SQLite) this is a
        # no-op; in production the TenantMiddleware already sets it.
        branches_used = BillingService._get_current_count("branches")
        menu_items_used = BillingService._get_current_count("menu_items")
        staff_accounts_used = BillingService._get_current_count("staff_accounts")

        usage_data = {
            "tenant_id": str(tenant.pk),
            "plan": plan.name,
            "branches": {
                "used": branches_used,
                "limit": plan.max_branches,
            },
            "menu_items": {
                "used": menu_items_used,
                "limit": plan.max_menu_items,
            },
            "staff_accounts": {
                "used": staff_accounts_used,
                "limit": plan.max_staff_accounts,
            },
            "subscription_status": subscription.status,
        }

        serializer = TenantUsageSerializer(usage_data)
        return Response(serializer.data, status=status.HTTP_200_OK)
