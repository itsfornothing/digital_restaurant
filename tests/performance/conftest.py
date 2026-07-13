"""
tests/performance/conftest.py

pytest fixtures shared across all performance tests in this directory.

Provides:
    base_url    — the root URL of the server under test.  Resolved in order:
                   1. PLAYWRIGHT_BASE_URL environment variable
                   2. pytest-django ``live_server`` URL  (when running with
                      ``--reuse-db`` and a Django test database)
                   3. Hardcoded fallback: http://localhost:8000

    qr_token    — a QR scan token UUID string read from the
                  PLAYWRIGHT_QR_TOKEN environment variable, or an empty
                  string if not set.  Tests that require a real session
                  skip themselves when this is empty.

    qr_scan_url — fully-qualified URL for the QR scan entry point,
                  constructed from base_url + qr_token.  If qr_token is
                  not available, falls back to ``/customer/menu/``.

    page        — fallback fixture: if ``pytest-playwright`` is NOT installed
                  this fixture skips the test with an informative message
                  instead of raising a hard fixture-not-found error.  When
                  ``pytest-playwright`` IS installed its ``page`` fixture takes
                  precedence and this fallback is never invoked.

Requirements: 19.1, 19.2, 19.3
"""

from __future__ import annotations

import importlib
import os

import pytest

# ---------------------------------------------------------------------------
# Detect whether pytest-playwright is available
# ---------------------------------------------------------------------------
_PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("pytest_playwright") is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """
    Return the base URL of the server under test.

    Resolution order:
      1. PLAYWRIGHT_BASE_URL environment variable
      2. pytest-django ``live_server`` fixture URL (if available)
      3. Fallback: http://localhost:8000
    """
    env_url = os.environ.get("PLAYWRIGHT_BASE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url

    # Try to use the pytest-django live_server fixture if it has been started
    # (only available when the tests are run inside a Django test session).
    live_server_fixture = request.config.pluginmanager.get_plugin("django")
    if live_server_fixture is not None:
        try:
            ls = request.getfixturevalue("live_server")
            return ls.url.rstrip("/")
        except Exception:
            pass

    return "http://localhost:8000"


@pytest.fixture(scope="session")
def qr_token() -> str:
    """Return a QR token UUID string from env, or '' if not configured."""
    return os.environ.get("PLAYWRIGHT_QR_TOKEN", "").strip()


@pytest.fixture(scope="session")
def qr_scan_url(base_url: str, qr_token: str) -> str:
    """
    Return the full QR scan URL for performance tests.

    If qr_token is set: ``{base_url}/qr/scan/{qr_token}/``
    Otherwise:          ``{base_url}/customer/menu/``
    """
    if qr_token:
        return f"{base_url}/qr/scan/{qr_token}/"
    return f"{base_url}/customer/menu/"


# ---------------------------------------------------------------------------
# Fallback ``page`` fixture
#
# When pytest-playwright is installed it registers its own ``page`` fixture
# at function scope — that registration takes precedence over this one and
# this fixture is never called.
#
# When pytest-playwright is NOT installed this fixture is the only definition
# of ``page`` and it skips the test with an informative message rather than
# raising a hard "fixture not found" error.
# ---------------------------------------------------------------------------

if not _PLAYWRIGHT_AVAILABLE:
    @pytest.fixture()
    def page():  # noqa: F811
        pytest.skip(
            "pytest-playwright is not installed — Playwright-based tests cannot run. "
            "Install it with: pip install pytest-playwright playwright && "
            "playwright install chromium"
        )
