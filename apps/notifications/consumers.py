"""
notifications/consumers.py — Django Channels WebSocket consumers.

Consumers implement real-time WebSocket communication between the server and
authenticated staff/customer clients.

Channel group membership:
  - branch_{branch_id}_kitchen   → KitchenConsumer    (Kitchen_Staff)
  - branch_{branch_id}_reception → ReceptionConsumer  (Receptionist)
  - branch_{branch_id}_manager   → ManagerConsumer    (Branch_Manager / Tenant_Owner)
  - order_{order_id}_customer    → CustomerOrderConsumer (Customer session)
  - branch_{branch_id}_inventory → InventoryConsumer  (Branch_Manager)

Authentication:
  - Staff consumers: require an authenticated Django session with the correct role.
    The outer AuthMiddlewareStack (see config/asgi.py) populates scope["user"]
    from the session cookie before the consumer's connect() is called.
  - Customer consumer: requires an active customer session stored in the Django
    session under the "customer_session" key (set by POST /api/v1/customer/session/).

Close codes:
  - 4001  Unauthenticated (no valid session / anonymous user)
  - 4003  Forbidden (authenticated but wrong role or branch/order mismatch)
  - 4004  Not Found (order ID not found for CustomerOrderConsumer)

Message type handlers follow Django Channels convention: a ``type`` field of
``"order.new"`` routes to a method named ``order_new``.  The dot is replaced
with an underscore.

Requirements: 17.1, 17.2, 17.3, 17.4
"""

from __future__ import annotations

import logging
from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from apps.authentication.models import UserRole
from apps.observability.metrics import websocket_connections_active

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base authenticated consumer
# ---------------------------------------------------------------------------

