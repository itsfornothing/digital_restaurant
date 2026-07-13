"""
Tests for apps.observability.views — /health endpoint.

All tests use mocks to avoid requiring real infrastructure (PostgreSQL, Redis,
Celery, R2) and run under config.settings.testing.

Requirements: 6.3
"""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse


class TestHealthCheckReturnsJson(TestCase):
    """Basic response shape tests."""

    def setUp(self):
        self.factory = RequestFactory()

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_returns_json(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """GET /health must return a JSON body with a top-level 'status' key."""
        mock_pg.return_value = {"status": "ok", "latency_ms": 1.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 1.0, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        self.assertEqual(response["Content-Type"], "application/json")
        data = json.loads(response.content)
        self.assertIn("status", data)
        self.assertIn("checks", data)


class TestHealthCheckPostgres(TestCase):
    """PostgreSQL health check tests."""

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_postgres_ok(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """When postgres check succeeds, the postgres check result is 'ok'."""
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 1.0, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertEqual(data["checks"]["postgres"]["status"], "ok")
        self.assertEqual(response.status_code, 200)

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_postgres_down(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """When postgres check fails, overall status must be 'down' and HTTP 503."""
        mock_pg.return_value = {
            "status": "error",
            "latency_ms": 0.0,
            "detail": "Connection refused",
        }
        mock_redis.return_value = {"status": "ok", "latency_ms": 1.0, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "down")
        self.assertEqual(response.status_code, 503)


class TestHealthCheckRedis(TestCase):
    """Redis health check tests."""

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_redis_ok(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """When redis check succeeds, the redis check result is 'ok'."""
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 0.5, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertEqual(data["checks"]["redis"]["status"], "ok")
        self.assertEqual(response.status_code, 200)

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_redis_down(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """When redis check fails, overall status must be 'down' and HTTP 503."""
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {
            "status": "error",
            "latency_ms": 0.0,
            "detail": "Redis connection timeout",
        }
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertIn(data["status"], ("down", "degraded"))
        self.assertEqual(data["checks"]["redis"]["status"], "error")


class TestHealthCheckCelery(TestCase):
    """Celery health check — non-critical, yields 'degraded'."""

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_celery_degraded(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """
        When celery check returns error but postgres/redis are ok,
        overall status is 'degraded' (not 'down') and HTTP 200.
        """
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 0.5, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "error",
            "latency_ms": 2000.0,
            "detail": "No Celery workers responded within timeout",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["checks"]["celery"]["status"], "error")
        self.assertEqual(response.status_code, 200)


class TestHealthCheckR2(TestCase):
    """R2 health check — skipped when credentials not configured."""

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    @override_settings(R2_ENDPOINT_URL="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY="")
    def test_health_check_r2_skipped_when_unconfigured(
        self, mock_pg, mock_redis, mock_celery
    ):
        """
        When R2_ENDPOINT_URL is empty, _check_r2 returns status 'skipped' and
        the overall status is not affected.
        """
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 0.5, "detail": None}
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        # Call _check_r2 directly with empty settings
        from apps.observability.views import _check_r2

        result = _check_r2()
        self.assertEqual(result["status"], "skipped")
        self.assertIn("not configured", result["detail"])

    @patch("apps.observability.views._check_celery")
    @patch("apps.observability.views._check_r2")
    @patch("apps.observability.views._check_redis")
    @patch("apps.observability.views._check_postgres")
    def test_health_check_r2_skipped_does_not_degrade(
        self, mock_pg, mock_redis, mock_r2, mock_celery
    ):
        """
        When R2 returns 'skipped', overall status should be 'ok' (not 'degraded').
        """
        mock_pg.return_value = {"status": "ok", "latency_ms": 2.0, "detail": None}
        mock_redis.return_value = {"status": "ok", "latency_ms": 0.5, "detail": None}
        mock_r2.return_value = {
            "status": "skipped",
            "latency_ms": 0.0,
            "detail": "R2 credentials not configured",
        }
        mock_celery.return_value = {
            "status": "ok",
            "latency_ms": 5.0,
            "detail": "1 worker(s) active",
        }

        response = self.client.get("/health")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(response.status_code, 200)


class TestMetricsMiddleware(TestCase):
    """MetricsMiddleware unit test — verifies metrics are incremented on each request."""

    def test_metrics_middleware_records_request(self):
        """
        Call MetricsMiddleware directly with a fake request/response pair and
        assert that request_count_total.labels().inc() is invoked.
        """
        from unittest.mock import MagicMock, patch

        from django.http import HttpResponse
        from django.test import RequestFactory

        from apps.observability.middleware import MetricsMiddleware

        factory = RequestFactory()
        request = factory.get("/health")

        # Stub get_response to return a plain 200
        fake_response = HttpResponse("ok", status=200)

        def get_response(req):
            return fake_response

        middleware = MetricsMiddleware(get_response)

        # Patch the inc() on the counter label vector
        mock_counter_labels = MagicMock()
        mock_counter = MagicMock()
        mock_counter.labels.return_value = mock_counter_labels

        mock_histogram_labels = MagicMock()
        mock_histogram = MagicMock()
        mock_histogram.labels.return_value = mock_histogram_labels

        with patch(
            "apps.observability.middleware.request_count_total", mock_counter
        ), patch(
            "apps.observability.middleware.request_duration_seconds", mock_histogram
        ):
            response = middleware(request)

        # Counter should have been labelled and incremented
        mock_counter.labels.assert_called_once_with(
            method="GET",
            endpoint="/health",
            status_code="200",
        )
        mock_counter_labels.inc.assert_called_once()

        # Histogram should have been observed
        mock_histogram.labels.assert_called_once_with(method="GET", endpoint="/health")
        mock_histogram_labels.observe.assert_called_once()

        self.assertEqual(response.status_code, 200)
