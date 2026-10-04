import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.stream import AGENT_STREAM_EVENT_NAMES, AgentStreamEvent
from se.src.infrastructure.config.schemas import GatewaySettings
from se.src.transport.gateway.api.v1.chat_router import (
    _agent_stream_event_has_response_progress,
    _provider_chunk_has_response_progress,
    _response_timeout_deadline,
    chat_completions_proxy,
)
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


def test_gateway_response_timeout_settings_are_optional_and_positive():
    defaults = GatewaySettings()
    assert defaults.response_idle_timeout_seconds is None
    assert defaults.response_hard_timeout_seconds is None

    configured = GatewaySettings(
        response_idle_timeout_seconds=2.5,
        response_hard_timeout_seconds=10.0,
    )
    assert configured.response_idle_timeout_seconds == 2.5
    assert configured.response_hard_timeout_seconds == 10.0

    with pytest.raises(ValueError):
        GatewaySettings(response_idle_timeout_seconds=0)
    with pytest.raises(ValueError):
        GatewaySettings(response_hard_timeout_seconds=-1)


@pytest.mark.asyncio
async def test_nonstream_response_idle_timeout_projects_canonical_scope(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    class Bus:
        def subscribe(self, event_name, handler):
            pass

        def unsubscribe(self, event_name, handler):
            pass

        async def publish(self, event):
            return True

    observed = []

    async def wait_for(awaitable, timeout):
        observed.append(timeout)
        if hasattr(awaitable, "close"):
            awaitable.close()
        elif hasattr(awaitable, "cancel"):
            awaitable.cancel()
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
            config=SimpleNamespace(
                provider=SimpleNamespace(timeout=60),
                gateway=SimpleNamespace(
                    response_idle_timeout_seconds=3.0,
                    response_hard_timeout_seconds=10.0,
                ),
            ),
            container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
        )

    assert observed == [3.0]
    assert raised.value.status_code == 504
    assert raised.value.detail == {
        "error": "Gateway response idle timeout expired.",
        "error_code": "RESPONSE_IDLE_TIMEOUT",
        "timeout_scope": "response_idle",
        "timeout_seconds": 3.0,
    }


@pytest.mark.asyncio
async def test_nonstream_canonical_timeout_supersedes_shorter_legacy_wait(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    class Bus:
        def subscribe(self, event_name, handler):
            pass

        def unsubscribe(self, event_name, handler):
            pass

        async def publish(self, event):
            return True

    observed = []

    async def wait_for(awaitable, timeout):
        observed.append(timeout)
        if hasattr(awaitable, "close"):
            awaitable.close()
        elif hasattr(awaitable, "cancel"):
            awaitable.cancel()
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
            config=SimpleNamespace(
                provider=SimpleNamespace(timeout=1),
                gateway=SimpleNamespace(
                    response_idle_timeout_seconds=3.0,
                    response_hard_timeout_seconds=10.0,
                ),
            ),
            container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
        )

    assert observed == [3.0]
    assert raised.value.detail["error_code"] == "RESPONSE_IDLE_TIMEOUT"
    assert raised.value.detail["timeout_scope"] == "response_idle"


@pytest.mark.asyncio
async def test_nonstream_canonical_timeout_bounds_request_dispatch():
    class SlowBus:
        def subscribe(self, event_name, handler):
            pass

        def unsubscribe(self, event_name, handler):
            pass

        async def publish(self, event):
            await asyncio.sleep(0.1)
            return True

    with pytest.raises(HTTPException) as raised:
        await chat_completions_proxy(
            _Request({
                "model": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": False},
            }),
            identity=Identity(auth_type="guest", user_id="user-1"),
            event_bus=SlowBus(),
            config=SimpleNamespace(
                provider=SimpleNamespace(timeout=60),
                gateway=SimpleNamespace(
                    response_idle_timeout_seconds=0.01,
                    response_hard_timeout_seconds=1.0,
                ),
            ),
            container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
        )

    assert raised.value.detail["error_code"] == "RESPONSE_IDLE_TIMEOUT"
    assert raised.value.detail["timeout_scope"] == "response_idle"


