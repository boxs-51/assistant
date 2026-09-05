"""Shared fixtures/helpers for live E2E tests.

These tests intentionally talk to a running Gateway over HTTP. They are skipped unless
GATEWAY_BASE_URL is set, so the normal unit/architecture suite remains offline.
"""

import os

import pytest

def run_test_live():
    return os.getenv("RUN_LIVE_TESTS", "false").lower() in {"1", "true", "yes", "on"}

@pytest.fixture(scope="session")
def live_base_url():
    BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return BASE_URL.rstrip("/")

@pytest.fixture(scope="session")
def live_headers():
    GATEWAY_AUTH_TOKEN = os.getenv("GATEWAY_AUTH_TOKEN")
    if not GATEWAY_AUTH_TOKEN:
        pytest.skip("Set GATEWAY_AUTH_TOKEN to run live Phase 0 E2E tests")
    return {"Authorization": f"Bearer {GATEWAY_AUTH_TOKEN}"} if GATEWAY_AUTH_TOKEN else {}

@pytest.fixture(scope="session")
def live_timeout():
    return float(os.getenv("LIVE_TIMEOUT", "90"))

@pytest.fixture(scope="session")
def live_concurrency():
    return max(1, int(os.getenv("LIVE_CONCURRENCY", "3")))

@pytest.fixture(scope="session")
def live_long_text_chars():
    return max(1000, int(os.getenv("LIVE_LONG_TEXT_CHARS", "30000")))

@pytest.fixture(scope="session")
def live_heavy_timeout():
    return float(os.getenv("LIVE_HEAVY_TIMEOUT", "180"))
@pytest.fixture(scope="session")
def live_long_text_chars():
    return max(1000, int(os.getenv("LIVE_LONG_TEXT_CHARS", "30000")))

def run_heavy_test():
    return os.getenv("LIVE_HEAVY_TEST", "false").lower() in {"1", "true", "yes", "on"}

def run_file_test():
    return os.getenv("LIVE_FILE_TEST", "false").lower() in {"1", "true", "yes", "on"}

def run_embedding_test():
    return os.getenv("LIVE_EMBEDDING_TEST", "false").lower() in {"1", "true", "yes", "on"}

