from __future__ import annotations

from typing import Any

from ...domain.schemas.event import BaseEvent
from .contracts.events import AgentEventEnvelope


class EventBusAgentEventPublisher:
    """Adapt agent event envelopes to the gateway EventBus contract."""

    def __init__(self, event_bus) -> None:
        self._event_bus = event_bus

    async def publish(self, event: AgentEventEnvelope) -> None:
        base_event = BaseEvent(
            event_id=event.event_id,
            event_name=event.event_name,
            session_id=event.correlation.session_id or event.correlation.execution_id,
            timestamp=event.timestamp,
            payload={
                "correlation": event.correlation.model_dump(mode="json"),
                **event.payload,
            },
        )
        future = self._event_bus.publish(base_event)
        if future is not None:
            await future