@pytest.mark.asyncio
async def test_nonstream_canonical_timeout_preserves_provider_failure_precedence():
    class RacingBus:
        def __init__(self):
            self.handlers = defaultdict(list)
            self.tasks = []

        def subscribe(self, event_name, handler):
            self.handlers[event_name].append(handler)

        def unsubscribe(self, event_name, handler):
            if handler in self.handlers[event_name]:
                self.handlers[event_name].remove(handler)

        def publish(self, event):
            loop = asyncio.get_running_loop()
            dispatch_future = loop.create_future()

            async def emit_failure():
                await asyncio.sleep(0)
                failure = BaseEvent(
                    event_name="provider.failed",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={
                        "error": "provider deadline",
                        "error_code": "PROVIDER_CALL_TIMEOUT",
                        "failure_domain": "PROVIDER",
                        "retryable": False,
                        "timeout_scope": "provider_call",
                        "timeout_seconds": 0.5,
                        "status_code": 504,
                    },
                )
                for handler in list(self.handlers["provider.failed"]):
                    await handler(failure)

            self.tasks.append(asyncio.create_task(emit_failure()))
            return dispatch_future

    bus = RacingBus()
    with pytest.raises(HTTPException) as raised:
        await chat_completions_proxy(
            _Request({
                "model": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": False},
            }),
            identity=Identity(auth_type="guest", user_id="user-1"),
            event_bus=bus,
            config=SimpleNamespace(
                provider=SimpleNamespace(timeout=60),
                gateway=SimpleNamespace(
                    response_idle_timeout_seconds=1.0,
                    response_hard_timeout_seconds=2.0,
                ),
            ),
            container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
        )
    await asyncio.gather(*bus.tasks)

    assert raised.value.status_code == 504
    assert raised.value.detail["error_code"] == "PROVIDER_CALL_TIMEOUT"
    assert raised.value.detail["timeout_scope"] == "provider_call"


def test_response_timeout_deadline_preserves_hard_ceiling_after_idle_progress():
    deadline = _response_timeout_deadline(
        response_started_at=10.0,
        last_progress_at=18.0,
        response_idle_timeout_seconds=5.0,
        response_hard_timeout_seconds=10.0,
    )
    assert deadline == (
        20.0,
        "RESPONSE_HARD_TIMEOUT",
        "response_hard",
        10.0,
    )


def test_provider_response_progress_excludes_usage_only_and_accepts_semantics():
    assert not _provider_chunk_has_response_progress(
        {"choices": [], "usage": {"output_tokens": 1}}
    )
    assert not _provider_chunk_has_response_progress(
        {"choices": [{"delta": {"content": ""}, "finish_reason": None}]}
    )
    assert _provider_chunk_has_response_progress(
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]}
    )
    assert _provider_chunk_has_response_progress(
        {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    )
    assert _provider_chunk_has_response_progress(
        {"choices": [{"delta": {"tool_calls": [{"id": "call-1"}]}}]}
    )


def test_agent_response_progress_requires_surfaced_semantic_activity():
    assert not _agent_stream_event_has_response_progress(
        AgentStreamEvent(
            event_id="event-empty",
            event_type="agent.response",
            timestamp=1.0,
            execution_id="exec-1",
            channel="response",
            data={"content": "", "tool_calls": [], "final": False},
        )
    )
    assert _agent_stream_event_has_response_progress(
        AgentStreamEvent(
            event_id="event-progress",
            event_type="agent.response",
            timestamp=1.0,
            execution_id="exec-1",
            channel="response",
            data={"content": "working", "tool_calls": [], "final": False},
        )
    )
    assert _agent_stream_event_has_response_progress(
        AgentStreamEvent(
            event_id="event-tool",
            event_type="agent.tool.started",
            timestamp=1.0,
            execution_id="exec-1",
            channel="tool",
            data={"purpose": "Inspect state", "status": "started"},
        )
    )


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
async def test_sse_idle_timeout_ignores_heartbeat_and_closes_bridge(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    class SilentBus:
        def __init__(self):
            self.handlers = defaultdict(list)

        def subscribe(self, event_name, handler):
            self.handlers[event_name].append(handler)

        def unsubscribe(self, event_name, handler):
            if handler in self.handlers[event_name]:
                self.handlers[event_name].remove(handler)

        def publish(self, event):
            future = asyncio.get_running_loop().create_future()
            future.set_result(True)
            return future

    clock = [0.0]

    def monotonic():
        return clock[0]

    async def wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        clock[0] += timeout
        raise asyncio.TimeoutError()

    monkeypatch.setattr(chat_router.time, "monotonic", monotonic)
    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)

    bus = SilentBus()
    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=SimpleNamespace(
            provider=SimpleNamespace(timeout=60),
            gateway=SimpleNamespace(
                response_idle_timeout_seconds=0.25,
                response_hard_timeout_seconds=10.0,
            ),
        ),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )

    parts = [part async for part in response.body_iterator]
    assert parts[0] == ": ping\n\n"
    timeout_payload = json.loads(parts[1][len("data: "):])
    assert timeout_payload == {
        "error": "Gateway response idle timeout expired.",
        "error_code": "RESPONSE_IDLE_TIMEOUT",
        "timeout_scope": "response_idle",
        "timeout_seconds": 0.25,
    }
    assert parts[-1] == "data: [DONE]\n\n"
    assert not bus.handlers["provider.stream.chunk_emitted"]
    assert not bus.handlers["provider.stream.completed"]
    assert not bus.handlers["provider.failed"]



class _SseArrivalBus:
    def __init__(self):
        self.handlers = defaultdict(list)
        self.request_event = None

    def subscribe(self, event_name, handler):
        self.handlers[event_name].append(handler)

    def unsubscribe(self, event_name, handler):
        if handler in self.handlers[event_name]:
            self.handlers[event_name].remove(handler)

    def publish(self, event):
        self.request_event = event
        future = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future

    async def emit(self, event_name, payload):
        assert self.request_event is not None
        event = BaseEvent(
            event_name=event_name,
            session_id=self.request_event.session_id,
            turn_id=self.request_event.turn_id,
            payload=payload,
        )
        for handler in list(self.handlers[event_name]):
            await handler(event)


