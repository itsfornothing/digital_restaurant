"""
shared/middleware.py — Request-ID middleware and thread-local request context.

RequestIdMiddleware:
  - Reads the ``X-Request-ID`` header from the incoming request (or generates
    a fresh UUID4 if the header is absent).
  - Stores the request_id in a thread-local so log formatters and views can
    retrieve it via ``get_request_id()``.
  - Adds ``X-Request-ID`` to every outgoing response so clients can correlate
    log entries with a specific request.

Thread-safety:
  Each Django worker thread (or coroutine, when used with ASGI) gets its own
  ``_request_id_local`` instance via ``threading.local()``, preventing any
  cross-request bleed.

Usage in views / tasks:
  from shared.middleware import get_request_id
  request_id = get_request_id()  # Returns str UUID or empty string

Requirements: 6.1
"""

import threading
import uuid

# ---------------------------------------------------------------------------
# Thread-local storage
# ---------------------------------------------------------------------------

_request_id_local = threading.local()


def get_request_id() -> str:
    """Return the request_id for the current thread, or an empty string."""
    return getattr(_request_id_local, "request_id", "")


def get_tenant_id() -> str:
    """
    Return the tenant schema name for the current thread, or an empty string.

    Populated by RequestIdMiddleware when ``request.tenant`` is available
    (set by django-tenants TenantMiddleware earlier in the stack).
    """
    return getattr(_request_id_local, "tenant_id", "")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class RequestIdMiddleware:
    """
    WSGI/ASGI-compatible middleware that attaches a unique request ID to every
    request/response cycle.

    Insert after SecurityMiddleware (and WhiteNoiseMiddleware) but before
    SessionMiddleware in MIDDLEWARE so that all subsequent middleware and views
    can access the request_id via ``get_request_id()``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Use the upstream-supplied header (e.g. from a load-balancer) or mint
        # a fresh UUID4.
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        _request_id_local.request_id = request_id

        # Store tenant_id if django-tenants has already resolved the tenant.
        tenant = getattr(request, "tenant", None)
        _request_id_local.tenant_id = str(tenant.schema_name) if tenant else ""

        response = self.get_response(request)

        # Propagate the request_id back to the caller.
        response["X-Request-ID"] = request_id

        # Clean up thread-local to avoid leaking state on thread reuse.
        _request_id_local.request_id = ""
        _request_id_local.tenant_id = ""

        return response
