"""
qr/services.py

QRService — business logic for QR code generation and validation.

Responsibilities:
  - generate_qr(table|room):
      1. Deactivate all prior QRCode records for the given location.
      2. Create a new QRCode with a fresh UUID token.
      3. Render a QR image using the `qrcode` library encoding the scan URL.
      4. Save the rendered image to local storage (served via /media/).
      5. Persist the public image URL on the QRCode record.
      6. Return the new QRCode instance.

  - validate_qr(token):
      Look up QRCode by token; return QRScanResult if is_active=True;
      raise QRCodeInvalid otherwise.

The scan URL encoded inside the QR image takes the form:
    https://{tenant_subdomain}/scan/{token}/

TenantConfig is resolved from the current schema context to obtain branding
(qr_design_template) for the image style.

Requirements: 14.1, 14.3
"""

import io
import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import qrcode
import qrcode.constants
from django.db import transaction
from django_tenants.utils import get_tenant
from django.db import connection

from django.core.files.storage import default_storage

from apps.qr.exceptions import QRCodeInvalid
from apps.qr.models import QRCode
from apps.branches.models import Branch, Room, Table


# ---------------------------------------------------------------------------
# QRScanResult — structured return type for validate_qr
# ---------------------------------------------------------------------------


@dataclass
class QRScanResult:
    """
    Result of a successful QR code validation.

    Attributes:
        branch — The Branch this QR code belongs to.
        table  — The Table if this QR code is for a table (None for room codes).
        room   — The Room if this QR code is for a room (None for table codes).
    """
    branch: Branch
    table: Optional[Table] = None
    room: Optional[Room] = None

    @property
    def location_type(self) -> str:
        """Return 'table' or 'room' depending on which location is set."""
        return "room" if self.room else "table"

    @property
    def location_name(self) -> str:
        """Return the display name of the location (table number or room name)."""
        if self.room:
            return self.room.name
        if self.table:
            return self.table.number
        return ""

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QR image style presets mapped to qr_design_template values
# ---------------------------------------------------------------------------

_QR_STYLE_PRESETS: dict = {
    "default": {
        "error_correction": qrcode.constants.ERROR_CORRECT_M,
        "box_size": 10,
        "border": 4,
    },
    "compact": {
        "error_correction": qrcode.constants.ERROR_CORRECT_L,
        "box_size": 8,
        "border": 2,
    },
    "high_quality": {
        "error_correction": qrcode.constants.ERROR_CORRECT_H,
        "box_size": 12,
        "border": 4,
    },
}


def _get_qr_style(template_name: str) -> dict:
    """Return QR style kwargs for a given template name, falling back to 'default'."""
    return _QR_STYLE_PRESETS.get(template_name, _QR_STYLE_PRESETS["default"])


def _get_tenant_subdomain() -> str:
    """
    Return the subdomain for the current tenant context.

    django-tenants sets the active tenant on the database connection.
    We read it from connection.tenant (available in any tenant-schema request).
    Falls back to 'localhost' in test/development environments where there is
    no active tenant.
    """
    try:
        tenant = connection.tenant
        # django-tenants Domain records hold the FQDN; the tenant slug is
        # typically the subdomain prefix.  We use the domain's primary FQDN
        # if available, otherwise fall back to tenant.schema_name.
        domain = tenant.get_primary_domain()
        if domain:
            return domain.domain
        return tenant.schema_name
    except Exception:
        # Fallback for test environments
        return "localhost"


