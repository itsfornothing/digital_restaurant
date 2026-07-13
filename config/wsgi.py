"""
WSGI entry point for the Restaurant Platform.

Used by Gunicorn for serving synchronous HTTP requests.
WebSocket connections are handled by Daphne via asgi.py.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