class _AuthenticatedConsumer(AsyncJsonWebsocketConsumer):
    """
    Abstract base for staff WebSocket consumers.

    Subclasses must define:
        allowed_roles  — tuple of UserRole values that may connect
        group_name()   — return the channel group name for this connection

    connect() flow:
        1. Retrieve scope["user"] populated by AuthMiddlewareStack.
        2. Reject with 4001 if the user is unauthenticated.
        3. Reject with 4003 if the user's role is not in allowed_roles.
        4. Validate branch scope (subclass hook: _check_scope()).
        5. Add to channel group; accept the WebSocket upgrade.

    disconnect() flow:
        Remove from channel group.
    """

    allowed_roles: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Abstract helpers — subclasses must implement
    # ------------------------------------------------------------------

    def group_name(self) -> str:
        """Return the channel group name for this consumer instance."""
        raise NotImplementedError  # pragma: no cover

    async def _check_scope(self, user) -> bool:
        """
        Optional additional scope check (e.g. branch membership).

        Return True to allow, False to close with 4003.
        Subclasses override this to perform async ORM checks.
        """
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _auth_from_session_key(self, session_key: str):
        """Look up user by session key (sync DB, run in thread via sync_to_async).

        Also resolves the tenant from the Host header and sets the schema
        context so the User model can be queried in the correct tenant schema.
        """
        from asgiref.sync import sync_to_async

        def _load_user():
            from importlib import import_module
            from django.contrib.auth import get_user_model
            from django_tenants.utils import schema_context

            # 1. Resolve tenant from the Host header (same logic as TenantMiddleware)
            hostname = None
            for hname, hvalue in self.scope.get("headers", []):
                if hname == b"host":
                    hostname = hvalue.decode().split(":")[0]  # strip port
                    break

            if hostname:
                from django_tenants.utils import get_tenant_domain_model
                DomainModel = get_tenant_domain_model()
                try:
                    domain = DomainModel.objects.select_related("tenant").get(
                        domain=hostname
                    )
                    tenant = domain.tenant
                except DomainModel.DoesNotExist:
                    tenant = None
            else:
                tenant = None

            # 2. Load session (always in public schema)
            engine = import_module(settings.SESSION_ENGINE)
            session = engine.SessionStore(session_key)
            if not session.load():
                return None
            user_id = session.get("_auth_user_id")
            if not user_id:
                return None

            # 3. Look up user in the tenant schema
            User = get_user_model()
            try:
                if tenant:
                    with schema_context(tenant.schema_name):
                        return User.objects.get(id=user_id)
                return User.objects.get(id=user_id)
            except User.DoesNotExist:
                return None

        return await sync_to_async(_load_user)()

    async def connect(self):
        user = self.scope.get("user")

        # 1. Cookie-based auth may fail when SameSite=Strict blocks the cookie
        #    on WebSocket upgrades.  Fall back to explicit sessionid in the
        #    query string as a workaround.
        if user is None or not user.is_authenticated:
            qs_session = self.scope.get("query_string", b"").decode()
            logger.info("Auth fallback: query_string=%r user=%s", qs_session, user)
            if qs_session.startswith("sessionid="):
                session_key = qs_session[len("sessionid="):]
                logger.info("Auth fallback: extracted session_key=%s", session_key)
                if session_key:
                    user = await self._auth_from_session_key(session_key)
                    logger.info("Auth fallback: resolved user=%s", user)
                    if user:
                        self.scope["user"] = user

        if user is None or not user.is_authenticated:
            logger.warning(
                "%s rejected unauthenticated WebSocket connection from %s",
                self.__class__.__name__,
                self.scope.get("client"),
            )
            await self.close(code=4001)
            return

        # 2. Reject wrong role
        if self.allowed_roles and user.role not in self.allowed_roles:
            logger.warning(
                "%s rejected connection from user %s (role=%s); allowed=%s",
                self.__class__.__name__,
                user.id,
                user.role,
                self.allowed_roles,
            )
            await self.close(code=4003)
            return

        # 3. Optional scope check implemented by subclass
        if not await self._check_scope(user):
            await self.close(code=4003)
            return

        # 4. Join channel group
        self._group = self.group_name()
        await self.channel_layer.group_add(self._group, self.channel_name)

        await self.accept()
        websocket_connections_active().inc()
        logger.info(
            "%s accepted: user=%s role=%s group=%s",
            self.__class__.__name__,
            user.id,
            user.role,
            self._group,
        )

    async def disconnect(self, code):
        group = getattr(self, "_group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)
        websocket_connections_active().dec()
        logger.debug(
            "%s disconnected: code=%s group=%s",
            self.__class__.__name__,
            code,
            group,
        )

    # ------------------------------------------------------------------
    # Receive from client (forward to channel group)
    # ------------------------------------------------------------------

    async def receive_json(self, content: dict[str, Any], **kwargs):
        """
        Forward any message sent by the client to the channel group.

        Staff UIs (KDS, reception dashboard, manager) may send messages back
        to the server — e.g. optimistic status-update acknowledgements.
        Those messages are broadcast to the group so all connected tabs see them.
        """
        group = getattr(self, "_group", None)
        if group:
            await self.channel_layer.group_send(group, content)

    # ------------------------------------------------------------------
    # Channel group event handlers
    # ------------------------------------------------------------------

    async def order_new(self, event: dict[str, Any]):
        """Handle ``type: "order.new"`` pushed to the channel group."""
        await self.send_json(event)

    async def order_status_changed(self, event: dict[str, Any]):
        """Handle ``type: "order_status_changed"`` pushed to the channel group."""
        await self.send_json(event)

    async def order_cancelled(self, event: dict[str, Any]):
        """Handle ``type: "order_cancelled"`` pushed to the channel group."""
        await self.send_json(event)

    async def inventory_alert(self, event: dict[str, Any]):
        """Handle ``type: "inventory_alert"`` pushed to the channel group."""
        await self.send_json(event)

    async def report_ready(self, event: dict[str, Any]):
        """Handle ``type: "report_ready"`` pushed to the channel group."""
        await self.send_json(event)

    async def low_stock(self, event: dict[str, Any]):
        """Handle ``type: "low_stock"`` pushed to the channel group."""
        await self.send_json(event)

    async def expiry_warning(self, event: dict[str, Any]):
        """Handle ``type: "expiry_warning"`` pushed to the channel group."""
        await self.send_json(event)

    async def out_of_stock(self, event: dict[str, Any]):
        """Handle ``type: "out_of_stock"`` pushed to the channel group."""
        await self.send_json(event)


# ---------------------------------------------------------------------------
# KitchenConsumer
# ---------------------------------------------------------------------------

class KitchenConsumer(_AuthenticatedConsumer):
    """
    WebSocket consumer for the Kitchen Display System (KDS).

    URL: ws/kitchen/

    Channel group: ``branch_{branch_id}_kitchen``
    Role: Kitchen_Staff (also accepts Branch_Manager for monitoring)

    Events delivered:
      - new_order         (type: "order.new")
      - order_cancelled   (type: "order_cancelled")

    The branch_id is derived from the authenticated user's assigned branch.

    Requirements: 17.1, 17.3, 17.4
    """

    allowed_roles = (
        UserRole.KITCHEN_STAFF,
        UserRole.BRANCH_MANAGER,
        UserRole.TENANT_OWNER,
        UserRole.SUPER_ADMIN,
    )

    def group_name(self) -> str:
        user = self.scope["user"]
        branch_id = str(user.branch_id) if user.branch_id else "none"
        return f"branch_{branch_id}_kitchen"

    async def _check_scope(self, user) -> bool:
        # Kitchen Staff and Branch Manager must have an assigned branch.
        if user.role in (UserRole.KITCHEN_STAFF, UserRole.BRANCH_MANAGER):
            if not user.branch_id:
                logger.warning(
                    "KitchenConsumer: user %s (role=%s) has no branch assigned",
                    user.id,
                    user.role,
                )
                return False
        return True


# ---------------------------------------------------------------------------
# ReceptionConsumer
# ---------------------------------------------------------------------------

class ReceptionConsumer(_AuthenticatedConsumer):
    """
    WebSocket consumer for the Reception dashboard.

    URL: ws/reception/

    Channel group: ``branch_{branch_id}_reception``
    Role: Receptionist (also accepts Branch_Manager)

    Events delivered:
      - new_order               (type: "order.new")
      - order_status_changed    (type: "order_status_changed")

    Requirements: 17.1, 17.2, 17.3, 17.4
    """

    allowed_roles = (
        UserRole.RECEPTIONIST,
        UserRole.BRANCH_MANAGER,
        UserRole.TENANT_OWNER,
        UserRole.SUPER_ADMIN,
    )

    def group_name(self) -> str:
        user = self.scope["user"]
        branch_id = str(user.branch_id) if user.branch_id else "none"
        return f"branch_{branch_id}_reception"

    async def _check_scope(self, user) -> bool:
        if user.role in (UserRole.RECEPTIONIST, UserRole.BRANCH_MANAGER):
            if not user.branch_id:
                logger.warning(
                    "ReceptionConsumer: user %s (role=%s) has no branch assigned",
                    user.id,
                    user.role,
                )
                return False
        return True


# ---------------------------------------------------------------------------
# ManagerConsumer
# ---------------------------------------------------------------------------

class ManagerConsumer(_AuthenticatedConsumer):
    """
    WebSocket consumer for the Branch Manager live dashboard.

    URL: ws/manager/

    Channel group: ``branch_{branch_id}_manager``
    Role: Branch_Manager (also accepts Tenant_Owner for cross-branch monitoring)

    Events delivered:
      - new_order         (type: "order.new")
      - inventory_alert   (type: "inventory_alert")
      - report_ready      (type: "report_ready")

    Requirements: 17.3, 17.4, 17.5
    """

    allowed_roles = (
        UserRole.BRANCH_MANAGER,
        UserRole.TENANT_OWNER,
        UserRole.SUPER_ADMIN,
    )

    def group_name(self) -> str:
        user = self.scope["user"]
        branch_id = str(user.branch_id) if user.branch_id else "none"
        return f"branch_{branch_id}_manager"

    async def _check_scope(self, user) -> bool:
        if user.role == UserRole.BRANCH_MANAGER:
            if not user.branch_id:
                logger.warning(
                    "ManagerConsumer: Branch Manager %s has no branch assigned",
                    user.id,
                )
                return False
        return True


# ---------------------------------------------------------------------------
# CustomerMenuConsumer — live menu updates for customers
# ---------------------------------------------------------------------------

class CustomerMenuConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for the customer-facing menu page.

    URL: ws/customer/menu/

    Channel group: ``branch_{branch_id}_menu``

    Authentication:
        Uses the anonymous customer session (no Django User account).
        The session must contain a "customer_session" key with a ``branch_id``
        set by the QR scan endpoint (POST /api/v1/customer/session/).

    Events delivered:
        - menu.item_updated  (type: "menu.item_updated")

    Close codes:
        - 4001  No valid customer session in Django session store
    """

    async def connect(self):
        await self.accept()

        session = self.scope.get("session", {})
        customer_session = session.get("customer_session")

        if not customer_session or not customer_session.get("branch_id"):
            logger.info(
                "CustomerMenuConsumer: no valid customer session; closing"
            )
            await self.close(code=4001)
            return

        branch_id = str(customer_session["branch_id"])
        self._group = f"branch_{branch_id}_menu"
        await self.channel_layer.group_add(self._group, self.channel_name)
        await self.accept()
        websocket_connections_active().inc()

        logger.info(
            "CustomerMenuConsumer accepted: branch_id=%s group=%s",
            branch_id,
            self._group,
        )

    async def disconnect(self, code):
        group = getattr(self, "_group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)
        websocket_connections_active().dec()

    async def receive_json(self, content: dict[str, Any], **kwargs):
        pass

    async def menu_item_updated(self, event: dict[str, Any]):
        """Handle ``type: "menu.item_updated"`` and forward to customer."""
        await self.send_json(event)


# ---------------------------------------------------------------------------
# CustomerOrderConsumer
# ---------------------------------------------------------------------------

class CustomerOrderConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for the Customer live order tracker.

    URL: ws/order/{order_id}/

    Channel group: ``order_{order_id}_customer``

    Authentication:
        Uses an anonymous customer session (no Django User account).
        The session must contain a "customer_session" key set by the QR scan
        endpoint (POST /api/v1/customer/session/).
        The order_id in the URL must match the order associated with this session.

    Events delivered:
      - order_status_changed  (type: "order_status_changed")

    Close codes:
      - 4001  No valid customer session in Django session store
      - 4003  The order_id does not match the customer's session order

    Requirements: 17.2, 17.4
    """

    async def connect(self):
        # Extract order_id from URL route
        self._order_id = self.scope["url_route"]["kwargs"].get("order_id", "")

        # Validate customer session
        session = self.scope.get("session", {})
        customer_session = session.get("customer_session")

        if not customer_session:
            logger.warning(
                "CustomerOrderConsumer: no customer session; rejecting order_id=%s",
                self._order_id,
            )
            await self.close(code=4001)
            return

        # Optionally: verify the order_id matches the customer's session order.
        # The customer session stores the order_id after order placement
        # (set by POST /api/v1/customer/orders/).
        session_order_id = customer_session.get("order_id")
        if session_order_id and str(session_order_id) != str(self._order_id):
            logger.warning(
                "CustomerOrderConsumer: session order %s != URL order %s; rejecting",
                session_order_id,
                self._order_id,
            )
            await self.close(code=4003)
            return

        # Join customer-specific order group
        self._group = f"order_{self._order_id}_customer"
        await self.channel_layer.group_add(self._group, self.channel_name)
        await self.accept()
        websocket_connections_active().inc()

        logger.info(
            "CustomerOrderConsumer accepted: order_id=%s group=%s",
            self._order_id,
            self._group,
        )

    async def disconnect(self, code):
        group = getattr(self, "_group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)
        websocket_connections_active().dec()
        logger.debug(
            "CustomerOrderConsumer disconnected: code=%s group=%s",
            code,
            group,
        )

    async def receive_json(self, content: dict[str, Any], **kwargs):
        # Customers do not send messages to the server via WebSocket;
        # ignore any incoming frames.
        pass

    async def order_status_changed(self, event: dict[str, Any]):
        """Handle ``type: "order_status_changed"`` and forward to the customer."""
        await self.send_json(event)


# ---------------------------------------------------------------------------
# InventoryConsumer
# ---------------------------------------------------------------------------

class InventoryConsumer(_AuthenticatedConsumer):
    """
    WebSocket consumer for real-time inventory alert delivery.

    URL: ws/inventory/  (wired in routing.py as part of Task 17.3)

    Channel group: ``branch_{branch_id}_inventory``
    Role: Branch_Manager, Tenant_Owner (inventory alerts target managers)

    Events delivered:
      - low_stock       (type: "low_stock")
      - expiry_warning  (type: "expiry_warning")
      - out_of_stock    (type: "out_of_stock")

    This consumer corresponds to the ``branch_{branch_id}_inventory`` channel
    group used by ``apps.inventory.tasks.send_inventory_alert`` (Task 12.4).

    Requirements: 11.3, 11.4, 11.5, 17.5
    """

    allowed_roles = (
        UserRole.BRANCH_MANAGER,
        UserRole.TENANT_OWNER,
        UserRole.SUPER_ADMIN,
    )

    def group_name(self) -> str:
        user = self.scope["user"]
        branch_id = str(user.branch_id) if user.branch_id else "none"
        return f"branch_{branch_id}_inventory"

    async def _check_scope(self, user) -> bool:
        if user.role == UserRole.BRANCH_MANAGER:
            if not user.branch_id:
                logger.warning(
                    "InventoryConsumer: Branch Manager %s has no branch assigned",
                    user.id,
                )
                return False
        return True