class QRService:
    """
    Service class for QR code lifecycle management.

    Stateless — all methods are instance methods for testability, but no
    per-instance state is maintained.  Can be instantiated once or called
    as a class method pattern.
    """

    def generate_qr(self, location: Union[Table, Room]) -> QRCode:
        """
        Generate a fresh QR code for the given table or room.

        Steps performed inside a single database transaction:
          1. Deactivate all prior QRCode records for the location.
          2. Create a new QRCode with a fresh UUID token (is_active=True).
          3. Determine the tenant subdomain and QR design template.
          4. Render the QR image encoding the URL ``https://{subdomain}/scan/{token}/``.
          5. Upload the PNG image to Cloudflare R2.
          6. Update the QRCode.image_url field with the returned storage URL.
          7. Return the saved QRCode instance.

        Args:
            location: A ``branches.Table`` or ``branches.Room`` instance.
                      The location's FK to Branch is used to scope the QR
                      scan URL and retrieve TenantConfig.

        Returns:
            The newly created, active ``QRCode`` instance.

        Requirements: 14.1, 14.3
        """
        is_room = isinstance(location, Room)
        branch_id = location.branch_id

        with transaction.atomic():
            # ----------------------------------------------------------------
            # Step 1: Deactivate all prior QR codes for this location
            # ----------------------------------------------------------------
            filter_kw = {"room": location} if is_room else {"table": location}
            deactivated_count = QRCode.objects.filter(
                is_active=True, **filter_kw
            ).update(is_active=False)

            if deactivated_count:
                logger.info(
                    "QRService.generate_qr: deactivated %d prior QRCode(s) for %s %s",
                    deactivated_count,
                    "room" if is_room else "table",
                    location.pk,
                )

            # ----------------------------------------------------------------
            # Step 2: Create new QRCode with a fresh UUID token
            # ----------------------------------------------------------------
            new_token = uuid.uuid4()
            create_kw = {
                "token": new_token,
                "is_active": True,
                "image_url": "",
            }
            if is_room:
                create_kw["room"] = location
            else:
                create_kw["table"] = location
            qr_code = QRCode.objects.create(**create_kw)

            logger.info(
                "QRService.generate_qr: created QRCode %s for %s %s",
                qr_code.pk,
                "room" if is_room else "table",
                location.pk,
            )

        # ----------------------------------------------------------------
        # Step 3: Resolve tenant subdomain and design template
        # ----------------------------------------------------------------
        subdomain = _get_tenant_subdomain()

        # Load TenantConfig for the QR design template (best-effort; use
        # 'default' if the config is not yet created for this tenant).
        qr_design_template = "default"
        try:
            from apps.whitelabel.models import TenantConfig

            tenant_config = TenantConfig.objects.first()
            if tenant_config and tenant_config.qr_design_template:
                qr_design_template = tenant_config.qr_design_template
        except Exception as exc:
            logger.warning(
                "QRService.generate_qr: could not load TenantConfig: %s", exc
            )

        # ----------------------------------------------------------------
        # Step 4: Render QR image
        # ----------------------------------------------------------------
        scan_url = f"https://{subdomain}/scan/{new_token}/"
        style = _get_qr_style(qr_design_template)

        qr_image_obj = qrcode.QRCode(
            version=None,  # auto-determined by data length
            error_correction=style["error_correction"],
            box_size=style["box_size"],
            border=style["border"],
        )
        qr_image_obj.add_data(scan_url)
        qr_image_obj.make(fit=True)

        pil_image = qr_image_obj.make_image(fill_color="black", back_color="white")

        # Render to an in-memory PNG buffer
        image_buffer = io.BytesIO()
        pil_image.save(image_buffer, format="PNG")
        image_buffer.seek(0)

        # ----------------------------------------------------------------
        # Step 5: Save QR image to local storage (served via /media/)
        # ----------------------------------------------------------------
        prefix = "room-codes" if is_room else "qr-codes"
        object_name = f"{prefix}/{branch_id}/{location.pk}/{new_token}.png"

        try:
            stored_name = default_storage.save(object_name, image_buffer)
            image_url = default_storage.url(stored_name)
            logger.info(
                "QRService.generate_qr: saved QR image as '%s'",
                stored_name,
            )
        except Exception as exc:
            # Non-fatal: persist the QRCode record even if the save fails.
            # The image_url will remain empty; staff can regenerate.
            logger.error(
                "QRService.generate_qr: local save failed for QRCode %s: %s",
                qr_code.pk,
                exc,
            )
            image_url = ""

        # ----------------------------------------------------------------
        # Step 6: Persist image_url
        # ----------------------------------------------------------------
        qr_code.image_url = image_url
        qr_code.save(update_fields=["image_url"])

        logger.info(
            "QRService.generate_qr: QRCode %s generated successfully (image_url=%r)",
            qr_code.pk,
            image_url,
        )

        return qr_code

    def validate_qr(self, token: uuid.UUID) -> QRScanResult:
        """
        Validate a QR code token and return the associated location info.

        Looks up the QRCode by token.  If the record exists and is_active=True,
        returns a ``QRScanResult`` with the branch and either table or room.
        Otherwise raises ``QRCodeInvalid``.

        This is called by the customer-facing session endpoint when a customer
        scans a QR code.

        Args:
            token: The UUID token extracted from the scan URL.

        Returns:
            A ``QRScanResult`` for the valid, active QR code.

        Raises:
            QRCodeInvalid: If the token is unknown or the matching QRCode is
                           inactive (e.g. it was superseded by regeneration).

        Requirements: 14.3, 14.4
        """
        try:
            qr_code = QRCode.objects.select_related(
                "table",
                "table__branch",
                "room",
                "room__branch",
            ).get(token=token)
        except QRCode.DoesNotExist:
            logger.warning(
                "QRService.validate_qr: token %s not found", token
            )
            raise QRCodeInvalid(
                f"QR code token '{token}' does not exist."
            )

        if not qr_code.is_active:
            logger.warning(
                "QRService.validate_qr: token %s is inactive (QRCode %s)",
                token,
                qr_code.pk,
            )
            raise QRCodeInvalid(
                "This QR code has been deactivated. Please ask staff for a new code."
            )

        if qr_code.table:
            table = qr_code.table
            branch = table.branch
            result = QRScanResult(branch=branch, table=table)
            logger.debug(
                "QRService.validate_qr: token %s is valid → branch=%s, table=%s",
                token, branch.pk, table.pk,
            )
        elif qr_code.room:
            room = qr_code.room
            branch = room.branch
            result = QRScanResult(branch=branch, room=room)
            logger.debug(
                "QRService.validate_qr: token %s is valid → branch=%s, room=%s",
                token, branch.pk, room.pk,
            )
        else:
            raise QRCodeInvalid("QR code is not linked to any location.")

        return result
