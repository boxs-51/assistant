"""TA-V1-R3 local effect-start lease and revocation drain primitive.

This is a *local* sequencing gate, not a remote signed authority or an
OS process sandbox. A revoke acknowledgement is returned only after active
leased callables have completed. Detached subprocesses/MCP effects are not
certified by this gate and remain subject to their independent DENY policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock
from time import monotonic
from typing import Iterator


class EffectStartRevoked(PermissionError):
    """A local effect lease cannot be issued after revocation."""


class LocalEffectStartGate:
    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._revoked = False
        self._active = 0
        self._epoch = 0

    @contextmanager
    def effect_lease(self) -> Iterator[None]:
        with self._condition:
            if self._revoked:
                raise EffectStartRevoked("Effect-start authority is revoked.")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def revoke_and_drain(self, *, timeout_seconds: float | None = None) -> int:
        """Irreversibly close this generation; ACK only when no lease remains.

        A timeout is never a successful revocation ACK. Even on timeout,
        new local effect leases are denied; incomplete active work remains
        unverified and must not be claimed as cleaned up.
        """
        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        with self._condition:
            if not self._revoked:
                self._revoked = True
                self._epoch += 1
            epoch = self._epoch
            deadline = (
                monotonic() + timeout_seconds
                if timeout_seconds is not None
                else None
            )
            while self._active:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        "Local effect-start revoke is pending active leases; "
                        "no revoke ACK may be issued."
                    )
                self._condition.wait(timeout=remaining)
            return epoch
