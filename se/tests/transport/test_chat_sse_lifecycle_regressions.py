import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.transport.gateway.api.v1.chat_router import chat_completions_proxy


class _Request:
    def __init__(self, body, *, disconnected=False):
        self._body = body
        self._disconnected = disconnected

    async def json(self):
        return self._body

    async def is_disconnected(self):
        return self._disconnected


class _SseBus:
    def __init__(self):
        self.handlers = defaultdict(list)
        self.tasks = []
        self.futures = []

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
