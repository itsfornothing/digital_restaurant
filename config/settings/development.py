"""
Development settings — NOT for production use.
"""

from .base import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = True

ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Security overrides for local dev (no HTTPS)
# ---------------------------------------------------------------------------

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# Database — PostgreSQL via Docker Compose service 'db'
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        # django_prometheus.db.backends.postgresql wraps the standard backend
        # to export DB query count metrics (django_db_execute_total).
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": "restaurant_platform",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "db",
        "PORT": "5432",
    }
}

# ---------------------------------------------------------------------------
# Email — console backend for local development
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# Debug toolbar (optional — only active when installed)
# ---------------------------------------------------------------------------

try:
    import debug_toolbar  # noqa: F401

    INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405
    INTERNAL_IPS = ["127.0.0.1"]
except ImportError:
    pass