def _sse_timeout_config(*, idle=1.0, hard=10.0):
    return SimpleNamespace(
        provider=SimpleNamespace(timeout=60),
        gateway=SimpleNamespace(
            response_idle_timeout_seconds=idle,
            response_hard_timeout_seconds=hard,
        ),
    )


@pytest.mark.asyncio
async def test_sse_provider_failure_arriving_before_deadline_keeps_precedence(
    monkeypatch,
):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    clock = [0.0]
    bus = _SseArrivalBus()

    def monotonic():
        return clock[0]

    async def wait_for(awaitable, timeout):
        assert timeout == pytest.approx(1.0)
        clock[0] = 0.5
        await bus.emit(
            "provider.failed",
            {
                "error": "provider deadline",
                "error_code": "PROVIDER_CALL_TIMEOUT",
                "failure_domain": "PROVIDER",
                "retryable": False,
                "timeout_scope": "provider_call",
                "timeout_seconds": 0.5,
            },
        )
        clock[0] = 2.0
        return await awaitable

    monkeypatch.setattr(chat_router.time, "monotonic", monotonic)
    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)

    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=_sse_timeout_config(),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )

    parts = [part async for part in response.body_iterator]
    failure = json.loads(parts[1][len("data: "):])
    assert failure["error_code"] == "PROVIDER_CALL_TIMEOUT"
    assert failure["timeout_scope"] == "provider_call"
    assert parts[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_sse_completion_arriving_before_deadline_keeps_precedence(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    clock = [0.0]
    bus = _SseArrivalBus()

    def monotonic():
        return clock[0]

    async def wait_for(awaitable, timeout):
        assert timeout == pytest.approx(1.0)
        clock[0] = 0.75
        await bus.emit("provider.stream.completed", {})
        clock[0] = 2.0
        return await awaitable

    monkeypatch.setattr(chat_router.time, "monotonic", monotonic)
    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)

    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=_sse_timeout_config(),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )

    parts = [part async for part in response.body_iterator]
    assert parts == [": ping\n\n", "data: [DONE]\n\n"]


@pytest.mark.asyncio
async def test_sse_progress_resets_idle_from_arrival_not_processing_time(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    clock = [0.0]
    calls = [0]
    bus = _SseArrivalBus()

    def monotonic():
        return clock[0]

    async def wait_for(awaitable, timeout):
        calls[0] += 1
        if calls[0] != 1:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise AssertionError("arrival-time idle anchor should expire before another wait")
        assert timeout == pytest.approx(1.0)
        clock[0] = 0.5
        await bus.emit(
            "provider.stream.chunk_emitted",
            {
                "chunk": {
                    "id": "chunk-progress",
                    "model": "mock",
                    "choices": [
                        {"index": 0, "delta": {"content": "hello"}}
                    ],
                }
            },
        )
        clock[0] = 1.5
        return await awaitable

    monkeypatch.setattr(chat_router.time, "monotonic", monotonic)
    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)

    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=_sse_timeout_config(),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )

    parts = [part async for part in response.body_iterator]
    progress = json.loads(parts[1][len("data: "):])
    timeout_payload = json.loads(parts[2][len("data: "):])
    assert progress["choices"][0]["delta"]["content"] == "hello"
    assert timeout_payload["error_code"] == "RESPONSE_IDLE_TIMEOUT"
    assert calls == [1]
    assert parts[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_sse_item_arriving_after_deadline_does_not_beat_timeout(monkeypatch):
    import se.src.transport.gateway.api.v1.chat_router as chat_router

    clock = [0.0]
    bus = _SseArrivalBus()

    def monotonic():
        return clock[0]

    async def wait_for(awaitable, timeout):
        assert timeout == pytest.approx(1.0)
        clock[0] = 1.1
        await bus.emit("provider.stream.completed", {})
        return await awaitable

    monkeypatch.setattr(chat_router.time, "monotonic", monotonic)
    monkeypatch.setattr(chat_router.asyncio, "wait_for", wait_for)

    response = await chat_completions_proxy(
        _Request({
            "model": "mock",
            "messages": [{"role": "user", "content": "hello"}],
            "config": {"stream": True},
        }),
        identity=Identity(auth_type="guest", user_id="user-1"),
        event_bus=bus,
        config=_sse_timeout_config(),
        container=SimpleNamespace(connection_runtime=SimpleNamespace(registry=None)),
    )

    parts = [part async for part in response.body_iterator]
    timeout_payload = json.loads(parts[1][len("data: "):])
    assert timeout_payload["error_code"] == "RESPONSE_IDLE_TIMEOUT"
    assert timeout_payload["timeout_scope"] == "response_idle"
    assert parts[-1] == "data: [DONE]\n\n"


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
