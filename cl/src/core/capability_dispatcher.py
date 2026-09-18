from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict

from .local_capability_executor import (
    LocalCapabilityExecutor,
    LocalExecutionCancelled,
    LocalExecutionDenied,
)


@dataclass
class TerminalOutcome:
    event_type: str
    payload: Dict[str, Any]
    execution_id: str | None
    trace_id: str | None
    expires_at: float


@dataclass
class LocalInvocation:
    invocation_id: str
    capability_id: str
    envelope: Dict[str, Any]
    cancellation_event: threading.Event
    future: Future
    epoch: int


class CapabilityDispatcher:
    """Correlate realtime invocations with the canonical local executor."""

    def __init__(
        self,
        registry,
        realtime,
        *,
        hitl=None,
        max_workers: int = 8,
        max_pending: int = 32,
        terminal_ttl: float = 300.0,
        max_terminal: int = 1024,
        local_executor: LocalCapabilityExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.realtime = realtime
        self.local_executor = local_executor or LocalCapabilityExecutor(registry, hitl)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="capability-worker",
        )
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._terminal_ttl = terminal_ttl
        self._max_terminal = max_terminal
        self._lock = threading.RLock()
        self._invocations: Dict[str, LocalInvocation] = {}
        self._terminal: OrderedDict[str, TerminalOutcome] = OrderedDict()
        self._registered_capabilities: frozenset[str] = frozenset()
        self._epoch = 0

    def set_realtime(self, realtime) -> None:
        self.realtime = realtime

    def update_registration_snapshot(self, capability_ids) -> None:
        self._registered_capabilities = frozenset(capability_ids)

    def dispatch(self, envelope: Dict[str, Any]) -> None:
        invocation_id = envelope.get("invocation_id")
        if not invocation_id:
            return
        if envelope.get("connection_id") != self.realtime.connection_id:
            self._record_and_emit(
                invocation_id,
                envelope,
                "capability.error",
                self._error("CONNECTION_MISMATCH", "Invocation connection mismatch."),
            )
            return
        payload = envelope.get("payload") or {}
        capability_id = payload.get("capability_id")
        arguments = payload.get("arguments") or {}
        if not isinstance(capability_id, str) or not capability_id:
            self._record_and_emit(
                invocation_id, envelope, "capability.error",
                self._error("INVALID_CAPABILITY_ID", "capability_id is required."),
            )
            return
        if not isinstance(arguments, dict):
            self._record_and_emit(
                invocation_id, envelope, "capability.error",
                self._error("INVALID_ARGUMENTS", "arguments must be an object."),
            )
            return
        if capability_id not in self._registered_capabilities:
            self._record_and_emit(
                invocation_id, envelope, "capability.error",
                self._error(
                    "CAPABILITY_NOT_REGISTERED",
                    "Capability is not in this connection snapshot.",
                ),
            )
            return

        with self._lock:
            self._purge_terminal_locked()
            if invocation_id in self._invocations:
                return
            previous = self._terminal.get(invocation_id)
            if previous is not None:
                self._emit(invocation_id, previous)
                return
            if not self._capacity.acquire(blocking=False):
                self._record_and_emit(
                    invocation_id, envelope, "capability.error",
                    self._error("CLIENT_BUSY", "Local capability queue is full.", retryable=True),
                )
                return
            cancellation_event = threading.Event()
            try:
                future = self._executor.submit(
                    self.local_executor.execute,
                    capability_id,
                    arguments,
                    envelope,
                    cancellation_event,
                )
            except BaseException:
                self._capacity.release()
                raise
            self._invocations[invocation_id] = LocalInvocation(
                invocation_id,
                capability_id,
                dict(envelope),
                cancellation_event,
                future,
                self._epoch,
            )
        future.add_done_callback(lambda completed: self._complete(invocation_id, completed))

    def _complete(self, invocation_id: str, future: Future) -> None:
        with self._lock:
            invocation = self._invocations.pop(invocation_id, None)
        if invocation is None:
            return
        self._capacity.release()
        with self._lock:
            if invocation.epoch != self._epoch:
                return
        try:
            if future.cancelled():
                raise LocalExecutionCancelled()
            result = future.result()
        except LocalExecutionCancelled:
            event_type, payload = "capability.cancelled", {}
        except LocalExecutionDenied as exc:
            event_type, payload = "capability.error", self._error(
                "HITL_DENIED", str(exc), retryable=False
            )
        except Exception as exc:
            event_type, payload = "capability.error", self._error(
                "LOCAL_EXECUTION_FAILED",
                str(exc),
                retryable=False,
                details={"exception_type": type(exc).__name__},
            )
        else:
            event_type, payload = "capability.result", {"output": result}
        self._record_and_emit(
            invocation_id,
            invocation.envelope,
            event_type,
            payload,
            expected_epoch=invocation.epoch,
        )

    def cancel(self, invocation_id: str) -> bool:
        with self._lock:
            invocation = self._invocations.get(invocation_id)
            terminal = invocation_id in self._terminal
        if invocation is None:
            return terminal
        invocation.cancellation_event.set()
        invocation.future.cancel()
        return True

    def fail_all(self, reason: str = "Client connection disconnected.") -> int:
        with self._lock:
            invocations = list(self._invocations.values())
        for invocation in invocations:
            invocation.cancellation_event.set()
            invocation.future.cancel()
        return len(invocations)

    def reset_principal(self) -> int:
        """Invalidate work/outcomes that belong to the previous auth principal."""
        with self._lock:
            self._epoch += 1
            self._terminal.clear()
            invocations = list(self._invocations.values())
        for invocation in invocations:
            invocation.cancellation_event.set()
            invocation.future.cancel()
        return len(invocations)

    def _record_and_emit(
        self,
        invocation_id,
        envelope,
        event_type,
        payload,
        *,
        expected_epoch=None,
    ) -> None:
        outcome = TerminalOutcome(
            event_type=event_type,
            payload=dict(payload),
            execution_id=envelope.get("execution_id"),
            trace_id=envelope.get("trace_id"),
            expires_at=time.monotonic() + self._terminal_ttl,
        )
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return
            existing = self._terminal.get(invocation_id)
            if existing is not None:
                outcome = existing
            else:
                self._terminal[invocation_id] = outcome
                self._terminal.move_to_end(invocation_id)
                self._purge_terminal_locked()
            # Keep principal reset serialized with the final send.  This avoids
            # leaking an old principal's result onto a newly authenticated
            # realtime generation.
            self._emit(invocation_id, outcome)

    def _emit(self, invocation_id: str, outcome: TerminalOutcome) -> None:
        try:
            kwargs = {"execution_id": outcome.execution_id, "trace_id": outcome.trace_id}
            if outcome.event_type == "capability.result":
                self.realtime.send_result(
                    invocation_id, outcome.payload.get("output"), **kwargs
                )
            elif outcome.event_type == "capability.cancelled":
                self.realtime.send_cancelled(invocation_id, **kwargs)
            else:
                self.realtime.send_error(invocation_id, **outcome.payload, **kwargs)
        except Exception:
            pass

    def _purge_terminal_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._terminal.items() if value.expires_at <= now]
        for key in expired:
            self._terminal.pop(key, None)
        while len(self._terminal) > self._max_terminal:
            self._terminal.popitem(last=False)

    @staticmethod
    def _error(code, message, *, retryable=False, details=None):
        return {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
        }

    def shutdown(self) -> None:
        self.reset_principal()
        self._executor.shutdown(wait=False, cancel_futures=True)
