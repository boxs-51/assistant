from __future__ import annotations

import inspect
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LocalInvocation:
    invocation_id: str
    capability_id: str
    cancellation_event: threading.Event
    future: Future


class CapabilityDispatcher:
    """
    Dispatches capability.invoke to local client implementations.

    Transport is intentionally not embedded here.
    """

    def __init__(
        self,
        registry,
        realtime,
        *,
        max_workers: int = 8,
    ) -> None:
        self.registry = registry
        self.realtime = realtime

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="capability-worker",
        )

        self._lock = threading.RLock()
        self._invocations: Dict[
            str,
            LocalInvocation,
        ] = {}

        self._terminal: set[str] = set()

    # ------------------------------------------------------------------
    # Incoming invocation
    # ------------------------------------------------------------------

    def dispatch(
        self,
        envelope: Dict[str, Any],
    ) -> None:
        invocation_id = envelope.get("invocation_id")
        connection_id = envelope.get("connection_id")

        if not invocation_id:
            return

        if connection_id != self.realtime.connection_id:
            self._send_error(
                invocation_id,
                "CONNECTION_MISMATCH",
                "Invocation connection_id does not match client connection.",
            )
            return

        payload = envelope.get("payload") or {}

        capability_id = payload.get("capability_id")
        arguments = payload.get("arguments") or {}

        if not isinstance(capability_id, str) or not capability_id:
            self._send_error(
                invocation_id,
                "INVALID_CAPABILITY_ID",
                "capability.invoke requires capability_id.",
            )
            return

        if not isinstance(arguments, dict):
            self._send_error(
                invocation_id,
                "INVALID_ARGUMENTS",
                "capability.invoke arguments must be an object.",
            )
            return

        with self._lock:
            if invocation_id in self._invocations:
                self._send_error(
                    invocation_id,
                    "DUPLICATE_INVOCATION",
                    f"Invocation '{invocation_id}' is already running.",
                )
                return

            if invocation_id in self._terminal:
                return

            cancellation_event = threading.Event()

            future = self._executor.submit(
                self._execute,
                invocation_id,
                capability_id,
                arguments,
                envelope,
                cancellation_event,
            )

            invocation = LocalInvocation(
                invocation_id=invocation_id,
                capability_id=capability_id,
                cancellation_event=cancellation_event,
                future=future,
            )

            self._invocations[invocation_id] = invocation

        future.add_done_callback(
            lambda completed: self._complete(
                invocation_id,
                completed,
            )
        )

    # ------------------------------------------------------------------
    # Local execution
    # ------------------------------------------------------------------

    def _execute(
        self,
        invocation_id: str,
        capability_id: str,
        arguments: Dict[str, Any],
        envelope: Dict[str, Any],
        cancellation_event: threading.Event,
    ) -> Any:
        implementation = self._resolve(
            capability_id
        )

        if implementation is None:
            raise LookupError(
                f"Capability '{capability_id}' "
                "is not executable on this client."
            )

        if cancellation_event.is_set():
            raise _InvocationCancelled()

        return self._call(
            implementation,
            arguments,
            envelope,
            cancellation_event,
        )

    def _resolve(
        self,
        capability_id: str,
    ) -> Any:
        tool = self.registry.tools.get(
            capability_id
        )

        if tool is None:
            tool = self.registry.get_tool(
                capability_id
            )

        if tool is None:
            return None

        if isinstance(tool, dict):
            return tool.get("func")

        if callable(tool):
            return tool

        return None

    @staticmethod
    def _call(
        target,
        arguments: Dict[str, Any],
        envelope: Dict[str, Any],
        cancellation_event: threading.Event,
    ) -> Any:
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            return target(**arguments)

        params = signature.parameters

        kwargs = dict(arguments)

        if (
            "invocation_id" in params
            or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        ):
            kwargs["invocation_id"] = envelope.get(
                "invocation_id"
            )

        if (
            "connection_id" in params
            or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        ):
            kwargs["connection_id"] = envelope.get(
                "connection_id"
            )

        if (
            "session_id" in params
            or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        ):
            kwargs["session_id"] = envelope.get(
                "session_id"
            )

        if (
            "cancel_event" in params
            or any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        ):
            kwargs["cancel_event"] = cancellation_event

        return target(**kwargs)

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def _complete(
        self,
        invocation_id: str,
        future: Future,
    ) -> None:
        with self._lock:
            invocation = self._invocations.pop(
                invocation_id,
                None,
            )

            if invocation is None:
                return

            if invocation_id in self._terminal:
                return

            self._terminal.add(invocation_id)

        if future.cancelled():
            self._send_cancelled(invocation_id)
            return

        try:
            result = future.result()
        except _InvocationCancelled:
            self._send_cancelled(invocation_id)
        except Exception as exc:
            self._send_error(
                invocation_id,
                type(exc).__name__,
                str(exc),
            )
        else:
            self._send_result(
                invocation_id,
                result,
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(
        self,
        invocation_id: str,
    ) -> bool:
        with self._lock:
            invocation = self._invocations.get(
                invocation_id
            )

        if invocation is None:
            return False

        invocation.cancellation_event.set()

        # Future.cancel() succeeds only if the worker has not started.
        cancelled = invocation.future.cancel()

        if cancelled:
            with self._lock:
                self._invocations.pop(
                    invocation_id,
                    None,
                )
                self._terminal.add(
                    invocation_id
                )

            self._send_cancelled(
                invocation_id
            )

        return True

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def fail_all(
        self,
        reason: str = "Client connection disconnected.",
    ) -> int:
        with self._lock:
            invocations = list(
                self._invocations.values()
            )
            self._invocations.clear()

            for invocation in invocations:
                self._terminal.add(
                    invocation.invocation_id
                )

        for invocation in invocations:
            invocation.cancellation_event.set()
            invocation.future.cancel()

        return len(invocations)

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    def _send_result(
        self,
        invocation_id: str,
        result: Any,
    ) -> None:
        try:
            self.realtime.send_result(
                invocation_id,
                result,
            )
        except Exception:
            pass

    def _send_error(
        self,
        invocation_id: str,
        code: str,
        message: str,
    ) -> None:
        with self._lock:
            if invocation_id in self._terminal:
                return
            self._terminal.add(
                invocation_id
            )

        try:
            self.realtime.send_error(
                invocation_id,
                code=code,
                message=message,
            )
        except Exception:
            pass

    def _send_cancelled(
        self,
        invocation_id: str,
    ) -> None:
        try:
            self.realtime.send_cancelled(
                invocation_id
            )
        except Exception:
            pass

    def shutdown(self) -> None:
        self.fail_all()
        self._executor.shutdown(
            wait=False,
            cancel_futures=True,
        )


class _InvocationCancelled(Exception):
    pass