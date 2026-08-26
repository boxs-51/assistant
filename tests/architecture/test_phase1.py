import asyncio

import pytest
from src.application.container import ApplicationContainer
from src.infrastructure.event_bus.bus import EventBus, EventPriority
from src.infrastructure.event_bus.registry import EventRegistry
from src.infrastructure.event_bus import subscribers
from src.domain.schemas.event import BaseEvent


class EventingStub:
    def __init__(self):
        self.bus = object()


def test_application_container_resolves_registered_dependencies():
    provider_runtime = object()
    container = ApplicationContainer(
        config={},
        storage=object(),
        uow_factory=lambda: None,
        http_client=object(),
        eventing_manager=EventingStub(),
        provider_runtime=provider_runtime,
    )

    assert container.require("provider_runtime") is provider_runtime
    assert container.get("missing", "fallback") == "fallback"


def test_builtin_subscribers_use_the_shared_registry():
    registry = EventRegistry()
    subscribers.register_subscribers(registry)

    assert len(registry.get_handlers("user.created")) == 2
    assert len(registry.get_handlers("system.event.failed")) == 2
    assert len(registry.get_handlers("unknown.event")) == 1

@pytest.mark.asyncio
async def test_equal_priority_events_are_orderable_without_comparing_events():
    registry = EventRegistry()
    bus = EventBus(registry, {"test.event": EventPriority.NORMAL})

    first = BaseEvent(event_name="test.event", session_id=None)
    second = BaseEvent(event_name="test.event", session_id=None)
    bus.publish(first)
    bus.publish(second)

    first_item = bus.queue.get_nowait()
    second_item = bus.queue.get_nowait()
    assert first_item[0] == second_item[0] == EventPriority.NORMAL
    assert first_item[1] < second_item[1]
    assert first_item[2] is first
    assert second_item[2] is second
