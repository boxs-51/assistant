from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Mapping

from ...connection.protocol import RealtimeEnvelope
from ...connection.realtime import RealtimeMultiplexer
from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition
from .base import BaseCapabilityDriver


class RemoteClientDriver(BaseCapabilityDriver):
    """Execute one capability implementation through an active client connection."""

    def __init__(
        self,
        definition: CapabilityDefinition,
        realtime: RealtimeMultiplexer,
        connection_id: str,
        dispatch_started_handler: (
            Callable[[RealtimeEnvelope], Awaitable[None] | None] | None
        ) = None,
    ) -> None:
        super().__init__(definition)
        if not connection_id:
            raise ValueError("RemoteClientDriver requires connection_id")
        self._realtime = realtime
        self._connection_id = connection_id
        self._dispatch_started_handler = dispatch_started_handler

    @property
    def connection_id(self) -> str:
        return self._connection_id

    def set_dispatch_started_handler(
        self,
        handler: Callable[[RealtimeEnvelope], Awaitable[None] | None] | None,
    ) -> None:
        self._dispatch_started_handler = handler

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        if context.cancelled:
            raise asyncio.CancelledError()
        if context.connection_id != self._connection_id:
            raise ValueError(
                "Capability execution connection_id does not match the selected "
                "remote implementation connection."
            )

        timeout = context.remaining_seconds
        if timeout is not None and timeout <= 0:
            raise asyncio.TimeoutError()

        envelope = RealtimeEnvelope(
            type="capability.invoke",
            message_id=f"msg-{uuid.uuid4().hex}",
            session_id=context.session_id,
            connection_id=context.connection_id,
            execution_id=context.execution_id,
            invocation_id=context.invocation_id,
            trace_id=context.trace_id or context.metadata.get("trace_id"),
            payload={
                "capability_id": self.name,
                "arguments": dict(arguments),
            },
        )

        try:
            if self._dispatch_started_handler is not None:
                observed = self._dispatch_started_handler(envelope)
                if asyncio.iscoroutine(observed):
                    await observed
            return await self._realtime.invoke(
                envelope,
                timeout=timeout,
            )
        except asyncio.CancelledError:
            await self._realtime.cancel(
                self._connection_id,
                context.invocation_id,
            )
            raise