import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ObservabilityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.observability"
    verbose_name = "Observability"

    def ready(self):
        """
        Connect Celery signal handlers for task instrumentation.

        Called once when Django starts.  Registers task_success, task_failure,
        and task_retry signals to increment the celery_tasks_total counter.

        Requirements: 6.8
        """
        self._register_celery_signals()

    @staticmethod
    def _register_celery_signals():
        """Register Celery task lifecycle signals for Prometheus instrumentation."""
        try:
            from celery.signals import task_failure, task_retry, task_success

            from apps.observability.metrics import celery_tasks_total

            @task_success.connect
            def on_task_success(sender, **kwargs):
                """Increment counter when a Celery task completes successfully."""
                task_name = getattr(sender, "name", str(sender))
                celery_tasks_total().labels(
                    task_name=task_name,
                    status="success",
                ).inc()

            @task_failure.connect
            def on_task_failure(sender, **kwargs):
                """Increment counter when a Celery task raises an unhandled exception."""
                task_name = getattr(sender, "name", str(sender))
                celery_tasks_total().labels(
                    task_name=task_name,
                    status="failure",
                ).inc()

            @task_retry.connect
            def on_task_retry(sender, **kwargs):
                """Increment counter when a Celery task is retried."""
                task_name = getattr(sender, "name", str(sender))
                celery_tasks_total().labels(
                    task_name=task_name,
                    status="retry",
                ).inc()

            logger.debug("Celery Prometheus signal handlers registered.")

        except ImportError:
            # Celery is not installed or not available in this process;
            # signal registration is silently skipped.
            logger.debug("Celery not available — skipping task signal registration.")
