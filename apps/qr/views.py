"""
qr/views.py

ViewSets for QR code generation and management (staff-facing).

Endpoints implemented here:
  GET    /api/v1/branches/{branch_pk}/qr-codes/       — list QR codes for branch
  POST   /api/v1/branches/{branch_pk}/qr-codes/       — generate QR code for a table
  POST   /api/v1/qr-codes/{pk}/regenerate/            — regenerate (invalidate + new)

Permission matrix (Requirement 4.2):
  All endpoints: IsBranchManager

The customer-facing session endpoint (POST /api/v1/customer/session/) lives
in customer_views.py and uses IsCustomerSession-exempt logic.

Requirements: 4.1, 4.2, 4.3, 14.1, 14.3
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.branches.models import Branch, Table
from apps.qr.models import QRCode
from apps.qr.serializers import QRCodeSerializer
from apps.qr.services import QRService
from shared.permissions import AuditLogMixin, IsBranchManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QRCodeListCreateView — GET / POST /api/v1/branches/{branch_pk}/qr-codes/
# ---------------------------------------------------------------------------


class QRCodeListCreateView(AuditLogMixin, APIView):
    """
    GET  /api/v1/branches/{branch_pk}/qr-codes/
        List all QRCode records for the given branch.
        Returns all QR codes (active and inactive) belonging to tables in
        the specified branch.

    POST /api/v1/branches/{branch_pk}/qr-codes/
        Generate a new QR code for a table in the given branch.
        Request body: {"table_id": "<uuid>"}

        The view:
          1. Validates that the branch exists.
          2. Validates that the table belongs to that branch.
           3. Calls QRService().generate_qr(table) which:
                - Deactivates all prior QRCode records for the table.
                - Creates a new QRCode with a fresh UUID token.
                - Renders and saves the QR image to local storage.
                - Persists the public image_url on the QRCode record.
          4. Returns the new QRCode serialized as 201 Created.

    Permission: IsBranchManager
    Requirements: 14.1, 14.3
    """

    permission_classes = [IsBranchManager]

    def _get_branch(self, branch_pk):
        """Resolve the branch from URL, raising 404 if not found."""
        try:
            return Branch.objects.get(pk=branch_pk)
        except Branch.DoesNotExist:
            raise NotFound("Branch not found.")

    def _enforce_branch_scope(self, request, branch):
        """
        For Branch_Manager role, verify the requested branch matches the
        user's assigned branch (Requirement 4.3).

        Super_Admin and Tenant_Owner have broader scope and are not
        restricted here.
        """
        from apps.authentication.models import UserRole

        user = request.user
        if not hasattr(user, "role"):
            return  # unauthenticated — permission class handles this

        if user.role in (UserRole.BRANCH_MANAGER, UserRole.RECEPTIONIST, UserRole.KITCHEN_STAFF):
            if user.branch_id and str(user.branch_id) != str(branch.id):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "You do not have permission to access resources outside your assigned branch."
                )

    def get(self, request, branch_pk=None):
        """
        List all QR codes for the branch.

        Returns QR codes for both tables and rooms belonging to this branch.
        """
        from django.db.models import Q

        branch = self._get_branch(branch_pk)
        self._enforce_branch_scope(request, branch)

        qr_codes = QRCode.objects.filter(
            Q(table__branch_id=branch.id) | Q(room__branch_id=branch.id)
        ).select_related("table", "room").order_by("-created_at")

        serializer = QRCodeSerializer(qr_codes, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, branch_pk=None):
        """
        Generate a new QR code for a table or room in the given branch.

        Expected request body (one of):
            {"table_id": "<uuid>"}
            {"room_id": "<uuid>"}

        Returns the new QRCode as 201 Created.
        """
        from apps.branches.models import Room

        branch = self._get_branch(branch_pk)
        self._enforce_branch_scope(request, branch)

        table_id = request.data.get("table_id")
        room_id = request.data.get("room_id")

        if table_id and room_id:
            raise ValidationError(
                "Provide either table_id or room_id, not both."
            )
        if not table_id and not room_id:
            raise ValidationError(
                "Either table_id or room_id is required."
            )

        if table_id:
            try:
                location = Table.objects.get(pk=table_id, branch=branch)
            except Table.DoesNotExist:
                raise ValidationError(
                    {"table_id": "Table not found or does not belong to this branch."}
                )
        else:
            try:
                location = Room.objects.get(pk=room_id, branch=branch)
            except Room.DoesNotExist:
                raise ValidationError(
                    {"room_id": "Room not found or does not belong to this branch."}
                )

        service = QRService()
        try:
            qr_code = service.generate_qr(location)
        except Exception as exc:
            logger.error(
                "QRCodeListCreateView.post: QRService.generate_qr failed for %s %s: %s",
                "room" if room_id else "table",
                location.pk,
                exc,
                exc_info=True,
            )
            raise ValidationError(
                {"detail": "Failed to generate QR code. Please try again."}
            )

        serializer = QRCodeSerializer(qr_code)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# QRCodeRegenerateView — POST /api/v1/qr-codes/{pk}/regenerate/
# ---------------------------------------------------------------------------


class QRCodeRegenerateView(AuditLogMixin, APIView):
    """
    POST /api/v1/qr-codes/{pk}/regenerate/

    Regenerates the QR code for the same location (table or room):
      1. Looks up the existing QRCode by pk.
      2. Retrieves the associated Table or Room.
      3. Calls QRService().generate_qr(location), which:
           - Deactivates all prior QRCode records for the location (including the
             looked-up one), satisfying Requirement 14.3.
           - Creates a new QRCode with a fresh UUID token.
           - Renders and saves the QR image to local storage.
           - Persists the public image_url.
      4. Returns the new QRCode as 201 Created.

    The original QRCode is deactivated automatically by QRService; any
    customer scanning the old QR code will receive a QRCodeInvalid error
    (Requirement 14.3).

    Permission: IsBranchManager
    Requirements: 14.1, 14.3
    """

    permission_classes = [IsBranchManager]

    def post(self, request, pk=None):
        """Regenerate the QR code for the location associated with the given QRCode pk."""
        # Fetch the existing QR code
        qr_code = get_object_or_404(
            QRCode.objects.select_related(
                "table", "table__branch", "room", "room__branch"
            ),
            pk=pk,
        )

        # Enforce branch scope for Branch_Manager role
        from apps.authentication.models import UserRole

        user = request.user
        if hasattr(user, "role") and user.role in (
            UserRole.BRANCH_MANAGER,
            UserRole.RECEPTIONIST,
            UserRole.KITCHEN_STAFF,
        ):
            branch_id = (
                qr_code.table.branch_id if qr_code.table_id
                else qr_code.room.branch_id
            )
            if user.branch_id and str(user.branch_id) != str(branch_id):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "You do not have permission to access resources outside your assigned branch."
                )

        location = qr_code.table or qr_code.room

        # Regenerate via the service layer
        service = QRService()
        try:
            new_qr_code = service.generate_qr(location)
        except Exception as exc:
            logger.error(
                "QRCodeRegenerateView.post: QRService.generate_qr failed for location %s: %s",
                location.pk,
                exc,
                exc_info=True,
            )
            raise ValidationError(
                {"detail": "Failed to regenerate QR code. Please try again."}
            )

        serializer = QRCodeSerializer(new_qr_code)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
