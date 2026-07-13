"""
conftest.py — Root-level pytest configuration for the restaurant platform.

This file provides shared fixtures and configuration for all tests.

Requirements: 19.10 (regression suite), 21.1 (UAT bug fixes)
"""

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures available to all tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def clear_cache():
    """
    Clear the Django in-memory cache before and after each test that requests
    this fixture.  Prevents cache state leaking between tests for
    FinancialService.compute_profit and CustomerMenuView.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()
