"""Phase 6.3 realtime invocation multiplexing.

This module is the transport-facing boundary between ConnectionRegistry and
remote capability invocation. AgentRuntime is intentionally absent here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from .multiplexer import ConnectionMultiplexer
from .protocol import RealtimeEnvelope
from .registry import ConnectionRegistry


class RemoteCapabilityError(RuntimeError):
    """Remote client reported a capability execution error."""


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

        socket = self.registry.require_active_socket(envelope.connection_id)
        future = await self.multiplexer.register(
            envelope.invocation_id,
            envelope.connection_id,
        )

        try:
            await socket.send_json(
                envelope.model_dump(mode="json")
            )
        except BaseException as exc:
            await self.multiplexer.reject(envelope.invocation_id, exc)
            raise

        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )
        if effective_timeout <= 0:
            await self.cancel(envelope.connection_id, envelope.invocation_id)
            raise TimeoutError("Realtime invocation timeout must be > 0")

        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            await self.cancel(
                envelope.connection_id,
                envelope.invocation_id,
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
        if envelope.connection_id and envelope.connection_id != connection_id:
            raise ValueError(
                "Realtime envelope connection_id does not match transport connection"
            )

        if envelope.type == "capability.result":
            return await self.multiplexer.resolve(
                envelope.invocation_id or "",
                envelope.payload,
            )

        if envelope.type == "capability.error":
            message = str(envelope.payload.get("message", "Remote capability failed"))
            return await self.multiplexer.reject(
                envelope.invocation_id or "",
                RemoteCapabilityError(message),
            )

        if envelope.type == "capability.cancelled":
            return await self.multiplexer.cancel(
                envelope.invocation_id or "",
            )

        if envelope.type == "capability.progress":
            if self.progress_handler is None:
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
        failure = error or ConnectionError(
            f"Connection '{connection_id}' disconnected"
        )
        return await self.multiplexer.fail_connection(
            connection_id,
            failure,
        )
