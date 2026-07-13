"""
Custom Prometheus metrics for the Restaurant Platform.

Provides application-level metrics:
- request_count_total: Counter per endpoint (method, endpoint, status_code)
- request_duration_seconds: Histogram with p50/p95/p99-appropriate buckets
- error_rate_total: Counter of errors per endpoint and error type
- websocket_connections_active: Gauge tracking live WebSocket connections
- celery_tasks_total: Counter of Celery task executions labelled by name and status
- db_queries_per_request: Histogram tracking DB query count per request

Celery signal handlers are registered in the AppConfig.ready() method in
apps/observability/apps.py.

Requirements: 6.2, 6.8
"""

from prometheus_client import Counter, Gauge, Histogram

_metrics_cache = {}

def _get_metric(metric_type, name, documentation, **kwargs):
    key = (metric_type, name)
    if key not in _metrics_cache:
        try:
            _metrics_cache[key] = metric_type(name, documentation, **kwargs)
        except ValueError:
            # Daphne reloads cause "Duplicated timeseries in CollectorRegistry"
            _metrics_cache[key] = metric_type(name, documentation, **kwargs)
    return _metrics_cache[key]

def request_count_total():
    return _get_metric(Counter, "restaurant_request_count_total",
        "Total number of HTTP requests processed by the platform",
        labelnames=["method", "endpoint", "status_code"])

def request_duration_seconds():
    return _get_metric(Histogram, "restaurant_request_duration_seconds",
        "HTTP request latency in seconds",
        labelnames=["method", "endpoint"],
        buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0])

def error_rate_total():
    return _get_metric(Counter, "restaurant_error_rate_total",
        "Total number of HTTP error responses (4xx/5xx) from the platform",
        labelnames=["endpoint", "error_type"])

def websocket_connections_active():
    return _get_metric(Gauge, "restaurant_websocket_connections_active",
        "Number of active WebSocket connections")

def celery_tasks_total():
    return _get_metric(Counter, "restaurant_celery_tasks_total",
        "Total number of Celery task executions by outcome",
        labelnames=["task_name", "status"])

def db_queries_per_request():
    return _get_metric(Histogram, "restaurant_db_queries_per_request",
        "Number of database queries executed per HTTP request",
        labelnames=["method", "endpoint"],
        buckets=[1, 2, 5, 10, 20, 50, 100, 200])
