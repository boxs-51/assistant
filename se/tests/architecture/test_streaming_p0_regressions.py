import asyncio
import json
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.bus import (
    EventBus,
    EventDispatcher,
    EventPriority,
)
from se.src.infrastructure.event_bus.registry import EventRegistry
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.loop import AgentLoopState
from se.src.runtimes.agent.contracts.result import AgentExecutionResult
from se.src.runtimes.workflow.runtime import WorkflowRuntime


class _RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return True


@pytest.mark.asyncio
async def test_agent_stream_chunk_payload_is_json_serializable():
    """Regression for mappingproxy leaking from InferenceMessage.content."""
    agent = AgentDefinition(
        name="stream-agent",
        goal="test",
        instruction="test",
    )

    # InferenceMessage deliberately deep-freezes nested mappings/lists.
    final_message = InferenceMessage(
        role="assistant",
        content=[
            {
                "type": "text",
                "text": None,
                "data": {
                    "data": "hello",
                    "format": "structured",
                    "encoding": None,
                },
            }
        ],
    )

    class _AgentRegistry:
        def get(self, name):
            return agent if name == agent.name else None

    class _AgentRuntime:
        async def execute(self, context):
            return AgentExecutionResult(
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                state=AgentLoopState.COMPLETED,
                final_message=final_message,
                usage=InferenceUsage(),
            )

    class _Authorization:
        def is_allowed(self, identity, definition):
            return True

    bus = _RecordingBus()
    runtime = WorkflowRuntime()
    runtime.event_bus = bus
    runtime.container = SimpleNamespace(
        capability_runtime=None,
        authorization_service=_Authorization(),
        agent_registry=_AgentRegistry(),
        agent_runtime=_AgentRuntime(),
    )

    identity = Identity(auth_type="guest", user_id="user-1")
    await runtime._execute_agent(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-1",
            turn_id="turn-1",
            payload={"identity": identity.model_dump()},
        ),
        {
            "agent_id": agent.name,
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {},
            "config": {"stream": True},
        },
    )

    chunk_event = next(
        event
        for event in bus.events
        if event.event_name == "provider.stream.chunk_emitted"
    )
    chunk = chunk_event.payload["chunk"]

    # Must not raise TypeError: mappingproxy is not JSON serializable.
    json.dumps(chunk)

    content = chunk["choices"][0]["delta"]["content"]
    assert isinstance(content, str)
    assert content == "hello"


class _DispatcherContainer:
    def __init__(self, bus):
        self.event_bus = bus

    def get_dependency(self, _annotation):
        return None


@pytest.mark.asyncio
async def test_dlq_publish_does_not_mask_original_handler_exception():
    """Regression for asyncio.create_task(EventBus.publish(...Future...))."""
    registry = EventRegistry()
    bus = EventBus(
        registry,
        {"system.event.failed": EventPriority.HIGH},
    )
    dispatcher = EventDispatcher(
        registry=registry,
        queue=bus.queue,
        dependency_container=_DispatcherContainer(bus),
        cache_driver=None,
        uow_factory=lambda: None,
        max_retries=1,
    )

    async def failing_handler(event):
        raise RuntimeError("original-handler-error")

    source_event = BaseEvent(
        event_name="provider.stream.chunk_emitted",
        session_id="session-1",
        turn_id="turn-1",
        payload={"chunk": {"ok": True}},
    )

    with pytest.raises(RuntimeError, match="original-handler-error"):
        await dispatcher._execute_handler(failing_handler, source_event)

    priority, _sequence, dlq_event, dlq_future = bus.queue.get_nowait()
    assert priority == EventPriority.HIGH
    assert dlq_event.event_name == "system.event.failed"
    assert dlq_event.payload["failed_handler"] == "failing_handler"
    assert dlq_event.payload["error_message"] == "original-handler-error"
    dlq_future.cancel()


@pytest.mark.asyncio
async def test_dispatcher_does_not_resurrect_cancelled_publish_future():
    registry = EventRegistry()
    bus = EventBus(registry, {"test.event": EventPriority.NORMAL})
    handled = asyncio.Event()

    async def handler(event):
        handled.set()

    registry.register("test.event", handler)
    dispatcher = EventDispatcher(
        registry=registry,
        queue=bus.queue,
        dependency_container=_DispatcherContainer(bus),
        cache_driver=None,
        uow_factory=lambda: None,
        max_retries=1,
    )

    future = bus.publish(
        BaseEvent(event_name="test.event", session_id="session-1")
    )
    future.cancel()

    _priority, _sequence, event, queued_future = bus.queue.get_nowait()
    assert queued_future is future

    await dispatcher._dispatch_event_task(event, queued_future)

    assert handled.is_set()
    assert future.cancelled()