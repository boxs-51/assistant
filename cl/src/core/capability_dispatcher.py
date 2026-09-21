from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict

from .client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerConflict,
    ClientInvocationLedgerState,
    ClientInvocationRecord,
)
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
    capability_id: str | None
    capability_version: str | None
    request_fingerprint: str | None
    expires_at: float


@dataclass
class LocalInvocation:
    invocation_id: str
    capability_id: str
    capability_version: str
    request_fingerprint: str
    client_id: str | None
    principal_id: str | None
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
        invocation_ledger: ClientInvocationLedger | None = None,
        client_id: str | None = None,
        principal_id: str | None = None,
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
        self.invocation_ledger = invocation_ledger
        self._client_id = client_id
        self._principal_id = principal_id

    def set_realtime(self, realtime) -> None:
        self.realtime = realtime

    def set_identity(
        self,
        client_id: str | None,
        principal_id: str | None,
    ) -> None:
        with self._lock:
            self._client_id = client_id
            self._principal_id = principal_id

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

        local_version = self._capability_version(capability_id)
        capability_version = str(
            payload.get("capability_version") or local_version
        )
        request_fingerprint = self._request_fingerprint(
            capability_id,
            capability_version,
            arguments,
        )
        declared_fingerprint = payload.get("request_fingerprint")
        if capability_version != local_version or (
            declared_fingerprint is not None
            and declared_fingerprint != request_fingerprint
        ):
            self._emit_conflict(invocation_id, envelope)
            return
        if declared_fingerprint is not None:
            request_fingerprint = str(declared_fingerprint)
        idempotency = self._capability_idempotency(capability_id)

        with self._lock:
            self._purge_terminal_locked()
            existing = self._invocations.get(invocation_id)
            if existing is not None:
                if not self._same_semantics(
                    existing.capability_id,
                    existing.capability_version,
                    existing.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ):
                    self._emit_conflict(invocation_id, envelope)
                return
            previous = self._terminal.get(invocation_id)
            if previous is not None:
                if not self._same_semantics(
                    previous.capability_id,
                    previous.capability_version,
                    previous.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ):
                    self._emit_conflict(invocation_id, envelope)
                    return
                self._emit(invocation_id, previous)
                return
            durable = self._ledger_get(invocation_id)
            if durable is not None:
                if not self._same_semantics(
                    durable.capability_id,
                    durable.capability_version,
                    durable.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ) or durable.idempotency != idempotency:
                    self._emit_conflict(invocation_id, envelope)
                    return
                if durable.state is ClientInvocationLedgerState.TERMINAL:
                    outcome = self._terminal_from_record(
                        durable,
                        envelope,
                    )
                    self._terminal[invocation_id] = outcome
                    self._terminal.move_to_end(invocation_id)
                    self._purge_terminal_locked()
                    self._emit(invocation_id, outcome)
                    return
                if (
                    durable.state is ClientInvocationLedgerState.RUNNING
                    and not self._durable_running_replay_safe(idempotency)
                ):
                    # A prior process may have entered the target call.  Only
                    # replay-safe semantics may deliberately re-enter after a
                    # process restart; unsafe/unknown effects stay blocked.
                    return
            if not self._capacity.acquire(blocking=False):
                self._record_and_emit(
                    invocation_id, envelope, "capability.error",
                    self._error("CLIENT_BUSY", "Local capability queue is full.", retryable=True),
                    capability_id=capability_id,
                    capability_version=capability_version,
                    request_fingerprint=request_fingerprint,
                )
                return
            ledger_identity = self._ledger_identity()
            if ledger_identity is not None:
                try:
                    durable = self.invocation_ledger.prepare(
                        client_id=ledger_identity[0],
                        principal_id=ledger_identity[1],
                        invocation_id=invocation_id,
                        capability_id=capability_id,
                        capability_version=capability_version,
                        request_fingerprint=request_fingerprint,
                        idempotency=idempotency,
                    )
                except ClientInvocationLedgerConflict:
                    self._capacity.release()
                    self._emit_conflict(invocation_id, envelope)
                    return
                if durable.state is ClientInvocationLedgerState.TERMINAL:
                    self._capacity.release()
                    outcome = self._terminal_from_record(
                        durable,
                        envelope,
                    )
                    self._terminal[invocation_id] = outcome
                    self._terminal.move_to_end(invocation_id)
                    self._purge_terminal_locked()
                    self._emit(invocation_id, outcome)
                    return
                if (
                    durable.state is ClientInvocationLedgerState.RUNNING
                    and not self._durable_running_replay_safe(idempotency)
                ):
                    self._capacity.release()
                    return
            cancellation_event = threading.Event()
            try:
                future = self._executor.submit(
                    self._execute_local,
                    capability_id,
                    arguments,
                    envelope,
                    cancellation_event,
                    ledger_identity,
                )
            except BaseException:
                self._capacity.release()
                raise
            self._invocations[invocation_id] = LocalInvocation(
                invocation_id,
                capability_id,
                capability_version,
                request_fingerprint,
                (
                    ledger_identity[0]
                    if ledger_identity is not None
                    else None
                ),
                (
                    ledger_identity[1]
                    if ledger_identity is not None
                    else None
                ),
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
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            request_fingerprint=invocation.request_fingerprint,
            client_id=invocation.client_id,
            principal_id=invocation.principal_id,
        )

    @staticmethod
    def _durable_running_replay_safe(idempotency: str) -> bool:
        return idempotency in {"IDEMPOTENT", "DEDUPLICATED"}

    def _execute_local(
        self,
        capability_id,
        arguments,
        envelope,
        cancellation_event,
        ledger_identity,
    ):
        before_target_call = None
        if ledger_identity is not None and self.invocation_ledger is not None:
            client_id, principal_id = ledger_identity
            invocation_id = envelope.get("invocation_id")

            def mark_running() -> None:
                self.invocation_ledger.mark_running(
                    client_id=client_id,
                    principal_id=principal_id,
                    invocation_id=invocation_id,
                )

            before_target_call = mark_running

        return self.local_executor.execute(
            capability_id,
            arguments,
            envelope,
            cancellation_event,
            before_target_call=before_target_call,
        )

    def reconcile(self, envelope: Dict[str, Any]) -> None:
        invocation_id = envelope.get("invocation_id")
        if not invocation_id:
            return
        if envelope.get("connection_id") != self.realtime.connection_id:
            return
        payload = envelope.get("payload") or {}
        capability_id = payload.get("capability_id")
        capability_version = payload.get("capability_version")
        request_fingerprint = payload.get("request_fingerprint")
        if not all(
            isinstance(value, str) and value
            for value in (
                capability_id,
                capability_version,
                request_fingerprint,
            )
        ):
            return

        with self._lock:
            self._purge_terminal_locked()
            running = self._invocations.get(invocation_id)
            terminal = self._terminal.get(invocation_id)

            if running is not None:
                if self._same_semantics(
                    running.capability_id,
                    running.capability_version,
                    running.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ):
                    response = {
                        "status": "RUNNING",
                        "capability_id": running.capability_id,
                        "capability_version": running.capability_version,
                        "request_fingerprint": running.request_fingerprint,
                    }
                else:
                    response = self._conflict_reconciliation(
                        capability_id,
                        capability_version,
                        request_fingerprint,
                    )
            elif terminal is not None:
                if self._same_semantics(
                    terminal.capability_id,
                    terminal.capability_version,
                    terminal.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ):
                    terminal_type = {
                        "capability.result": "result",
                        "capability.error": "error",
                        "capability.cancelled": "cancelled",
                    }[terminal.event_type]
                    response = {
                        "status": "TERMINAL",
                        "capability_id": terminal.capability_id,
                        "capability_version": terminal.capability_version,
                        "request_fingerprint": terminal.request_fingerprint,
                        "terminal_type": terminal_type,
                        "terminal_payload": dict(terminal.payload),
                    }
                else:
                    response = self._conflict_reconciliation(
                        capability_id,
                        capability_version,
                        request_fingerprint,
                    )
            else:
                durable = self._ledger_get(invocation_id)
                if durable is not None and not self._same_semantics(
                    durable.capability_id,
                    durable.capability_version,
                    durable.request_fingerprint,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                ):
                    response = self._conflict_reconciliation(
                        capability_id,
                        capability_version,
                        request_fingerprint,
                    )
                elif (
                    durable is not None
                    and durable.state
                    is ClientInvocationLedgerState.TERMINAL
                ):
                    response = {
                        "status": "TERMINAL",
                        "capability_id": durable.capability_id,
                        "capability_version": durable.capability_version,
                        "request_fingerprint": durable.request_fingerprint,
                        "terminal_type": durable.terminal_type,
                        "terminal_payload": dict(
                            durable.terminal_payload or {}
                        ),
                        "ledger_state": durable.state.value,
                    }
                elif durable is not None:
                    response = {
                        "status": "UNKNOWN",
                        "capability_id": durable.capability_id,
                        "capability_version": durable.capability_version,
                        "request_fingerprint": durable.request_fingerprint,
                        "ledger_state": durable.state.value,
                    }
                else:
                    response = {
                        "status": "NOT_FOUND",
                        "capability_id": capability_id,
                        "capability_version": capability_version,
                        "request_fingerprint": request_fingerprint,
                    }

        try:
            self.realtime.send_reconciliation(
                invocation_id,
                response,
                execution_id=envelope.get("execution_id"),
                trace_id=envelope.get("trace_id"),
            )
        except Exception:
            pass

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
        capability_id=None,
        capability_version=None,
        request_fingerprint=None,
        client_id=None,
        principal_id=None,
    ) -> None:
        outcome = TerminalOutcome(
            event_type=event_type,
            payload=dict(payload),
            execution_id=envelope.get("execution_id"),
            trace_id=envelope.get("trace_id"),
            capability_id=capability_id,
            capability_version=capability_version,
            request_fingerprint=request_fingerprint,
            expires_at=time.monotonic() + self._terminal_ttl,
        )
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return
            if (
                self.invocation_ledger is not None
                and client_id
                and principal_id
                and capability_id
                and capability_version
                and request_fingerprint
            ):
                terminal_type = {
                    "capability.result": "result",
                    "capability.error": "error",
                    "capability.cancelled": "cancelled",
                }[event_type]
                try:
                    self.invocation_ledger.commit_terminal(
                        client_id=client_id,
                        principal_id=principal_id,
                        invocation_id=invocation_id,
                        terminal_type=terminal_type,
                        terminal_payload=dict(payload),
                    )
                except ClientInvocationLedgerConflict:
                    self._emit_conflict(invocation_id, envelope)
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

    def _capability_version(self, capability_id: str) -> str:
        tool = self.registry.tools.get(capability_id)
        metadata = (
            tool.get("metadata", {})
            if isinstance(tool, dict)
            else {}
        )
        return str(metadata.get("version", "1.0"))

    def _capability_idempotency(self, capability_id: str) -> str:
        tool = self.registry.tools.get(capability_id)
        metadata = (
            tool.get("metadata", {})
            if isinstance(tool, dict)
            else {}
        )
        return str(metadata.get("idempotency", "UNKNOWN"))

    def _ledger_identity(self) -> tuple[str, str] | None:
        if (
            self.invocation_ledger is None
            or not self._client_id
            or not self._principal_id
        ):
            return None
        return self._client_id, self._principal_id

    def _ledger_get(
        self,
        invocation_id: str,
    ) -> ClientInvocationRecord | None:
        identity = self._ledger_identity()
        if identity is None:
            return None
        return self.invocation_ledger.get(
            client_id=identity[0],
            principal_id=identity[1],
            invocation_id=invocation_id,
        )

    def _terminal_from_record(
        self,
        record: ClientInvocationRecord,
        envelope: Dict[str, Any],
    ) -> TerminalOutcome:
        event_type = {
            "result": "capability.result",
            "error": "capability.error",
            "cancelled": "capability.cancelled",
        }.get(record.terminal_type)
        if event_type is None:
            raise ClientInvocationLedgerConflict(
                "Durable terminal record has invalid terminal_type."
            )
        return TerminalOutcome(
            event_type=event_type,
            payload=dict(record.terminal_payload or {}),
            execution_id=envelope.get("execution_id"),
            trace_id=envelope.get("trace_id"),
            capability_id=record.capability_id,
            capability_version=record.capability_version,
            request_fingerprint=record.request_fingerprint,
            expires_at=time.monotonic() + self._terminal_ttl,
        )

    @staticmethod
    def _request_fingerprint(
        capability_id: str,
        capability_version: str,
        arguments: Dict[str, Any],
    ) -> str:
        encoded = json.dumps(
            {
                "capability_id": capability_id,
                "capability_version": capability_version,
                "arguments": dict(arguments),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _same_semantics(
        left_capability_id,
        left_version,
        left_fingerprint,
        right_capability_id,
        right_version,
        right_fingerprint,
    ) -> bool:
        return (
            left_capability_id == right_capability_id
            and left_version == right_version
            and left_fingerprint == right_fingerprint
        )

    def _emit_conflict(
        self,
        invocation_id: str,
        envelope: Dict[str, Any],
    ) -> None:
        try:
            self.realtime.send_error(
                invocation_id,
                code="REMOTE_INVOCATION_CONFLICT",
                message=(
                    "invocation_id is already bound to different request "
                    "semantics."
                ),
                details={},
                retryable=False,
                execution_id=envelope.get("execution_id"),
                trace_id=envelope.get("trace_id"),
            )
        except Exception:
            pass

    @staticmethod
    def _conflict_reconciliation(
        capability_id: str,
        capability_version: str,
        request_fingerprint: str,
    ) -> Dict[str, Any]:
        return {
            "status": "CONFLICT",
            "capability_id": capability_id,
            "capability_version": capability_version,
            "request_fingerprint": request_fingerprint,
        }

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
