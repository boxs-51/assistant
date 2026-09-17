from __future__ import annotations

from threading import RLock

from .drivers.base import BaseCapabilityDriver


class CapabilityDriverRegistry:
    """Executable bindings keyed by concrete implementation identity."""

    def __init__(self) -> None:
        self._drivers: dict[str, BaseCapabilityDriver] = {}
        self._lock = RLock()

    def bind(
        self,
        implementation_id: str,
        driver: BaseCapabilityDriver,
        *,
        replace: bool = False,
    ) -> BaseCapabilityDriver:
        if not implementation_id.strip():
            raise ValueError("implementation_id must be non-empty")
        with self._lock:
            existing = self._drivers.get(implementation_id)
            if existing is not None and existing is not driver and not replace:
                raise ValueError(
                    f"Driver already bound for implementation '{implementation_id}'"
                )
            self._drivers[implementation_id] = driver
        return driver

    def get(self, implementation_id: str) -> BaseCapabilityDriver | None:
        with self._lock:
            return self._drivers.get(implementation_id)

    def unbind(self, implementation_id: str) -> BaseCapabilityDriver | None:
        with self._lock:
            return self._drivers.pop(implementation_id, None)

