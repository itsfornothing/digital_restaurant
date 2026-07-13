"""
Production settings — all secrets read from environment variables via python-decouple.
"""

from decouple import config

from .base import *  # noqa: F401, F403
from .base import INSTALLED_APPS, MIDDLEWARE  # noqa: F401

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = False

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="").split(",")

SECRET_KEY = config("DJANGO_SECRET_KEY")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        # django_prometheus.db.backends.postgresql wraps the standard backend
        # to automatically export DB query count and duration metrics
        # (django_db_execute_total) — Requirement 6.8.
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "sslmode": "require",
        },
    }
}

# ---------------------------------------------------------------------------
# Read Replica database (Task 20.4 — Requirement 19.9)
#
# When REPLICA_DB_HOST is set, read-heavy queries for Income, ProfitRecord,
# Expense, and AuditLog models are routed to this database by
# shared.db_router.ReadReplicaRouter.  If the env var is absent the replica
# key is not added and the router gracefully falls back to 'default'.
#
# In tests, the replica mirrors the default database (TEST.MIRROR = 'default')
# so no separate test replica database is needed.
# ---------------------------------------------------------------------------

DATABASES["replica"] = {
    # Use the prometheus-wrapped backend for the replica too so DB query
    # metrics cover both read and write paths.
    "ENGINE": "django_tenants.postgresql_backend",
    "NAME": config("REPLICA_DB_NAME", default=config("DB_NAME", default="")),
    "USER": config("REPLICA_DB_USER", default=config("DB_USER", default="")),
    "PASSWORD": config("REPLICA_DB_PASSWORD", default=config("DB_PASSWORD", default="")),
    "HOST": config("REPLICA_DB_HOST", default=config("DB_HOST", default="")),
    "PORT": config("REPLICA_DB_PORT", default=config("DB_PORT", default="5432")),
    "CONN_MAX_AGE": 60,
    "OPTIONS": {
        "sslmode": "require",
    },
    # In tests, the replica mirrors the primary so tests always use a single DB.
    "TEST": {"MIRROR": "default"},
}

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------
# Content Security Policy (django-csp 3.8)
# Restricts resource loading to known, trusted origins.
# Requirements: 19.5, 19.6
# ---------------------------------------------------------------------------

# django-csp middleware must be inserted early in the stack so the
# Content-Security-Policy header is present on every response.
MIDDLEWARE = [
    # PrometheusBeforeMiddleware MUST be FIRST so it can start timing every
    # request before any other middleware runs (Requirement 6.2).
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "apps.tenants.middleware.TenantMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",  # CSP header on every response
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.middleware.AuditLogMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.observability.middleware.MetricsMiddleware",
    # PrometheusAfterMiddleware MUST be LAST so it records the final response
    # status after all other middleware has processed it (Requirement 6.2).
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

# Fetch / connect — self + WebSocket
CSP_DEFAULT_SRC = ("'self'",)

# Scripts — CDNs + unsafe-inline required for inline script blocks in templates
CSP_SCRIPT_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://cdn.jsdelivr.net",   # Bootstrap JS
    "https://unpkg.com",           # HTMX
)

# Styles — CDNs + unsafe-inline for inline style blocks
CSP_STYLE_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://fonts.googleapis.com",
    "https://cdn.jsdelivr.net",   # Bootstrap CSS + Icons
    "https://unpkg.com",           # HTMX styles (if any)
)

# Fonts — Bootstrap Icons CDN + Google Fonts
CSP_FONT_SRC = (
    "'self'",
    "https://cdn.jsdelivr.net",   # Bootstrap Icons font files
    "https://fonts.gstatic.com",
    "data:",
)

# Images — same origin, data URIs (e.g. inline QR codes), and HTTPS CDNs
CSP_IMG_SRC = (
    "'self'",
    "data:",
    "https:",
)

# WebSocket / XHR / Fetch — same origin + WebSocket protocol
CSP_CONNECT_SRC = (
    "'self'",
    "ws:",
    "wss:",
)

# Disallow framing to prevent clickjacking (belt-and-suspenders with
# X_FRAME_OPTIONS = "DENY")
CSP_FRAME_ANCESTORS = ("'none'",)

# No plugins
CSP_OBJECT_SRC = ("'none'",)

# Base URI restricted to same origin to prevent base-tag injection
CSP_BASE_URI = ("'self'",)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="smtp.sendgrid.net")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@platform.example.com")

# ---------------------------------------------------------------------------
# Static files — served via Nginx / CDN in production
# ---------------------------------------------------------------------------

STATIC_ROOT = "/app/staticfiles"

# ---------------------------------------------------------------------------
# Logging — structured JSON to stdout (collected by container runtime)
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "django.utils.log.ServerFormatter",
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
