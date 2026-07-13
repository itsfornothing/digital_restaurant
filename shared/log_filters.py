"""
shared/log_filters.py — Logging filters and custom JSON formatter.

RequestIdFilter injects the current request_id and tenant_id from the
thread-local values set by shared.middleware.RequestIdMiddleware into every
log record so that structured JSON log lines include these fields automatically.

CustomJsonFormatter overrides ``add_fields`` on pythonjsonlogger.JsonFormatter
to map standard Python log field names to the canonical output field names
required by FR-P5.1:
  - ``%(levelname)s``  → ``level``
  - ``%(name)s``       → ``logger``
  - ``timestamp``      added as ISO-8601 UTC string
  - ``request_id``     and ``tenant_id`` pulled from the filter above

Requirements: FR-P5.1 (Requirement 6.1)
"""

import logging
from datetime import datetime, timezone

from pythonjsonlogger import jsonlogger

from shared.middleware import get_request_id, get_tenant_id


class RequestContextFilter(logging.Filter):
    """
    Logging filter that adds ``request_id`` and ``tenant_id`` attributes to
    every LogRecord.

    Attach to any handler whose formatter references ``%(request_id)s`` or
    ``%(tenant_id)s``.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = get_request_id() or "-"
        record.tenant_id = get_tenant_id() or "-"
        return True


# Keep the old name as an alias so any existing references in base.py / tests
# continue to work without changes.
RequestIdFilter = RequestContextFilter


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter that emits a canonical set of fields for every log record:

      {
        "timestamp": "2024-01-15T12:34:56.789012Z",
        "level":     "INFO",
        "logger":    "django.request",
        "message":   "...",
        "request_id": "...",
        "tenant_id":  "..."
      }

    Standard Python log attributes are renamed:
      levelname → level
      name      → logger
    """

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # Canonical timestamp in ISO-8601 UTC format
        log_record["timestamp"] = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        # Rename levelname → level, name → logger
        log_record["level"] = log_record.pop("levelname", record.levelname)
        log_record["logger"] = log_record.pop("name", record.name)

        # Ensure request_id / tenant_id are present (the filter adds them, but
        # provide safe defaults in case the filter is not attached).
        log_record.setdefault("request_id", getattr(record, "request_id", "-"))
        log_record.setdefault("tenant_id", getattr(record, "tenant_id", "-"))
