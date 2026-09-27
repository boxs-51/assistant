import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.stream import AGENT_STREAM_EVENT_NAMES, AgentStreamEvent
from se.src.transport.gateway.api.v1.chat_router import chat_completions_proxy
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_nonstream_agent_response_wait_follows_execution_budget(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    class Bus:
        def subscribe(self, event_name, handler):
            pass

        def unsubscribe(self, event_name, handler):
            pass

        async def publish(self, event):
            return True

    observed = []

    async def wait_for(future, timeout):
        observed.append(timeout)
        future.cancel()
        return {"response": {"choices": []}}

    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)
    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "agent_enabled": True,
            "agent_limits": {"timeout_seconds": 600},
            "config": {"stream": False},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=Bus(),
        config=SimpleNamespace(provider=SimpleNamespace(timeout=60)),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )
    assert response == {"choices": []}
    assert observed == [605]


@pytest.mark.asyncio
async def test_nonstream_response_wait_timeout_has_transport_scope(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    class Bus:
        def subscribe(self, event_name, handler):
            pass

        def unsubscribe(self, event_name, handler):
            pass

        async def publish(self, event):
            return True

    async def wait_for(future, timeout):
        future.cancel()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)
    with pytest.raises(HTTPException) as raised:
        await chat_completions_proxy(
            _Request({
                "model": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": False},
            }),
            identity=Identity(auth_type="guest", user_id="user-1"),
            event_bus=Bus(),
            config=SimpleNamespace(provider=SimpleNamespace(timeout=60)),
            container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
        )
    assert raised.value.status_code == 504
    assert raised.value.detail["error_code"] == "GATEWAY_RESPONSE_TIMEOUT"
    assert raised.value.detail["timeout_scope"] == "response_wait"


class _Request:
    def __init__(self, body, *, disconnected=False):
        self._body = body
        self._disconnected = disconnected

    async def json(self):
        return self._body

    async def is_disconnected(self):
        return self._disconnected


class _SseBus:
    def __init__(self, *, agent_activity=False):
        self.handlers = defaultdict(list)
        self.tasks = []
        self.futures = []
        self.agent_activity = agent_activity

    def subscribe(self, event_name, handler):
        self.handlers[event_name].append(handler)

    def unsubscribe(self, event_name, handler):
        if handler in self.handlers[event_name]:
            self.handlers[event_name].remove(handler)

    async def _emit(self, event):
        for handler in list(self.handlers[event.event_name]):
            await handler(event)

    def publish(self, event):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.futures.append(future)

        async def run():
            try:
                if event.event_name == "transport.event.request_received":
                    if self.agent_activity:
                        await self._emit(BaseEvent(
                            event_name="agent.tool.requested",
                            session_id="other-session",
                            turn_id=event.turn_id,
                            payload={"correlation": {"execution_id": "other"}},
                        ))
                        await self._emit(BaseEvent(
                            event_name="agent.tool.requested",
                            session_id=event.session_id,
                            turn_id="other-turn",
                            payload={"correlation": {"execution_id": "other"}},
                        ))
                        progress_count = 0
                        for name in (
                            "agent.inference.requested",
                            "agent.progress",
                            "agent.tool.requested",
                            "agent.tool.started",
                            "agent.tool.completed",
                            "agent.progress",
                            "agent.execution.completed",
                        ):
                            if name == "agent.progress":
                                progress_count += 1
                            await self._emit(BaseEvent(
                                event_name=name,
                                session_id=event.session_id,
                                turn_id=event.turn_id,
                                payload={
                                    "correlation": {"execution_id": "exec-1", "tool_call_id": "call-1"},
                                    "capability_id": "skill.load",
                                    "purpose": "Load instructions for skill web-research",
                                    "arguments": {"skill_id": "web-research"},
                                    "content": (
                                        "I will load the skill first."
                                        if progress_count == 1 else "The skill is ready."
                                    ) if name == "agent.progress" else None,
                                    "tool_calls": ([{
                                        "tool_call_id": "call-1",
                                        "name": "skill.load",
                                        "purpose": "Load instructions for skill web-research",
                                        "arguments": {"skill_id": "web-research"},
                                    }] if progress_count == 1 else []) if name == "agent.progress" else None,
                                },
                            ))
                    await self._emit(
                        BaseEvent(
                            event_name="provider.stream.chunk_emitted",
                            session_id=event.session_id,
                            turn_id=event.turn_id,
                            payload={
                                "chunk": {
                                    "id": "chunk-1",
                                    "model": "mock",
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": "hello"},
                                        }
                                    ],
                                }
                            },
                        )
                    )
                    await self._emit(
                        BaseEvent(
                            event_name="provider.stream.completed",
                            session_id=event.session_id,
                            turn_id=event.turn_id,
                            payload={},
                        )
                    )
                else:
                    await self._emit(event)
                if not future.done():
                    future.set_result(True)
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)

        self.tasks.append(asyncio.create_task(run()))
        return future


