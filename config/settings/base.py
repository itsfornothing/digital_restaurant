"""
Base settings shared across all environments.
"""

import os
from pathlib import Path


from decouple import config

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Build paths inside the project: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECRET_KEY = config("DJANGO_SECRET_KEY", default="change-me-in-production")

DEBUG = False

ALLOWED_HOSTS = []

# ---------------------------------------------------------------------------
# django-tenants configuration
# ---------------------------------------------------------------------------

# Must be first in DATABASE_ROUTERS
DATABASE_ROUTERS = [
    "django_tenants.routers.TenantSyncRouter",
    # Routes read queries for Income, ProfitRecord, Expense, and AuditLog to
    # the 'replica' PostgreSQL database when configured (Task 20.4, Req 19.9).
    "shared.db_router.ReadReplicaRouter",
]

TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"

# Apps that live in the shared (public) PostgreSQL schema
SHARED_APPS = [
    "django_tenants",
    "django_prometheus",
    "drf_spectacular",
    "django.contrib.contenttypes",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.tenants",
    "apps.billing",
    # authentication must also be in SHARED_APPS because AUTH_USER_MODEL points
    # to authentication.User and Django's shared-schema apps (sessions, admin)
    # generate FKs to that table during migrate_schemas --shared.
    "apps.authentication",
    # branches must be in SHARED_APPS because authentication.0002 adds a FK
    # to branches.Branch; without this the shared-schema migration fails.
    "apps.branches",
]

# Apps scoped to each tenant schema
TENANT_APPS = [
    "apps.authentication",
    "apps.audit",
    "apps.observability",
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
    "apps.webhooks",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    # PrometheusBeforeMiddleware MUST be FIRST so it can start timing every
    # request before any other middleware runs (Requirement 6.2).
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    # Custom TenantMiddleware resolves the tenant from the hostname before any
    # application logic is applied.
    "apps.tenants.middleware.TenantMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files (including compiled React bundles)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Attach a unique X-Request-ID to every request/response cycle and store
    # it in a thread-local for structured log enrichment.
    "shared.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # AuditLogMiddleware must come after AuthenticationMiddleware and
    # SessionMiddleware so that request.user and request.session are available.
    "apps.audit.middleware.AuditLogMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Custom metrics middleware — records request count, duration, and error rate
    "apps.observability.middleware.MetricsMiddleware",
    # PrometheusAfterMiddleware MUST be LAST so it records the final response
    # status after all other middleware has processed it (Requirement 6.2).
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "config.urls"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # White-label: injects tenant_config, use_ethiopic_font, amharic_css
                "apps.whitelabel.context_processors.whitelabel_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database — overridden per environment
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        # django_prometheus.db.backends.postgresql wraps the standard
        # django_tenants backend to automatically export DB query count and
        # duration metrics (django_db_execute_total) — Requirement 6.8.
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": config("DB_NAME", default="restaurant_platform"),
        "USER": config("DB_USER", default="postgres"),
        "PASSWORD": config("DB_PASSWORD", default="postgres"),
        "HOST": config("DB_HOST", default="db"),
        "PORT": config("DB_PORT", default="5432"),
    },
    # ---------------------------------------------------------------------------
    # Read replica database (Task 20.4 — Requirement 19.9)
    #
    # In development and testing environments, the replica points to the same
    # database as 'default' so no real replica setup is required.  The
    # ReadReplicaRouter in shared/db_router.py will route reads for
    # Income, ProfitRecord, Expense, and AuditLog models to this alias.
    #
    # Production overrides this with a real replica host via REPLICA_DB_* env
    # vars in config/settings/production.py.
    # ---------------------------------------------------------------------------
    "replica": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": config("DB_NAME", default="restaurant_platform"),
        "USER": config("DB_USER", default="postgres"),
        "PASSWORD": config("DB_PASSWORD", default="postgres"),
        "HOST": config("DB_HOST", default="db"),
        "PORT": config("DB_PORT", default="5432"),
        # In tests, mirror the primary so no separate test replica DB is needed.
        "TEST": {"MIRROR": "default"},
    },
}

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "authentication.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
# SCOPE CHANGE (July 2026): SRS BR-8.3 specified 4-hour (14400s) customer session timeout.
# Changed to 7 days (604800s) by request to reduce re-authentication friction for
# customers who keep the menu tab open across a meal.
# Security impact: longer exposure window on shared/abandoned devices.
# To revert: set SESSION_COOKIE_AGE = 14400
SESSION_COOKIE_AGE = 604800  # 7 days
# Slide the expiry on every request so idle timeout is measured from last activity
SESSION_SAVE_EVERY_REQUEST = True  # Requirement 3.8

