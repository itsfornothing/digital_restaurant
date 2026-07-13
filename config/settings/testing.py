"""
testing.py — Lightweight settings for unit tests that do NOT require PostgreSQL.

Replaces the PostgreSQL backend with SQLite in-memory and removes django-tenants
from INSTALLED_APPS so that its AppConfig.ready() validation doesn't fire.

Tenant integration tests (schema creation, migrate_schemas) run inside Docker
using the development settings and are covered by subtask 2.6.
"""

import os
from pathlib import Path

# Allow async tests (Playwright, asyncio) to access the ORM
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Must still be on sys.path so the apps/ package resolves
# ---------------------------------------------------------------------------
# (set by conftest via sys.path manipulation or manage.py)

# ---------------------------------------------------------------------------
# Database — SQLite in-memory (no PostgreSQL/psycopg2 needed)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    # Replica mirrors default so db_router tests can verify routing logic
    # without requiring a real PostgreSQL replica.  TEST.MIRROR ensures
    # Django's test runner uses the same in-memory DB for both aliases.
    "replica": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"MIRROR": "default"},
    },
}

# Include the ReadReplicaRouter so router unit tests work correctly.
# django-tenants router is excluded (requires PG).
DATABASE_ROUTERS = ["shared.db_router.ReadReplicaRouter"]

# ---------------------------------------------------------------------------
# INSTALLED_APPS — django-tenants EXCLUDED to avoid AppConfig.ready() check
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_prometheus",
    # Our apps (no django-tenants wrapper)
    "apps.tenants",
    "apps.billing",
    "apps.authentication",
    "apps.audit",
    "apps.whitelabel",
    "apps.branches",
    "apps.menus",
    "apps.kitchen",
    "apps.inventory",
    "apps.expenses",
    "apps.financials",
    "apps.qr",
    "apps.orders",
    "apps.privacy",
    "apps.notifications",
    "apps.observability",
]

STATIC_URL = "/static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# django-tenants required settings (model import needs these even without
# the django_tenants app in INSTALLED_APPS)
# ---------------------------------------------------------------------------
TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"

# ---------------------------------------------------------------------------
# Auth — use our custom User model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "authentication.User"

# ---------------------------------------------------------------------------
# Middleware — minimal set (no TenantMiddleware for import-level tests)
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Caches — in-memory (no Redis)
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ---------------------------------------------------------------------------
# Password hashers — fast MD5 for tests
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ---------------------------------------------------------------------------
# Celery — always eager in tests
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Logging — silence during tests
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
}

# ---------------------------------------------------------------------------
# Security (relaxed for tests)
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "shared.exceptions.custom_exception_handler",
}

# ---------------------------------------------------------------------------
# Rate limiting — disabled in tests so that Hypothesis examples do not
# accumulate IP-level attempt counters across test iterations.
# Property 9 (rate limiting) tests are responsible for enabling this
# explicitly in their own setup when needed.
# ---------------------------------------------------------------------------
RATELIMIT_ENABLE = False

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# ---------------------------------------------------------------------------
# Django Channels — in-memory channel layer for tests (no Redis required)
# ---------------------------------------------------------------------------
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

ASGI_APPLICATION = "config.asgi.application"
