"""
conftest.py — pytest configuration for the tenants app unit tests.

Forces Django settings to the lightweight 'testing' profile (SQLite in-memory,
no PostgreSQL backend) so that model import and logic tests can run without
a running Docker / PostgreSQL instance.

Integration tests (schema creation, migrate_schemas) are run separately inside
the Docker container using the development settings.
"""

import django
import os


def pytest_configure(config):
    """Override DJANGO_SETTINGS_MODULE for this test package."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