# ---------------------------------------------------------------------------
# Cache — in-process (no Redis)
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Django Channels — in-memory channel layer (no Redis)
# ---------------------------------------------------------------------------

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ---------------------------------------------------------------------------
# Celery — eager mode (no broker needed)
# ---------------------------------------------------------------------------

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en"

LANGUAGES = [
    ("en", "English"),
    ("am", "አማርኛ"),
]

from django.conf import locale

locale.LANG_INFO["am"] = {
    "code": "am",
    "name": "Amharic",
    "name_local": "አማርኛ",
    "bidi": False,
}

TIME_ZONE = "UTC"

USE_I18N = True
USE_L10N = True
USE_TZ = True

LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# WhiteNoise — serve compiled static files (including React bundles) from STATIC_ROOT
WHITENOISE_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_FILE_STORAGE = "shared.storage.R2Storage"

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
    "DEFAULT_PAGINATION_CLASS": "shared.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "shared.exceptions.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---------------------------------------------------------------------------
# drf-spectacular — OpenAPI 3.0
# ---------------------------------------------------------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "Restaurant Management & Smart Ordering Platform API",
    "DESCRIPTION": "Multi-tenant SaaS API for restaurant management. Supports Amharic (አማርኛ) and English.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1/",
    "COMPONENT_SPLIT_REQUEST": True,
    "POSTPROCESSING_HOOKS": ["drf_spectacular.hooks.postprocess_schema_enums"],
}

# ---------------------------------------------------------------------------
# Default primary key field type
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging — structured JSON via python-json-logger
#
# Each log record is emitted as a single-line JSON object containing:
#   timestamp   ISO-8601 UTC string
#   level       uppercased severity (INFO, WARNING, ERROR …)
#   logger      Python logger name (e.g. "django.request")
#   message     the log message
#   request_id  UUID from X-Request-ID header (set by RequestIdMiddleware)
#   tenant_id   schema name of the current tenant (set by RequestIdMiddleware)
#
# RequestContextFilter (aliased as RequestIdFilter) pulls request_id and
# tenant_id out of the thread-local maintained by RequestIdMiddleware and
# attaches them to every LogRecord before formatting.
#
# Docker captures stdout and forwards it to log aggregation (Requirement 6.1).
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "shared.log_filters.RequestContextFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "shared.log_filters.CustomJsonFormatter",
            "format": (
                "%(timestamp)s %(level)s %(logger)s %(message)s"
                " %(request_id)s %(tenant_id)s"
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request_context"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Cloudflare R2 / Storage config (read by shared/storage.py)
# ---------------------------------------------------------------------------

R2_ENDPOINT_URL = config("R2_ENDPOINT_URL", default="")
R2_ACCESS_KEY_ID = config("R2_ACCESS_KEY_ID", default="")
R2_SECRET_ACCESS_KEY = config("R2_SECRET_ACCESS_KEY", default="")
R2_BUCKET_NAME = config("R2_BUCKET_NAME", default="restaurant-platform")
R2_CUSTOM_DOMAIN = config("R2_CUSTOM_DOMAIN", default="")
