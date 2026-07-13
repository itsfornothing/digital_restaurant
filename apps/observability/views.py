"""
Observability views — /health endpoint.

Returns the current operational status of all platform dependencies:
  - PostgreSQL (critical)
  - Redis cache (critical)
  - Celery workers (non-critical / degraded)
  - Cloudflare R2 (non-critical / degraded; skipped when unconfigured)

Response shape:
  {
    "status": "ok" | "degraded" | "down",
    "checks": {
      "postgres":  {"status": "ok"|"error", "latency_ms": float, "detail": str|null},
      "redis":     {"status": "ok"|"error", "latency_ms": float, "detail": str|null},
      "celery":    {"status": "ok"|"error", "latency_ms": float, "detail": str|null},
      "r2":        {"status": "ok"|"error"|"skipped", "latency_ms": float, "detail": str|null},
    }
  }

HTTP status: 200 for "ok"/"degraded", 503 for "down".

The endpoint is intentionally public — no authentication is required.

Requirements: 6.3
"""

import logging
import time
from datetime import datetime, timezone

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


def _check_postgres() -> dict:
    """Run SELECT 1 against the default database."""
    start = time.monotonic()
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        latency_ms = (time.monotonic() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2), "detail": None}
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("Health check — postgres failed: %s", exc)
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "detail": str(exc),
        }


def _check_redis() -> dict:
    """Ping the default cache backend using a test set/get round-trip."""
    start = time.monotonic()
    try:
        from django.core.cache import cache

        cache.set("__health__", 1, 1)
        value = cache.get("__health__")
        latency_ms = (time.monotonic() - start) * 1000
        if value != 1:
            return {
                "status": "error",
                "latency_ms": round(latency_ms, 2),
                "detail": "Cache round-trip value mismatch",
            }
        return {"status": "ok", "latency_ms": round(latency_ms, 2), "detail": None}
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("Health check — redis failed: %s", exc)
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "detail": str(exc),
        }


def _check_celery() -> dict:
    """Inspect active Celery workers with a short timeout."""
    start = time.monotonic()
    try:
        from config.celery import app as celery_app

        inspector = celery_app.control.inspect(timeout=2)
        active = inspector.active()
        latency_ms = (time.monotonic() - start) * 1000
        if active is None:
            return {
                "status": "error",
                "latency_ms": round(latency_ms, 2),
                "detail": "No Celery workers responded within timeout",
            }
        worker_count = len(active)
        return {
            "status": "ok",
            "latency_ms": round(latency_ms, 2),
            "detail": f"{worker_count} worker(s) active",
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("Health check — celery failed: %s", exc)
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "detail": str(exc),
        }


def _check_r2() -> dict:
    """
    HEAD request to the configured R2 bucket.
    Returns status "skipped" when R2 credentials are not configured so that
    environments without R2 (e.g. development, CI) are not marked as degraded.
    """
    endpoint_url = getattr(settings, "R2_ENDPOINT_URL", "")
    access_key = getattr(settings, "R2_ACCESS_KEY_ID", "")
    secret_key = getattr(settings, "R2_SECRET_ACCESS_KEY", "")
    bucket_name = getattr(settings, "R2_BUCKET_NAME", "")

    if not endpoint_url or not access_key or not secret_key:
        return {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }

    start = time.monotonic()
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 1}),
        )
        client.head_bucket(Bucket=bucket_name)
        latency_ms = (time.monotonic() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2), "detail": None}
    except (BotoCoreError, ClientError) as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("Health check — R2 failed: %s", exc)
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "detail": str(exc),
        }
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("Health check — R2 unexpected error: %s", exc)
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "detail": str(exc),
        }


@never_cache
@require_GET
def health_check(request):
    """
    Public health check endpoint.

    Critical checks: postgres — failure → overall "down".
    Non-critical checks: celery, r2 — failure → overall "degraded".
    """
    checks = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "celery": _check_celery(),
        "r2": _check_r2(),
    }

    # Determine overall status
    postgres_ok = checks["postgres"]["status"] == "ok"
    redis_ok = checks["redis"]["status"] == "ok"
    celery_ok = checks["celery"]["status"] == "ok"
    r2_status = checks["r2"]["status"]
    r2_ok = r2_status in ("ok", "skipped")

    if not postgres_ok:
        overall_status = "down"
    elif not celery_ok or not r2_ok:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    http_status = 503 if overall_status == "down" else 200

    timestamp = datetime.now(tz=timezone.utc).isoformat()

    return JsonResponse(
        {"status": overall_status, "checks": checks, "timestamp": timestamp},
        status=http_status,
    )
