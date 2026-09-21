"""Phase 6.3 realtime invocation multiplexing.

This module is the transport-facing boundary between ConnectionRegistry and
remote capability invocation. AgentRuntime is intentionally absent here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from .multiplexer import ConnectionMultiplexer, RemoteConnectionLost
from .protocol import RealtimeEnvelope
from .registry import ConnectionRegistry


class RemoteCapabilityError(RuntimeError):
    """Structured remote client capability execution error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "REMOTE_CAPABILITY_ERROR",
        details: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.retryable = bool(retryable)


ProgressHandler = Callable[[RealtimeEnvelope], Awaitable[None] | None]


class RealtimeMultiplexer:
    """Multiplex invocations over active connection sockets."""

    TERMINAL_TYPES = {
        "capability.result",
        "capability.error",
        "capability.cancelled",
    }

    def __init__(
        self,
        registry: ConnectionRegistry,
        *,
        multiplexer: Optional[ConnectionMultiplexer] = None,
        default_timeout: float = 60.0,
        progress_handler: Optional[ProgressHandler] = None,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be > 0")

        self.registry = registry
        self.multiplexer = multiplexer or ConnectionMultiplexer()
        self.default_timeout = float(default_timeout)
        self.progress_handler = progress_handler

    @staticmethod
    def _drain_owned_future(
        future: asyncio.Future[Any],
    ) -> None:
        """Release caller ownership of one correlation future.

        A Future completed with an exception must have that exception
        retrieved before the final reference is dropped, otherwise asyncio
        reports "Future exception was never retrieved".  If the Future is
        still pending, cancellation is the correct local ownership release;
        it does not imply any rollback of a remote side effect.
        """
        if future.cancelled():
            return
        if future.done():
            future.exception()
            return
        future.cancel()

    async def _abandon_pending(
        self,
        invocation_id: str,
        connection_id: str,
        future: asyncio.Future[Any],
    ) -> None:
        """Remove local correlation ownership without remote semantics."""
        await self.multiplexer.cancel(
            invocation_id,
            connection_id,
        )
        self._drain_owned_future(future)

    async def _cancel_remote_and_abandon(
        self,
        connection_id: str,
        invocation_id: str,
        future: asyncio.Future[Any],
    ) -> None:
        """Best-effort remote cancel plus deterministic local cleanup."""
        try:
            await self.cancel(
                connection_id,
                invocation_id,
            )
        except Exception:
            # Cancellation cleanup must not replace the caller's original
            # TimeoutError/CancelledError with a secondary transport failure.
            pass
        finally:
            await self._abandon_pending(
                invocation_id,
                connection_id,
                future,
            )

    async def invoke(
        self,
        envelope: RealtimeEnvelope,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        if envelope.type != "capability.invoke":
            raise ValueError("RealtimeMultiplexer.invoke requires capability.invoke")
        if not envelope.connection_id:
            raise ValueError("capability.invoke requires connection_id")
        if not envelope.invocation_id:
            raise ValueError("capability.invoke requires invocation_id")

        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )
        if effective_timeout <= 0:
            raise TimeoutError("Realtime invocation timeout must be > 0")

        socket = self.registry.require_active_socket(envelope.connection_id)
        future = await self.multiplexer.register(
            envelope.invocation_id,
            envelope.connection_id,
        )

        try:
            await socket.send_json(
                envelope.model_dump(mode="json")
            )
        except asyncio.CancelledError:
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise
        except Exception as exc:
            # Once send_json has been attempted the server cannot prove that
            # the peer received zero bytes.  Normalize the transport failure
            # to the R6 "may have executed" boundary.
            failure = RemoteConnectionLost(
                envelope.connection_id,
                envelope.invocation_id,
            )
            # Do not reject the Future and then raise the same failure
            # directly: that leaves an exception-bearing Future with no
            # remaining awaiter.  The caller owns the direct exception path,
            # so abandon/cancel the local correlation Future instead.
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise failure from exc

        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            await self._cancel_remote_and_abandon(
                envelope.connection_id,
                envelope.invocation_id,
                future,
            )
            raise
        except asyncio.CancelledError:
            await self._cancel_remote_and_abandon(
                envelope.connection_id,
                envelope.invocation_id,
                future,
            )
            raise

    async def reconcile(
        self,
        envelope: RealtimeEnvelope,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Query client-side invocation state without granting execution."""
        if envelope.type != "capability.reconcile":
            raise ValueError(
                "RealtimeMultiplexer.reconcile requires capability.reconcile"
            )
        if not envelope.connection_id:
            raise ValueError("capability.reconcile requires connection_id")
        if not envelope.invocation_id:
            raise ValueError("capability.reconcile requires invocation_id")

        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )
        if effective_timeout <= 0:
            raise TimeoutError("Realtime reconciliation timeout must be > 0")

        socket = self.registry.require_active_socket(
            envelope.connection_id
        )
        future = await self.multiplexer.register(
            envelope.invocation_id,
            envelope.connection_id,
        )
        try:
            await socket.send_json(
                envelope.model_dump(mode="json")
            )
        except asyncio.CancelledError:
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise
        except Exception as exc:
            failure = RemoteConnectionLost(
                envelope.connection_id,
                envelope.invocation_id,
            )
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise failure from exc

        try:
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=effective_timeout,
            )
            return dict(result)
        except asyncio.TimeoutError:
            # Reconciliation timeout only abandons the query.  It must never
            # send capability.cancel because reconcile carries no execution
            # permission.
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise
        except asyncio.CancelledError:
            # Same rule as timeout: reconciliation is query-only and caller
            # cancellation must not be converted into capability.cancel.
            await self._abandon_pending(
                envelope.invocation_id,
                envelope.connection_id,
                future,
            )
            raise

    async def cancel(
        self,
        connection_id: str,
        invocation_id: str,
    ) -> bool:
        cancelled = await self.multiplexer.cancel(invocation_id)
        if not cancelled:
            return False

        socket = self.registry.get_socket(connection_id)
        if socket is None or not self.registry.is_active(connection_id):
            return True

        envelope = RealtimeEnvelope(
            type="capability.cancel",
            message_id=f"cancel-{invocation_id}",
            session_id=self.registry.session_for_connection(connection_id),
            connection_id=connection_id,
            invocation_id=invocation_id,
            payload={},
        )
        await socket.send_json(envelope.model_dump(mode="json"))
        return True

    async def handle_inbound(
        self,
        connection_id: str,
        envelope: RealtimeEnvelope,
    ) -> bool:
        correlated_types = {
            "capability.result",
            "capability.error",
            "capability.cancelled",
            "capability.progress",
            "capability.reconciliation",
        }
        if envelope.type in correlated_types and envelope.connection_id != connection_id:
            raise ValueError(
                "Realtime envelope connection_id does not match transport connection"
            )

        if envelope.type == "capability.result":
            return await self.multiplexer.resolve(
                envelope.invocation_id or "",
                envelope.payload.get("output"),
                connection_id,
            )

        if envelope.type == "capability.error":
            message = str(envelope.payload.get("message", "Remote capability failed"))
            return await self.multiplexer.reject(
                envelope.invocation_id or "",
                RemoteCapabilityError(
                    message,
                    code=str(envelope.payload.get("code", "REMOTE_CAPABILITY_ERROR")),
                    details=envelope.payload.get("details"),
                    retryable=bool(envelope.payload.get("retryable", False)),
                ),
                connection_id,
            )

        if envelope.type == "capability.cancelled":
            return await self.multiplexer.cancel(
                envelope.invocation_id or "",
                connection_id,
            )

        if envelope.type == "capability.reconciliation":
            return await self.multiplexer.resolve(
                envelope.invocation_id or "",
                dict(envelope.payload),
                connection_id,
            )

        if envelope.type == "capability.progress":
            if self.progress_handler is None:
                return False
            if await self.multiplexer.connection_for_invocation(
                envelope.invocation_id or ""
            ) != connection_id:
                return False
            result = self.progress_handler(envelope)
            if asyncio.iscoroutine(result):
                await result
            return True

        if envelope.type == "connection.heartbeat":
            self.registry.heartbeat(connection_id)
            return True

        return False

    async def disconnect(
        self,
        connection_id: str,
        error: Optional[BaseException] = None,
    ) -> int:
        return await self.multiplexer.fail_connection(
            connection_id,
            error,
        )
