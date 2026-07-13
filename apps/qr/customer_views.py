"""
qr/customer_views.py

Customer-facing views for QR scan session creation, menu browsing,
order placement, and order status polling.

Permission matrix (Requirement 4.2):
  - CustomerSessionView:
      POST (create session from QR token)  → AllowAny
        Validates QR token and creates a Django session.
        No existing customer session is required to call this endpoint.
  - CustomerMenuView:
      GET (browse active menu for branch)  → IsCustomerSession
  - CustomerOrderView:
      POST (place order)                   → IsCustomerSession
      GET  (own order status)              → IsCustomerSession

Requirements: 4.1, 4.2, 4.3, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.10, 14.11, 3.7
"""

import logging
import uuid

from django.shortcuts import redirect, render
from django.utils.translation import get_language
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.qr.exceptions import QRCodeInvalid
from apps.qr.services import QRService
from shared.permissions import (
    AuditLogMixin,
    IsCustomerSession,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serializers for customer-facing menu responses
# ---------------------------------------------------------------------------

class CustomerNutritionSerializer(drf_serializers.Serializer):
    """
    Serializes NutritionProfile fields for the customer menu API.
    Requirements: 14.5
    """
    calories_kcal = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    protein_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    carbs_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    fat_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    saturated_fat_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    sugar_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    sodium_mg = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    fibre_g = drf_serializers.DecimalField(
        max_digits=7, decimal_places=2, allow_null=True, read_only=True
    )
    allergens = drf_serializers.ListField(
        child=drf_serializers.CharField(), read_only=True
    )


class CustomerMenuItemSerializer(drf_serializers.Serializer):
    """
    Serializes MenuItem for the customer-facing menu API.

    Fields returned per Requirement 14.5:
        id, name, description, image (URL), price, prep_time_minutes,
        dietary_tags, nutrition (nested), categories (list of names)

    Requirements: 14.5, 14.6, 14.11
    """
    id = drf_serializers.UUIDField(read_only=True)
    name = drf_serializers.CharField(read_only=True)
    name_translated = drf_serializers.SerializerMethodField()
    description = drf_serializers.CharField(read_only=True)
    description_translated = drf_serializers.SerializerMethodField()
    image_url = drf_serializers.SerializerMethodField()
    price = drf_serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    prep_time_minutes = drf_serializers.IntegerField(read_only=True)
    status = drf_serializers.CharField(read_only=True)
    dietary_tags = drf_serializers.ListField(
        child=drf_serializers.CharField(), read_only=True
    )
    categories = drf_serializers.SerializerMethodField()
    nutrition = drf_serializers.SerializerMethodField()
    # Kept for backwards-compatibility with existing TC-Q01 test assertions
    branch_id = drf_serializers.UUIDField(read_only=True)

    def get_image_url(self, obj) -> str | None:
        """Return the best available image URL.

        Priority:
        1. external_image_url (Cloudinary CDN URL) — set by the frontend upload flow
        2. image field (local filesystem or R2) — set by multipart upload
        """
        # 1. Cloudinary / external CDN URL
        if getattr(obj, "external_image_url", None):
            return obj.external_image_url

        # 2. Local / R2 ImageField
        if obj.image and obj.image.name:
            try:
                return obj.image.url
            except Exception:
                pass
        return None

    def get_name_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.name_am:
            return obj.name_am
        return obj.name

    def get_description_translated(self, obj) -> str:
        lang = get_language()
        if lang == "am" and obj.description_am:
            return obj.description_am
        return obj.description

    def get_categories(self, obj):
        """Return list of category names."""
        lang = get_language()
        return [getattr(cat, "name_am", "") if lang == "am" and cat.name_am else cat.name for cat in obj.categories.all()]

    def get_nutrition(self, obj):
        """Return nested nutrition data if a NutritionProfile exists."""
        try:
            nutrition = obj.nutrition
        except Exception:
            return None
        if nutrition is None:
            return None
        return CustomerNutritionSerializer(nutrition).data


# ---------------------------------------------------------------------------
# 16.1 — CustomerSessionView
# POST /api/v1/customer/session/
# GET  /qr/scan/<token>/ (browser route — renders branded HTML on invalid token)
# ---------------------------------------------------------------------------


class CustomerSessionView(AuditLogMixin, APIView):
    """
    POST /api/v1/customer/session/

    Create an anonymous customer session from a valid QR code token.
    No prior session is required (this IS the session creation endpoint).

    Request body:
        {"token": "<uuid>"}

    On success (200):
        Session cookie is set with ``customer_session`` containing
        ``{"branch_id": "...", "table_id": "...", "table_number": "..."}``.
        Returns:
            {
              "session_id": "<session_key>",
              "branch_id": "...",
              "table_id": "...",
              "table_number": "..."
            }

    On invalid/inactive token (404):
        Returns ``{"error": "QR_CODE_INVALID", "message": "..."}`` (Req 14.4).

    Allowed: Anyone — permission is the token validation itself.

    Requirements: 3.7, 14.2, 14.3, 14.4
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No session needed to create a session

    def post(self, request):
        """Validate QR token and initialise a customer session."""
        token_raw = request.data.get("token") if hasattr(request, "data") else None
        if not token_raw:
            return Response(
                {
                    "error": "QR_CODE_INVALID",
                    "message": "A QR token is required.",
                    # kept for backward-compat with older tests expecting "code"
                    "code": "QR_CODE_INVALID",
                    "detail": "A QR token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse UUID
        try:
            token = uuid.UUID(str(token_raw))
        except (ValueError, AttributeError):
            return Response(
                {
                    "error": "QR_CODE_INVALID",
                    "message": "Invalid QR code token format.",
                    "code": "QR_CODE_INVALID",
                    "detail": "Invalid QR code token format.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate via QRService
        service = QRService()
        try:
            scan_result = service.validate_qr(token)
        except QRCodeInvalid as exc:
            logger.info("CustomerSessionView: invalid QR token %s: %s", token, exc)
            return Response(
                {
                    "error": "QR_CODE_INVALID",
                    "message": (
                        "This QR code is invalid or has expired. "
                        "Please ask staff for a new code."
                    ),
                    # backward-compat keys
                    "code": "QR_CODE_INVALID",
                    "detail": (
                        "This QR code is no longer valid. "
                        "Please ask a staff member for a new QR code."
                    ),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Enforce session independence (Requirements 15.5, 15.6):
        # Each QR scan creates a completely fresh session. We flush any
        # existing session first so that:
        #   - no prior session data is copied to the new session
        #   - no cross-session tracking is possible via a reused session key
        # flush() deletes the old session from the backend and generates a
        # new session key — the response will set a brand-new session cookie.
        request.session.flush()

        # Persist session data (Requirement 3.7 — anonymous session scoped to
        # tenant, branch, and table/room; no account required).
        session_data = {
            "branch_id": str(scan_result.branch.id),
        }
        if scan_result.table:
            session_data["table_id"] = str(scan_result.table.id)
            session_data["table_number"] = scan_result.table.number
        if scan_result.room:
            session_data["room_id"] = str(scan_result.room.id)
            session_data["room_name"] = scan_result.room.name

        request.session["customer_session"] = session_data
        request.session.modified = True
        # Save explicitly so that session_key is assigned before we read it
        # (the DB backend only generates a key on the first save/create).
        request.session.save()

        response_data = {
            "session_id": request.session.session_key,
            "branch_id": str(scan_result.branch.id),
            "location_type": scan_result.location_type,
            "location_name": scan_result.location_name,
            "status": "ok",
        }
        if scan_result.table:
            response_data["table_id"] = str(scan_result.table.id)
            response_data["table_number"] = scan_result.table.number
        if scan_result.room:
            response_data["room_id"] = str(scan_result.room.id)
            response_data["room_name"] = scan_result.room.name

        logger.info(
            "CustomerSessionView: session created for branch=%s %s=%s",
            scan_result.branch.id,
            scan_result.location_type,
            scan_result.location_name,
        )

        return Response(response_data, status=status.HTTP_200_OK)


class QRScanView(APIView):
    """
    GET /qr/scan/<token>/

    Browser-facing QR scan entry point. When a customer scans the QR code
    with their phone camera, this URL is opened directly in the browser.

    On valid token:
        Creates the session and redirects to the customer menu page
        /customer/menu/.

    On invalid/inactive token:
        Renders the branded ``customer/qr_invalid.html`` error template
        with code QR_CODE_INVALID (Requirement 14.4).

    Requirements: 3.7, 14.2, 14.4
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        """Handle browser QR scan — validate token and redirect or show error."""
        # Parse the UUID token from URL
        try:
            token_uuid = uuid.UUID(str(token))
        except (ValueError, AttributeError):
            return self._render_invalid(request, "Invalid QR code format.")

        service = QRService()
        try:
            scan_result = service.validate_qr(token_uuid)
        except QRCodeInvalid:
            return self._render_invalid(
                request,
                "This QR code is invalid or has expired. "
                "Please ask a staff member for a new code.",
            )

        # Enforce session independence (Requirements 15.5, 15.6):
        # Flush any prior session before creating the new one so that no
        # prior session data leaks into the new customer context.
        request.session.flush()

        # Store session data so the menu page works immediately
        session_data = {
            "branch_id": str(scan_result.branch.id),
        }
        if scan_result.table:
            session_data["table_id"] = str(scan_result.table.id)
            session_data["table_number"] = scan_result.table.number
        if scan_result.room:
            session_data["room_id"] = str(scan_result.room.id)
            session_data["room_name"] = scan_result.room.name

        request.session["customer_session"] = session_data
        request.session.modified = True

        # Redirect to the customer menu browser page
        from django.shortcuts import redirect
        return redirect("/customer/menu/")

    def _render_invalid(self, request, message: str):
        """Render the branded invalid-QR error page."""
        # Load TenantConfig for branding (best-effort)
        config = _get_tenant_config()
        context = {
            "error_code": "QR_CODE_INVALID",
            "error_message": message,
            "config": config,
        }
        return render(request, "customer/qr_invalid.html", context, status=404)


class CustomerMenuPageView(APIView):
    """
    GET /customer/menu/

    Browser route for the customer-facing digital menu page.
    Renders the Bootstrap 5 / HTMX menu template with initial context.

    Requires an active customer session (set via QR scan).
    If no session exists, redirects to a generic error page.

    Requirements: 14.2, 14.5, 16.1, 16.2, 16.4
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        """Render the digital menu template."""
        session_data = request.session.get("customer_session", {})
        branch_id = session_data.get("branch_id")

        if not branch_id:
            context = {
                "error_code": "SESSION_EXPIRED",
                "error_message": (
                    "Your session has expired. Please scan the QR code again."
                ),
                "config": _get_tenant_config(),
            }
            return render(request, "customer/qr_invalid.html", context, status=403)

        config = _get_tenant_config()
        lang_code = _get_language_code(config)
        text_direction = "ltr"  # Ethiopic script is LTR

        # If customer has a recent active order, show a "Track Your Order" link
        last_order_id = session_data.get("last_order_id")
        if last_order_id:
            try:
                from apps.orders.models import Order
                last_order = Order.objects.get(id=last_order_id)
                if last_order.status in ("confirmed", "received", "preparing", "ready"):
                    pass  # show the link
                else:
                    last_order_id = None  # served/cancelled — hide link
            except Order.DoesNotExist:
                last_order_id = None

        context = {
            "config": config,
            "branch_id": branch_id,
            "table_number": session_data.get("table_number", ""),
            "lang_code": lang_code,
            "text_direction": text_direction,
            "use_amharic": (config.default_language == "am") if config else False,
            "last_order_id": last_order_id,
        }
        from django.views.decorators.cache import never_cache
        response = render(request, "customer/menu.html", context)
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


# ---------------------------------------------------------------------------
# 16.3 — CustomerMenuView
# GET /api/v1/customer/menu/
# ---------------------------------------------------------------------------


class CustomerMenuView(AuditLogMixin, APIView):
    """
    GET /api/v1/customer/menu/

    Return active, non-archived MenuItems for the session's branch.

    Supports optional ``?dietary_tags=vegetarian,vegan`` multi-filter query
    parameter. ALL specified tags must be present on an item for it to appear
    (Requirement 14.6).

    Only returns items with status='available' AND is_archived=False
    (Requirement 14.11).

    Caching (Task 20.2 — Requirement 19.1, 19.2):
        Non-filtered responses are cached in Redis under the key
        ``menu:branch:{branch_id}`` with a 30-second TTL.  Dietary-tag
        filtered requests bypass the cache (they are rare and highly
        specific — caching them would waste Redis memory).

        Cache invalidation happens in two places:
          1. MenuItem post-save signal (apps/menus/signals.py) — clears the
             key whenever a MenuItem is saved or archived.
          2. MenuItemViewSet._invalidate_branch_menu_cache() in menus/views.py
             which is called on every create/update/archive action.

    Allowed: Active customer session (IsCustomerSession).

    Requirements: 4.2, 14.5, 14.6, 14.11, 19.1, 19.2
    """

    permission_classes = [IsCustomerSession]

    # Cache TTL for the full (unfiltered) branch menu — 30 seconds.
    _MENU_CACHE_TTL = 30

    @staticmethod
    def _menu_cache_key(branch_id: str) -> str:
        """Return the Redis cache key for a branch's customer menu."""
        return f"menu:branch:{branch_id}"

    def get(self, request):
        """Return active menu items for the branch stored in the customer session."""
        from django.core.cache import cache as django_cache

        session_data = request.session.get("customer_session", {})
        branch_id = session_data.get("branch_id")

        if not branch_id:
            return Response(
                {"code": "SESSION_INVALID", "detail": "No active customer session found."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Optional dietary tag filtering (Requirement 14.6)
        # Query parameter: ?dietary_tags=vegetarian,vegan
        tags_param = request.query_params.get("dietary_tags", "")
        requested_tags = (
            [t.strip() for t in tags_param.split(",") if t.strip()]
            if tags_param
            else []
        )

        # ----------------------------------------------------------------
        # Cache lookup: only cache the full (unfiltered) menu response.
        # Filtered responses are too specific to be worth caching.
        # ----------------------------------------------------------------
        cache_key = self._menu_cache_key(branch_id)
        if not requested_tags:
            cached_data = django_cache.get(cache_key)
            if cached_data is not None:
                return Response(cached_data, status=status.HTTP_200_OK)

        from apps.menus.models import MenuItem

        # Base queryset: non-archived items for branch (both available and unavailable)
        # The frontend shows unavailable items with a badge so staff changes propagate instantly.
        # Requirement 14.11: archived items are hidden from customers.
        qs = (
            MenuItem.objects.filter(
                branch_id=branch_id,
                is_archived=False,
            )
            .select_related("nutrition")
            .prefetch_related("categories")
            .order_by("name")
        )

        if requested_tags:
            # Try database-level JSON containment first (PostgreSQL).
            # Fall back to in-memory filtering for other backends (SQLite in tests).
            from django.db import connection as db_connection
            vendor = getattr(db_connection, "vendor", "")
            if vendor == "postgresql":
                # PostgreSQL: use efficient JSONField __contains filter
                for tag in requested_tags:
                    qs = qs.filter(dietary_tags__contains=[tag])
            else:
                # SQLite / other: materialise the queryset and filter in Python
                # This is only used in test environments; production uses PostgreSQL.
                items_list = list(qs)
                for tag in requested_tags:
                    items_list = [
                        item for item in items_list
                        if isinstance(item.dietary_tags, list) and tag in item.dietary_tags
                    ]
                serializer = CustomerMenuItemSerializer(items_list, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = CustomerMenuItemSerializer(qs, many=True)
        response_data = serializer.data

        # ----------------------------------------------------------------
        # Cache the full unfiltered menu for 30 seconds (Task 20.2).
        # Dietary-tag filtered results are NOT cached.
        # ----------------------------------------------------------------
        if not requested_tags:
            try:
                django_cache.set(cache_key, response_data, timeout=self._MENU_CACHE_TTL)
            except Exception as cache_exc:
                # Cache write failure must never block the HTTP response.
                logger.warning(
                    "CustomerMenuView: failed to cache menu for branch %s: %s",
                    branch_id,
                    cache_exc,
                )

        return Response(response_data, status=status.HTTP_200_OK)


class CustomerOrderViewSet(AuditLogMixin, viewsets.GenericViewSet):
    """
    POST /api/v1/customer/orders/          — place order
    GET  /api/v1/customer/orders/{id}/status/ — poll own order status

    Allowed: Active customer session (IsCustomerSession)

    Full implementation: Task 17
    Requirements: 4.2, 14.7, 14.8, 14.10
    """

    permission_classes = [IsCustomerSession]

    def get_queryset(self):
        from apps.orders.models import Order
        session = self.request.session.get("customer_session", {})
        branch_id = session.get("branch_id")
        if branch_id:
            return Order.objects.filter(branch_id=branch_id).select_related(
                "table", "room", "branch"
            ).prefetch_related("items__menu_item")
        return Order.objects.none()

    @action(detail=True, methods=["get"], url_path="status")
    def status(self, request, pk=None):
        """GET /api/v1/customer/orders/{id}/status/ — HTTP polling fallback."""
        order = self.get_object()
        return Response(CustomerOrderResponseSerializer(order).data)

    def cancel(self, request, pk=None):
        """POST /api/v1/customer/orders/{id}/cancel/ — cancel own order if confirmed."""
        order = self.get_object()
        if not order.is_valid_transition("cancelled"):
            return Response(
                {"error": "INVALID_TRANSITION", "detail": "Order cannot be cancelled at this stage."},
                status=422,
            )
        previous_status = order.status
        order.status = "cancelled"
        order.save(update_fields=["status"])
        from apps.notifications.utils import push_customer_event, push_staff_roles_event
        ws_payload = {
            "order_id": str(order.id),
            "order_number": order.order_number,
            "previous_status": previous_status,
            "new_status": "cancelled",
            "branch_id": str(order.branch_id),
            "table_number": order.table.number if order.table else None,
            "timestamp": order.placed_at.isoformat(),
        }
        push_staff_roles_event(str(order.branch_id), "order_cancelled", ws_payload, ["kitchen", "reception"])
        push_customer_event(str(order.id), "order_status_changed", ws_payload)
        return Response({"status": "cancelled"})

    def list(self, request):
        """GET /api/v1/customer/orders/ — list active orders for this session."""
        orders = self.get_queryset().exclude(status__in=["served", "cancelled"]).order_by("-placed_at")
        from apps.orders.serializers import OrderSerializer
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Order placement input serializer
# ---------------------------------------------------------------------------

class _OrderItemInputSerializer(drf_serializers.Serializer):
    """
    Validates a single line item in a customer order request.

    Fields:
        menu_item_id         — UUID of the MenuItem to order
        quantity             — Positive integer serving count
        special_instructions — Optional free-text notes (e.g. "no onions")
    """

    menu_item_id = drf_serializers.UUIDField()
    quantity = drf_serializers.IntegerField(min_value=1)
    special_instructions = drf_serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class CustomerOrderInputSerializer(drf_serializers.Serializer):
    """
    Validates the request body for POST /api/v1/customer/orders/.

    Fields:
        items          — Non-empty list of order line items
        customer_name  — Optional customer name (never required — Req 14.9)
        customer_phone — Optional customer phone (never required — Req 14.9)
    """

    items = drf_serializers.ListField(
        child=_OrderItemInputSerializer(),
        min_length=1,
        error_messages={"min_length": "An order must contain at least one item."},
    )
    customer_name = drf_serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    customer_phone = drf_serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class CustomerOrderResponseSerializer(drf_serializers.Serializer):
    """
    Response shape for a successfully placed order.
    """

    id = drf_serializers.UUIDField(read_only=True)
    order_number = drf_serializers.CharField(read_only=True)
    status = drf_serializers.CharField(read_only=True)
    total_amount = drf_serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    placed_at = drf_serializers.DateTimeField(read_only=True)
    table_number = drf_serializers.SerializerMethodField()
    room_name = drf_serializers.SerializerMethodField()
    location_type = drf_serializers.SerializerMethodField()
    customer_name = drf_serializers.CharField(read_only=True)
    customer_phone = drf_serializers.CharField(read_only=True)
    items = drf_serializers.SerializerMethodField()

    def get_table_number(self, obj):
        return obj.table.number if obj.table else None

    def get_room_name(self, obj):
        return obj.room.name if obj.room else None

    def get_location_type(self, obj):
        return "room" if obj.room_id else "table"

    def get_items(self, obj):
        return [
            {
                "id": str(item.id),
                "menu_item_id": str(item.menu_item_id),
                "menu_item_name": item.menu_item.name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "special_instructions": item.special_instructions,
            }
            for item in obj.items.select_related("menu_item").all()
        ]


# ---------------------------------------------------------------------------
# Task 17.1 — CustomerOrderCreateView
# POST /api/v1/customer/orders/
# ---------------------------------------------------------------------------


class CustomerOrderCreateView(AuditLogMixin, APIView):
    """
    POST /api/v1/customer/orders/

    Place a new order from the active customer session.

    Request body::

        {
          "items": [
            {
              "menu_item_id": "<uuid>",
              "quantity": <int>,
              "special_instructions": "<str>"   // optional
            }
          ],
          "customer_name":  "<str>",   // optional — never required (Req 14.9)
          "customer_phone": "<str>"    // optional — never required (Req 14.9)
        }

    Processing steps (Requirement 14.7, 14.8, 14.9):
      1. Validate all items have status='available' and is_archived=False.
         Return 422 ITEM_UNAVAILABLE if any item fails this check.
      2. Snapshot unit_price from MenuItem.price at placement time (Req 14.8).
      3. Compute total_amount as sum(unit_price × quantity) across all items.
      4. Persist Order with status='confirmed' and all OrderItems.
      5. Enqueue send_order_notification Celery task (Req 17.1).

    Returns:
        201 — serialized created Order with all items and computed total.
        400 — validation error (malformed payload).
        401 — no active customer session.
        422 — ITEM_UNAVAILABLE if any item is not orderable.

    Requirements: 14.7, 14.8, 14.9
    """

    permission_classes = [IsCustomerSession]

    def post(self, request):
        """Handle order placement from an active customer session."""
        # -- 1. Validate session ------------------------------------------
        session_data = request.session.get("customer_session", {})
        branch_id = session_data.get("branch_id")
        table_id = session_data.get("table_id")
        room_id = session_data.get("room_id")

        if not branch_id:
            return Response(
                {"error": "SESSION_INVALID", "detail": "No active customer session found."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not table_id and not room_id:
            return Response(
                {"error": "SESSION_INVALID", "detail": "No location (table or room) in session."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # -- 2. Validate input payload ------------------------------------
        input_serializer = CustomerOrderInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {"error": "VALIDATION_ERROR", "detail": input_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated = input_serializer.validated_data
        items_data = validated["items"]
        customer_name = validated.get("customer_name", "")
        customer_phone = validated.get("customer_phone", "")

        # -- 3. Look up MenuItems and validate availability ---------------
        from decimal import Decimal

        from apps.branches.models import Branch, Room, Table
        from apps.menus.models import MenuItem
        from apps.orders.models import Order, OrderItem

        # Collect all requested menu_item_ids for a single DB query.
        menu_item_ids = [str(item["menu_item_id"]) for item in items_data]

        # Fetch only items scoped to the session's branch.
        menu_items_qs = MenuItem.objects.filter(
            id__in=menu_item_ids,
            branch_id=branch_id,
        ).select_related("recipe").prefetch_related(
            "recipe__ingredients__inventory_item",
        )
        menu_items_by_id = {str(mi.id): mi for mi in menu_items_qs}

        # Validate each requested item.
        unavailable_items = []
        out_of_stock_items = []
        for item_input in items_data:
            mid = str(item_input["menu_item_id"])
            qty = item_input["quantity"]
            menu_item = menu_items_by_id.get(mid)

            if menu_item is None:
                unavailable_items.append(mid)
                continue

            # Requirement 14.7 / 14.11: only available & not archived items.
            if menu_item.status != "available" or menu_item.is_archived:
                unavailable_items.append(mid)
                continue

            # Pre-order inventory check: verify stock for all recipe ingredients
            try:
                recipe = menu_item.recipe
            except Exception:
                continue
            if recipe is not None:
                for ingredient in recipe.ingredients.all():
                    inv = ingredient.inventory_item
                    required = ingredient.quantity * qty
                    if inv.quantity < required:
                        out_of_stock_items.append({
                            "menu_item_id": mid,
                            "name": menu_item.name,
                            "ingredient": inv.name,
                            "available": float(inv.quantity),
                            "required": float(required),
                        })
                        break

        if unavailable_items:
            return Response(
                {
                    "error": "ITEM_UNAVAILABLE",
                    "detail": "One or more items are not available for ordering.",
                    "unavailable_item_ids": unavailable_items,
                },
                status=422,
            )

        if out_of_stock_items:
            names = ", ".join(i["name"] for i in out_of_stock_items)
            return Response(
                {
                    "error": "OUT_OF_STOCK",
                    "detail": f"Insufficient stock for: {names}.",
                    "out_of_stock_items": out_of_stock_items,
                },
                status=422,
            )

        # -- 4. Fetch Location (Table or Room) for FK -----------------------
        location_type = "room" if room_id else "table"
        location_model = Room if location_type == "room" else Table
        location_id = room_id or table_id
        try:
            location = location_model.objects.get(id=location_id, branch_id=branch_id)
        except location_model.DoesNotExist:
            return Response(
                {
                    "error": "SESSION_INVALID",
                    "detail": f"Session references an unknown {location_type}. Please scan the QR code again.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            branch = Branch.objects.get(id=branch_id)
        except Branch.DoesNotExist:
            return Response(
                {
                    "error": "SESSION_INVALID",
                    "detail": "Session references an unknown branch.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # -- 5. Compute total_amount (price snapshot × quantity) --------
        total_amount = Decimal("0.00")
        line_items = []
        for item_input in items_data:
            mid = str(item_input["menu_item_id"])
            menu_item = menu_items_by_id[mid]
            qty = item_input["quantity"]
            unit_price = menu_item.price  # snapshot at placement time (Req 14.8)
            line_items.append(
                {
                    "menu_item": menu_item,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "special_instructions": item_input.get("special_instructions", ""),
                }
            )
            total_amount += unit_price * qty

        # -- 6. Persist Order and OrderItems (atomic) -------------------
        from django.db import transaction

        order_kw = {
            "branch": branch,
            "status": "confirmed",
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "total_amount": total_amount,
        }
        if location_type == "room":
            order_kw["room"] = location
        else:
            order_kw["table"] = location

        with transaction.atomic():
            order = Order.objects.create(**order_kw)

            for line in line_items:
                OrderItem.objects.create(
                    order=order,
                    menu_item=line["menu_item"],
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    special_instructions=line["special_instructions"],
                )

        logger.info(
            "Order %s placed: branch=%s %s=%s total=%s items=%d",
            order.order_number,
            branch_id,
            location_type,
            location_id,
            total_amount,
            len(line_items),
        )

        # -- 7. Enqueue notification task (non-blocking) ----------------
        try:
            from apps.notifications.tasks import send_order_notification
            send_order_notification.delay(str(order.id))
        except Exception as task_exc:
            # Never let task-queuing failure block the order response.
            logger.warning(
                "Failed to enqueue send_order_notification for order %s: %s",
                order.id,
                task_exc,
            )

        # -- 8. Store last_order_id in session so the menu page can show
        #         a "Track Your Order" link returning customers can follow.
        session_data["last_order_id"] = str(order.id)
        request.session["customer_session"] = session_data
        request.session.save()

        # -- 9. Return created order ----------------------------------------
        response_serializer = CustomerOrderResponseSerializer(order)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Task 17.5 — CartPageView
# GET /customer/cart/
# ---------------------------------------------------------------------------


class CartPageView(APIView):
    """
    GET /customer/cart/

    Browser route for the customer-facing cart review and order confirmation
    page.  Renders the Bootstrap 5 / HTMX cart template.

    The cart contents live in sessionStorage on the client (written by the
    menu page's addToCart() function).  This view simply provides the server-
    rendered shell; the JavaScript on the page reads sessionStorage and
    populates the item list client-side.

    Requires an active customer session.  If no session exists, redirects to
    the branded error page.

    Requirements: 14.7
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        """Render the cart / order-confirmation template."""
        session_data = request.session.get("customer_session", {})
        branch_id = session_data.get("branch_id")

        if not branch_id:
            context = {
                "error_code": "SESSION_EXPIRED",
                "error_message": (
                    "Your session has expired. Please scan the QR code again."
                ),
                "config": _get_tenant_config(),
            }
            return render(request, "customer/qr_invalid.html", context, status=403)

        config = _get_tenant_config()
        lang_code = _get_language_code(config)

        last_order_id = session_data.get("last_order_id")
        context = {
            "config": config,
            "branch_id": branch_id,
            "table_number": session_data.get("table_number", ""),
            "lang_code": lang_code,
            "text_direction": "ltr",
            "use_amharic": (config.default_language == "am") if config else False,
            "last_order_id": last_order_id,
        }
        return render(request, "customer/cart.html", context)


# ---------------------------------------------------------------------------
# Task 17.5 — OrderTrackerPageView
# GET /customer/order/<order_id>/
# ---------------------------------------------------------------------------


class OrderTrackerPageView(APIView):
    """
    GET /customer/order/<order_id>/

    Browser route for the live order tracker page.  Renders the Bootstrap 5
    / WebSocket order tracker template.

    Attempts to load the order from the database to pre-populate the tracker
    with the order number, status, and items.  Falls back gracefully if the
    order cannot be found (the JS polling/WebSocket will still work).

    Requirements: 14.7
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, order_id):
        """Render the order tracker template."""
        config = _get_tenant_config()
        lang_code = _get_language_code(config)
        session_data = request.session.get("customer_session", {})

        # Attempt to load order data for initial render (best-effort)
        order = None
        order_items = []
        order_status = "confirmed"
        order_number = ""
        order_total = None
        order_status_display = "Confirmed"

        STATUS_DISPLAY = {
            "confirmed": "Confirmed",
            "received": "Received",
            "preparing": "Preparing",
            "ready": "Ready",
            "served": "Served",
            "cancelled": "Cancelled",
        }

        try:
            from apps.orders.models import Order
            order = Order.objects.prefetch_related(
                "items__menu_item"
            ).get(id=order_id)
            order_status = order.status
            order_number = order.order_number
            order_total = order.total_amount
            order_status_display = STATUS_DISPLAY.get(order.status, order.status.title())

            # Build a serialisable items list for the template
            for item in order.items.select_related("menu_item").all():
                order_items.append(
                    {
                        "menu_item_name": item.menu_item.name,
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                        "special_instructions": item.special_instructions,
                    }
                )
        except Exception:
            # If order lookup fails, render with minimal context — the
            # WebSocket / polling will update the status dynamically.
            pass

        context = {
            "config": config,
            "lang_code": lang_code,
            "text_direction": "ltr",
            "use_amharic": (config.default_language == "am") if config else False,
            "order_id": str(order_id),
            "order_number": order_number,
            "order_status": order_status,
            "order_status_display": order_status_display,
            "order_items": order_items,
            "order_total": order_total,
            "table_number": (
                order.table.number
                if order and order.table
                else session_data.get("table_number", "")
            ),
        }
        return render(request, "customer/order_tracker.html", context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class CustomerCartAPIView(APIView):
    """
    Server-side session cart — persists across page navigations.

    GET  /api/v1/customer/cart/        → return current cart contents
    POST /api/v1/customer/cart/        → add/update an item
        body: {"item_id": "<uuid>", "name": "...", "price": 12.50, "qty": 1, "instructions": ""}
    DELETE /api/v1/customer/cart/      → clear entire cart
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    CART_SESSION_KEY = "customer_cart"

    def _get_cart(self, request):
        return dict(request.session.get(self.CART_SESSION_KEY, {}))

    def _save_cart(self, request, cart):
        request.session[self.CART_SESSION_KEY] = cart
        request.session.modified = True

    def get(self, request):
        return Response(self._get_cart(request))

    def post(self, request):
        item_id = str(request.data.get("item_id", ""))
        name = str(request.data.get("name", ""))
        price = float(request.data.get("price", 0))
        delta = int(request.data.get("qty", 1))
        instructions = str(request.data.get("instructions", ""))

        if not item_id:
            return Response({"error": "item_id required"}, status=400)

        cart = self._get_cart(request)
        if item_id in cart:
            cart[item_id]["qty"] += delta
            if instructions:
                cart[item_id]["instructions"] = instructions
            if cart[item_id]["qty"] <= 0:
                del cart[item_id]
        else:
            if delta > 0:
                cart[item_id] = {
                    "name": name,
                    "price": price,
                    "qty": delta,
                    "instructions": instructions,
                }

        self._save_cart(request, cart)
        return Response(cart)

    def delete(self, request):
        self._save_cart(request, {})
        return Response({})


# ---------------------------------------------------------------------------
# Test entry — simulate a QR scan for manual testing
# ---------------------------------------------------------------------------


class CustomerTestEntryView(APIView):
    """
    GET /customer/test/

    Simple test page that lists branches, tables, and rooms so you can
    pick one and jump straight into the customer menu without scanning a
    real QR code.

    This page is only intended for **development / demo** environments.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from apps.branches.models import Branch, Room, Table
        from apps.qr.models import QRCode

        branches = Branch.objects.all()
        branch_data = []
        for b in branches:
            tables = Table.objects.filter(branch=b)
            rooms = Room.objects.filter(branch=b)
            qr_tokens = {
                str(t.id): QRCode.objects.filter(table=t, is_active=True)
                .values_list("token", flat=True)
                .first()
                for t in tables
            }
            branch_data.append({
                "id": str(b.id),
                "name": b.name,
                "tables": [
                    {"id": str(t.id), "number": t.number, "qr_token": qr_tokens.get(str(t.id))}
                    for t in tables
                ],
                "rooms": [{"id": str(r.id), "name": r.name} for r in rooms],
            })

        return render(request, "customer/test_entry.html", {
            "branches": branch_data,
            "config": _get_tenant_config(),
        })


    def post(self, request):
        from apps.branches.models import Branch, Room, Table

        branch_id = request.POST.get("branch_id")
        table_id = request.POST.get("table_id")
        room_id = request.POST.get("room_id")

        branch = Branch.objects.filter(id=branch_id).first()
        if not branch:
            return render(request, "customer/test_entry.html", {
                "branches": [],
                "config": _get_tenant_config(),
                "error": "Branch not found.",
            })

        request.session.flush()
        session_data = {"branch_id": str(branch.id)}

        if table_id:
            table = Table.objects.filter(id=table_id, branch=branch).first()
            if table:
                session_data["table_id"] = str(table.id)
                session_data["table_number"] = table.number

        if room_id:
            room = Room.objects.filter(id=room_id, branch=branch).first()
            if room:
                session_data["room_id"] = str(room.id)
                session_data["room_name"] = room.name

        request.session["customer_session"] = session_data
        request.session.modified = True
        return redirect("/customer/menu/")


def _get_tenant_config():
    """
    Load TenantConfig for the current tenant (best-effort).
    Returns None if not available (e.g. no TenantConfig record exists yet).
    """
    try:
        from apps.whitelabel.models import TenantConfig
        return TenantConfig.objects.first()
    except Exception:
        return None


def _get_language_code(config) -> str:
    """Return the language code to use, defaulting to 'en'."""
    if config and config.default_language:
        return config.default_language
    return "en"
