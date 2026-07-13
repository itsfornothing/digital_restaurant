"""
audit/decorators.py

Provides:
  - Thread-local request context storage (_request_context)
  - AuditLogMiddleware — captures user/IP/UA from every request
  - SENSITIVE_AUDIT_FIELDS — set of field names whose values must be redacted
  - redact_sensitive() — recursive redaction helper
  - audit_action() — decorator that wraps service functions to auto-log entries

Requirements: 5.1, 5.3
"""

import functools
import logging
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-local request context
# ---------------------------------------------------------------------------

_request_context = threading.local()


def _get_context_attr(name, default=None):
    """Safely read an attribute from the thread-local request context."""
    return getattr(_request_context, name, default)


# ---------------------------------------------------------------------------
# Sensitive field redaction
# ---------------------------------------------------------------------------

SENSITIVE_AUDIT_FIELDS = {"password", "token", "secret", "totp_secret"}


def redact_sensitive(value):
    """
    Recursively walk *value* and replace the values of any key found in
    SENSITIVE_AUDIT_FIELDS with the string "[REDACTED]".

    Works on dicts, lists, and scalars.  Does NOT mutate the input —
    returns a new sanitised copy.

    Requirements: 5.3
    """
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k in SENSITIVE_AUDIT_FIELDS else redact_sensitive(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# AuditLogMiddleware
# ---------------------------------------------------------------------------

class AuditLogMiddleware:
    """
    Django middleware that captures request context into a thread-local store
    so that the audit_action decorator can read it without access to the
    request object.

    Captured fields:
      - user_id    — str(request.user.id) if authenticated, else None
      - user_role  — request.user.role if authenticated, else ""
      - ip_address — from X-Forwarded-For or REMOTE_ADDR
      - user_agent — HTTP_USER_AGENT header
      - tenant_id  — str(request.tenant.pk) if tenant middleware ran, else None

    The context is cleared in the finally block of __call__ so it never
    leaks across requests on the same thread.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Populate thread-local context before the view runs
        self._set_context(request)
        try:
            response = self.get_response(request)
        finally:
            # Always clear — prevents context leaking to the next request
            self._clear_context()
        return response

    @staticmethod
    def _set_context(request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            _request_context.user_id = str(user.pk)
            _request_context.user_role = getattr(user, "role", "")
        else:
            _request_context.user_id = None
            _request_context.user_role = ""

        # Tenant — set by TenantMiddleware before this middleware runs
        tenant = getattr(request, "tenant", None)
        _request_context.tenant_id = str(tenant.pk) if tenant is not None else None

        # IP address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            _request_context.ip_address = x_forwarded_for.split(",")[0].strip()
        else:
            _request_context.ip_address = request.META.get("REMOTE_ADDR")

        # User-Agent
        _request_context.user_agent = request.META.get("HTTP_USER_AGENT", "")

    @staticmethod
    def _clear_context():
        for attr in ("user_id", "user_role", "ip_address", "user_agent", "tenant_id"):
            try:
                delattr(_request_context, attr)
            except AttributeError:
                pass


# ---------------------------------------------------------------------------
# audit_action decorator
# ---------------------------------------------------------------------------

def audit_action(
    action_code,
    resource_type,
    get_resource_id=None,
    get_old_value=None,
):
    """
    Decorator that wraps a service function and writes an AuditLog entry.

    Args:
        action_code (str): Standardised action enum code, e.g. "USER_LOGIN",
            "EXPENSE_DELETE".
        resource_type (str): Name of the affected model, e.g. "User", "Expense".
        get_resource_id (callable, optional): ``fn(result) -> UUID | str | None``
            Called with the wrapped function's return value to extract the
            resource_id for the log entry.
        get_old_value (callable, optional): ``fn(*args, **kwargs) -> dict | None``
            Called BEFORE the wrapped function executes to snapshot the
            before-state.  Must not raise — exceptions are silently swallowed.

    Behaviour:
        - On success: status="success", new_value taken from return value if
          it is a dict (or model instance serialised to dict).
        - On exception: status="failure", failure_reason=str(exc), the
          exception is re-raised after the log entry is written.
        - Reads user_id, ip_address, user_agent, tenant_id from thread-local
          _request_context (falls back to None if middleware hasn't run).
        - Silently no-ops if the AuditLog model is not yet importable (e.g.
          before migrations run in tests).

    Requirements: 5.1, 5.3
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # --- Capture old_value before execution ---
            old_val = None
            if get_old_value is not None:
                try:
                    old_val = get_old_value(*args, **kwargs)
                except Exception as exc:
                    logger.debug(
                        "audit_action: get_old_value raised for %s: %s", action_code, exc
                    )

            exc_raised = None
            result = None

            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                exc_raised = exc

            # --- Build the log entry ---
            _write_audit_log(
                action_code=action_code,
                resource_type=resource_type,
                get_resource_id=get_resource_id,
                result=result,
                old_val=old_val,
                exc_raised=exc_raised,
            )

            if exc_raised is not None:
                raise exc_raised

            return result

        return wrapper

    return decorator


def _write_audit_log(
    action_code,
    resource_type,
    get_resource_id,
    result,
    old_val,
    exc_raised,
):
    """
    Internal helper that creates the AuditLog entry.
    Silently swallows all errors so logging never blocks the main flow.
    """
    try:
        from apps.audit.models import AuditLog
    except Exception as import_err:
        logger.debug("AuditLog not yet available; skipping log entry: %s", import_err)
        return

    try:
        # Resolve resource_id
        resource_id = None
        if get_resource_id is not None and result is not None:
            try:
                resource_id = get_resource_id(result)
            except Exception:
                pass

        # Build new_value from result
        new_val = None
        if result is not None:
            if isinstance(result, dict):
                new_val = redact_sensitive(result)
            elif hasattr(result, "__dict__"):
                try:
                    raw = {
                        k: v for k, v in result.__dict__.items()
                        if not k.startswith("_")
                    }
                    new_val = redact_sensitive(_make_json_safe(raw))
                except Exception:
                    pass

        # Redact old_value
        if old_val is not None:
            old_val = redact_sensitive(old_val)

        # Read from thread-local context
        user_id = _get_context_attr("user_id")
        user_role = _get_context_attr("user_role", "")
        ip_address = _get_context_attr("ip_address")
        user_agent = _get_context_attr("user_agent", "")
        tenant_id = _get_context_attr("tenant_id")

        AuditLog.objects.create(
            tenant_id=tenant_id,
            branch_id=None,  # callers may override via get_old_value / subclass
            user_id=user_id,
            user_role=user_role,
            ip_address=ip_address or "0.0.0.0",
            user_agent=user_agent,
            action=action_code,
            resource_type=resource_type,
            resource_id=resource_id,
            old_value=old_val,
            new_value=new_val,
            status="failure" if exc_raised is not None else "success",
            failure_reason=str(exc_raised) if exc_raised is not None else "",
        )
    except Exception as log_err:
        logger.warning(
            "audit_action: failed to write AuditLog entry for %s: %s",
            action_code,
            log_err,
            exc_info=True,
        )


def _make_json_safe(d: dict) -> dict:
    """Convert non-JSON-serialisable values to strings."""
    import uuid as _uuid
    import datetime

    safe = {}
    for k, v in d.items():
        if isinstance(v, (_uuid.UUID,)):
            safe[k] = str(v)
        elif isinstance(v, (datetime.datetime, datetime.date)):
            safe[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, type(None))):
            safe[k] = v
        elif isinstance(v, dict):
            safe[k] = _make_json_safe(v)
        else:
            safe[k] = str(v)
    return safe