def test_tool_activity_dto_requires_purpose():
    with pytest.raises(ValueError, match="non-empty purpose"):
        AgentStreamEvent(
            event_id="event-1",
            event_type="agent.tool.requested",
            timestamp=1.0,
            execution_id="exec-1",
            channel="tool",
            data={"tool_call_id": "call-1", "purpose": ""},
        )
    with pytest.raises(ValueError, match="non-empty purpose"):
        AgentStreamEvent(
            event_id="event-2",
            event_type="agent.response",
            timestamp=1.0,
            execution_id="exec-1",
            channel="response",
            data={"content": "", "tool_calls": [{"name": "skill.load"}]},
        )


@pytest.mark.asyncio
async def test_sse_bridge_emits_chunk_done_and_unsubscribes():
    bus = _SseBus()
    request = _Request(
        {
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }
    )
    identity = Identity(auth_type="guest", user_id="user-1")
    config = SimpleNamespace(provider=SimpleNamespace(timeout=5))
    container = SimpleNamespace(
        connection_runtime=SimpleNamespace(registry=None)
    )

    response = await chat_completions_proxy(
        request,
        identity=identity,
        event_bus=bus,
        config=config,
        container=container,
    )

    parts = [part async for part in response.body_iterator]
    await asyncio.gather(*bus.tasks)

    assert parts[0] == ": ping\n\n"
    assert parts[-1] == "data: [DONE]\n\n"

    data_frames = [
        part
        for part in parts
        if part.startswith("data: ") and part != "data: [DONE]\n\n"
    ]
    assert len(data_frames) == 1
    payload = json.loads(data_frames[0][len("data: "):])
    assert payload["choices"][0]["delta"]["content"] == "hello"

    assert not bus.handlers["provider.stream.chunk_emitted"]
    assert not bus.handlers["provider.stream.completed"]
    assert not bus.handlers["provider.failed"]


@pytest.mark.asyncio
async def test_agent_activity_is_separate_from_final_chunk_and_scoped_to_turn():
    assert AGENT_STREAM_EVENT_NAMES == (
        "agent.progress", "agent.tool.requested", "agent.tool.started",
        "agent.tool.completed", "agent.tool.failed",
    )
    bus = _SseBus(agent_activity=True)
    request = _Request({
        "model": "mock",
        "messages": [{"role": "user", "content": "research"}],
        "agent_enabled": True,
        "config": {"stream": True, "agent_activity_stream": True},
    })
    response = await chat_completions_proxy(
        request,
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=SimpleNamespace(provider=SimpleNamespace(timeout=5)),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )
    frames = [part async for part in response.body_iterator]
    await asyncio.gather(*bus.tasks)
    payloads = [json.loads(part[6:]) for part in frames if part.startswith("data: {")]
    activities = [item for item in payloads if item.get("object") == "agent_stream_event"]
    assert [item["event_type"] for item in activities] == [
        "agent.response", "agent.tool.requested", "agent.tool.started",
        "agent.tool.completed", "agent.response",
    ]
    assert all(item["execution_id"] == "exec-1" for item in activities)
    assert activities[0]["channel"] == "response"
    assert activities[0]["data"] == {
        "content": "I will load the skill first.",
        "tool_calls": [{
            "tool_call_id": "call-1",
            "name": "skill.load",
            "purpose": "Load instructions for skill web-research",
            "arguments": {"skill_id": "web-research"},
        }],
        "final": False,
    }
    assert activities[-1]["data"] == {
        "content": "The skill is ready.", "tool_calls": [], "final": False,
    }
    assert activities[1]["data"] == {
        "tool_call_id": "call-1",
        "name": "skill.load",
        "purpose": "Load instructions for skill web-research",
        "arguments": {"skill_id": "web-research"},
        "status": "requested",
    }
    assert payloads[-1]["choices"][0]["delta"]["content"] == "hello"
    assert frames[-1] == "data: [DONE]\n\n"
    assert all(not bus.handlers[name] for name in AGENT_STREAM_EVENT_NAMES)
