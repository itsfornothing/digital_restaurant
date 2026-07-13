"""
MetricsMiddleware — records per-request Prometheus metrics.

Positioned between PrometheusBeforeMiddleware and PrometheusAfterMiddleware in
MIDDLEWARE so that django-prometheus wraps both ends.

Captures:
  - request_count_total (method, endpoint, status_code)
  - request_duration_seconds (method, endpoint)
  - error_rate_total (endpoint, error_type) for 4xx/5xx responses
  - db_queries_per_request (method, endpoint) — number of DB queries per request

Requirements: 6.2, 6.8
"""

import time

from django.db import connection

from .metrics import (
    db_queries_per_request,
    error_rate_total,
    request_count_total,
    request_duration_seconds,
)


def _resolve_endpoint(request) -> str:
    """
    Return a stable endpoint label: the resolved URL name when available,
    otherwise the raw path.  Uses the URL name to avoid cardinality explosion
    from dynamic path segments (e.g. /api/v1/orders/123/ → 'order-detail').
    """
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match and resolver_match.url_name:
        return resolver_match.url_name
    return request.path


class MetricsMiddleware:
    """Django middleware that records custom Prometheus metrics on every response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        # Capture the number of DB queries already executed before this request
        # so we can subtract it from the count after to get just this request's
        # query count (works with Django's connection.queries list).
        queries_before = len(connection.queries)

        response = self.get_response(request)
        duration = time.monotonic() - start

        method = request.method
        endpoint = _resolve_endpoint(request)
        status_code = str(response.status_code)

        # Increment request counter
        request_count_total().labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).inc()

        # Record request duration
        request_duration_seconds().labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        # Record DB query count for this request
        # connection.queries is only populated when DEBUG=True; in production
        # django-prometheus's db backend provides django_db_execute_total
        # automatically.  We still record this for completeness in dev.
        queries_after = len(connection.queries)
        query_count = max(0, queries_after - queries_before)
        db_queries_per_request().labels(
            method=method,
            endpoint=endpoint,
        ).observe(query_count)

        # Record error metrics for 4xx and 5xx responses
        status = response.status_code
        if 400 <= status < 500:
            error_rate_total().labels(
                endpoint=endpoint,
                error_type="client_error",
            ).inc()
        elif 500 <= status < 600:
            error_rate_total().labels(
                endpoint=endpoint,
                error_type="server_error",
            ).inc()

        return response
