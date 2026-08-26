"""Shared fixtures/helpers for live E2E tests.

These tests intentionally talk to a running Gateway over HTTP. They are skipped unless
PHASE0_LIVE_BASE_URL is set, so the normal unit/architecture suite remains offline.
"""

import os

import pytest


@pytest.fixture(scope="session")
def live_base_url():
    url = os.getenv("PHASE0_LIVE_BASE_URL")
    if not url:
        pytest.skip("Set PHASE0_LIVE_BASE_URL to run live Phase 0 E2E tests")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def live_headers():
    token = os.getenv("PHASE0_LIVE_AUTH_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}
