# Make Celery app available when 'config' package is imported,
# so that @shared_task decorators work correctly in all apps.
from .celery import app as celery_app  # noqa: F401

__all__ = ["celery_app"]
