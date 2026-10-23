# Make Celery app available when 'config' package is imported,
# so that @shared_task decorators work correctly in all apps.
import sys
from .celery import app as celery_app  # noqa: F401

# Python 3.14 compatibility: fix copy(super()) in Django 5.0 BaseContext.__copy__
if sys.version_info >= (3, 14):
    from django.template.context import BaseContext
    def _py314_basecontext_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate
    BaseContext.__copy__ = _py314_basecontext_copy

__all__ = ["celery_app"]
