#!/usr/bin/env python3
"""Run Phase 0 live E2E tests against an already running Gateway."""

from __future__ import annotations

import os
import subprocess
import sys


if __name__ == "__main__":
    base_url = os.getenv("PHASE0_LIVE_BASE_URL")
    if not base_url:
        raise SystemExit("PHASE0_LIVE_BASE_URL is required, e.g. http://127.0.0.1:8000")
    env = dict(os.environ)
    env.setdefault("PHASE0_LIVE_BASE_URL", base_url)
    raise SystemExit(subprocess.call([
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/e2e/test_phase0_live.py",
        *sys.argv[1:],
    ], env=env))
