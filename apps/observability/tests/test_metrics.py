"""
Tests for apps.observability.metrics — verifies that the custom Prometheus
Counter and Histogram are importable and correctly registered.

Requirements: 6.2
"""

import django
from django.test import TestCase


class TestCustomMetricsRegistered(TestCase):
    """Verify the custom Prometheus metrics are importable and have correct types."""

    def test_custom_metrics_are_registered(self):
        """
        Import the three custom metrics from apps.observability.metrics and
        assert they have the expected Prometheus metric types.
        """
        from prometheus_client import Counter, Histogram

        from apps.observability.metrics import (
            error_rate_total,
            request_count_total,
            request_duration_seconds,
        )

        self.assertIsInstance(
            request_count_total(),
            Counter,
            "request_count_total should be a prometheus_client Counter",
        )
        self.assertIsInstance(
            request_duration_seconds(),
            Histogram,
            "request_duration_seconds should be a prometheus_client Histogram",
        )
        self.assertIsInstance(
            error_rate_total(),
            Counter,
            "error_rate_total should be a prometheus_client Counter",
        )

    def test_request_count_total_labels(self):
        """request_count_total should accept method, endpoint, status_code labels."""
        from apps.observability.metrics import request_count_total

        # Labelling and incrementing should not raise
        request_count_total().labels(
            method="GET", endpoint="health-check", status_code="200"
        ).inc()

    def test_request_duration_seconds_labels(self):
        """request_duration_seconds should accept method and endpoint labels."""
        from apps.observability.metrics import request_duration_seconds

        request_duration_seconds().labels(method="POST", endpoint="order-list").observe(
            0.123
        )

    def test_error_rate_total_labels(self):
        """error_rate_total should accept endpoint and error_type labels."""
        from apps.observability.metrics import error_rate_total

        error_rate_total().labels(
            endpoint="order-list", error_type="server_error"
        ).inc()

    def test_histogram_buckets(self):
        """request_duration_seconds should have the specified SLO buckets."""
        from apps.observability.metrics import request_duration_seconds

        expected_buckets = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        metric = request_duration_seconds()
        # Prometheus always appends +Inf; we check that our buckets are present
        upper_bounds = metric._kwargs.get(
            "buckets", metric._upper_bounds[:-1]
        )
        for bucket in expected_buckets:
            self.assertIn(
                bucket,
                list(upper_bounds),
                f"Expected bucket {bucket} not found in histogram",
            )
