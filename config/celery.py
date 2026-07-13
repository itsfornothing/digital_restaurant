"""
Celery application configuration for the Restaurant Platform.

Celery workers are started with:
    celery -A config.celery worker --loglevel=info

Celery Beat scheduler:
    celery -A config.celery beat --scheduler django_celery_beat.schedulers:DatabaseScheduler
"""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("restaurant_platform")

# Read configuration from Django settings using the CELERY_ namespace prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all installed apps (looks for tasks.py in each app).
app.autodiscover_tasks()


@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Schedule periodic inventory-menu reconciliation every 5 minutes."""
    sender.add_periodic_task(
        300.0,  # every 5 minutes
        "apps.inventory.tasks.reconcile_menu_availability",
        name="reconcile-menu-availability-every-5min",
    )


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostic task for verifying Celery connectivity."""
    print(f"Request: {self.request!r}")
