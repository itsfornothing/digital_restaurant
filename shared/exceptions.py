"""
API error envelope format and custom exception handler.

All API error responses use a consistent JSON envelope:

    {
        "error": {
            "code":    "TENANT_NOT_FOUND",
            "message": "No tenant matched the requested hostname.",
            "details": {}          // optional field-level validation errors
        }
    }

This module provides:
    - ``custom_exception_handler`` — DRF exception handler hook (configured in
      REST_FRAMEWORK['EXCEPTION_HANDLER'] in settings/base.py)
    - ``APIError`` — base exception class for raising typed errors from services
    - Named subclasses for all platform-level error codes

Full implementation is in Task 5.
This stub establishes the interface used across all apps.
"""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


# ---------------------------------------------------------------------------
# Custom exception handler — registered in settings/base.py
# ---------------------------------------------------------------------------


def custom_exception_handler(exc, context):
    """
    Wraps DRF's default exception handler to emit the platform's standard
    error envelope format.

    On 4xx/5xx responses the body becomes:
        {"error": {"code": "...", "message": "..."}}

    - "code" is the machine-readable error identifier for API consumers.
    - "message" is a safe, human-readable string — never raw DRF internals.
    - Internal error details (field-level validation errors, stack traces, etc.)
      are intentionally omitted from the response to avoid leaking implementation
      information to clients.  Validation errors surface their messages through
      "message" only.
    """
    from rest_framework.exceptions import ValidationError, AuthenticationFailed, NotAuthenticated

    response = exception_handler(exc, context)

    if response is None:
        return None

    code = getattr(exc, "default_code", "error").upper()

    # Build a clean, user-friendly message.
    # ValidationError.detail can be a dict (field errors) or list — flatten to
    # a single readable sentence rather than exposing the raw structure.
    if isinstance(exc, ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            # Collect the first message from each field: "name: This field is required."
            parts = []
            for field, errors in detail.items():
                if isinstance(errors, list):
                    parts.append(f"{field}: {errors[0]}" if errors else field)
                else:
                    parts.append(f"{field}: {errors}")
            message = "  ".join(parts) if parts else "Invalid input."
        elif isinstance(detail, list):
            message = str(detail[0]) if detail else "Invalid input."
        else:
            message = str(detail)
    elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        message = "Authentication required. Please log in and try again."
    elif hasattr(exc, "default_detail"):
        message = str(exc.default_detail)
    elif hasattr(exc, "detail"):
        message = str(exc.detail)
    else:
        message = "An unexpected error occurred."

    response.data = {
        "error": {
            "code": code,
            "message": message,
        }
    }

    return response


# ---------------------------------------------------------------------------
# Base API error
# ---------------------------------------------------------------------------


class APIError(APIException):
    """
    Base class for all platform-specific API errors.

    Subclass this to define named error codes:

        class TenantNotFound(APIError):
            status_code = 404
            default_code = "TENANT_NOT_FOUND"
            default_detail = "No tenant matched the requested hostname."
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "API_ERROR"
    default_detail = "An error occurred."


# ---------------------------------------------------------------------------
# Platform-level errors
# ---------------------------------------------------------------------------


class TenantNotFound(APIError):
    """Raised when no Tenant matches the incoming request hostname."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "TENANT_NOT_FOUND"
    default_detail = "No tenant matched the requested hostname."


class TenantSuspended(APIError):
    """Raised when the matched Tenant has is_active=False."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "TENANT_SUSPENDED"
    default_detail = "This tenant has been suspended."


class ResourceLimitExceeded(APIError):
    """Raised by BillingService when a plan resource limit is reached."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_code = "RESOURCE_LIMIT_EXCEEDED"
    default_detail = "The subscription plan resource limit has been reached."


class QRCodeInvalid(APIError):
    """Raised when a customer scans an invalidated or expired QR code."""

    status_code = status.HTTP_410_GONE
    default_code = "QR_CODE_INVALID"
    default_detail = "This QR code is no longer valid. Please ask staff for a new code."


class RateLimitExceeded(APIError):
    """Raised when an IP exceeds the authentication rate limit."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "RATE_LIMIT_EXCEEDED"
    default_detail = "Too many attempts. Please try again later."


class PermissionDenied(APIError):
    """Raised when a user attempts to access a resource outside their RBAC scope."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "PERMISSION_DENIED"
    default_detail = "You do not have permission to perform this action."


class InvalidOrderTransition(APIError):
    """Raised when an illegal order status transition is attempted."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "INVALID_ORDER_TRANSITION"
    default_detail = "The requested order status transition is not permitted."
