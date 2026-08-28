from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from copy import deepcopy
from typing import Any


class MockState:
    """Resettable deterministic in-memory state for offline tests."""

    def __init__(self, seed: str = "assistant-offline-mock"):
        self.seed = seed
        self._lock = threading.RLock()
        self._calls = defaultdict(int)
        self._files: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    def count(self, operation: str) -> int:
        with self._lock:
            self._calls[operation] += 1
            return self._calls[operation]

    def stable_id(self, namespace: str, value: str) -> str:
        digest = hashlib.sha256(
            f"{self.seed}:{namespace}:{value}".encode("utf-8")
        ).hexdigest()[:20]
        return f"mock-{namespace}-{digest}"

    @property
    def files(self):
        return self._files

    @property
    def batches(self):
        return self._batches

    @property
    def jobs(self):
        return self._jobs

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "seed": self.seed,
                "calls": dict(self._calls),
                "files": deepcopy(self._files),
                "batches": deepcopy(self._batches),
                "jobs": deepcopy(self._jobs),
            }

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._files.clear()
            self._batches.clear()
            self._jobs.clear()
