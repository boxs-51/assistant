from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.transport.gateway.api.v1.chat_router import chat_completions_proxy


_FAILURE = {
    "error": "checkpoint serialization failed",
    "error_code": "AGENT_PERSISTENCE_SERIALIZATION_FAILED",
    "failure_domain": "AGENT_PERSISTENCE",
    "retryable": False,
    "execution_id": "exec-r2-1",
    "status_code": 500,
}


class _Request:
    def __init__(self, body) -> None:
        self._body = body

    async def json(self):
        return self._body

    async def is_disconnected(self):
        return False


class _FailureBus:
    def __init__(self, payload=None) -> None:
        self.payload = dict(payload or _FAILURE)
        self.handlers = defaultdict(list)
        self.tasks = []

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

        async def run():
            try:
                if event.event_name == "transport.event.request_received":
                    await self._emit(
                        BaseEvent(
                            event_name="provider.failed",
                            session_id=event.session_id,
                            turn_id=event.turn_id,
                            payload=self.payload,
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


def _deps():
    identity = Identity(auth_type="guest", user_id="user-1")
    config = SimpleNamespace(provider=SimpleNamespace(timeout=5))
    container = SimpleNamespace(
        connection_runtime=SimpleNamespace(registry=None)
    )
    return identity, config, container


@pytest.mark.asyncio
async def test_sse_failure_preserves_structured_agent_failure_metadata():
    bus = _FailureBus()
    identity, config, container = _deps()
    response = await chat_completions_proxy(
        _Request(
            {
                "model": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": True},
            }
        ),
        identity=identity,
        event_bus=bus,
        config=config,
        container=container,
    )

    parts = [part async for part in response.body_iterator]
    await asyncio.gather(*bus.tasks)
    data_frames = [
        part
        for part in parts
        if part.startswith("data: ") and part != "data: [DONE]\n\n"
    ]
    assert len(data_frames) == 1
    payload = json.loads(data_frames[0][len("data: "):])
    assert payload["error_code"] == _FAILURE["error_code"]
    assert payload["failure_domain"] == "AGENT_PERSISTENCE"
    assert payload["retryable"] is False
    assert payload["execution_id"] == "exec-r2-1"
    assert parts[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_non_stream_failure_preserves_structured_agent_failure_metadata():
    bus = _FailureBus()
    identity, config, container = _deps()

    with pytest.raises(HTTPException) as caught:
        await chat_completions_proxy(
            _Request(
                {
                    "model": "mock",
                    "messages": [{"role": "user", "content": "hello"}],
                    "config": {"stream": False},
                }
            ),
            identity=identity,
            event_bus=bus,
            config=config,
            container=container,
        )

    await asyncio.gather(*bus.tasks)
    assert caught.value.status_code == 500
    assert caught.value.detail["error"] == _FAILURE["error"]
    assert caught.value.detail["error_code"] == _FAILURE["error_code"]
    assert caught.value.detail["failure_domain"] == "AGENT_PERSISTENCE"
    assert caught.value.detail["retryable"] is False
    assert caught.value.detail["execution_id"] == "exec-r2-1"


@pytest.mark.asyncio
async def test_non_stream_legacy_provider_failure_keeps_string_detail():
    bus = _FailureBus({"error": "legacy provider failure", "status_code": 502})
    identity, config, container = _deps()

    with pytest.raises(HTTPException) as caught:
        await chat_completions_proxy(
            _Request(
                {
                    "model": "mock",
                    "messages": [{"role": "user", "content": "hello"}],
                    "config": {"stream": False},
                }
            ),
            identity=identity,
            event_bus=bus,
            config=config,
            container=container,
        )

    await asyncio.gather(*bus.tasks)
    assert caught.value.status_code == 502
    assert caught.value.detail == "legacy provider failure"