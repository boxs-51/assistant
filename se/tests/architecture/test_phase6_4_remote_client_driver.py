from __future__ import annotations

import asyncio

import pytest

from src.runtimes.capability.contracts.context import CapabilityExecutionContext
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.remote_client_driver import RemoteClientDriver
from src.runtimes.connection.protocol import RealtimeEnvelope
from src.runtimes.connection.registry import ConnectionRegistry
from src.runtimes.connection.realtime import RealtimeMultiplexer


class FakeSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def make_driver() -> tuple[RemoteClientDriver, RealtimeMultiplexer, FakeSocket]:
    registry = ConnectionRegistry()
    socket = FakeSocket()
    registry.register("sess-1", "user-1", socket, connection_id="conn-1")
    registry.activate("conn-1")
    realtime = RealtimeMultiplexer(registry)
    definition = CapabilityDefinition(
        id="filesystem.read",
        name="filesystem.read",
        description="Read a local file.",
        input_schema={"type": "object"},
    )
    driver = RemoteClientDriver(definition, realtime, "conn-1")
    return driver, realtime, socket


def make_context(**kwargs) -> CapabilityExecutionContext:
    return CapabilityExecutionContext.create(
        identity=None,
        execution_id="exec-1",
        invocation_id="inv-1",
        session_id="sess-1",
        connection_id="conn-1",
        metadata={"trace_id": "trace-1"},
        **kwargs,
    )


def test_remote_driver_preserves_correlation_and_arguments() -> None:
    async def scenario() -> None:
        driver, realtime, socket = make_driver()
        context = make_context()
        task = asyncio.create_task(driver.execute(context, {"path": "a.txt"}))
        await asyncio.sleep(0)

        sent = socket.messages[0]
        assert sent["type"] == "capability.invoke"
        assert sent["session_id"] == "sess-1"
        assert sent["connection_id"] == "conn-1"
        assert sent["execution_id"] == "exec-1"
        assert sent["invocation_id"] == "inv-1"
        assert sent["trace_id"] == "trace-1"
        assert sent["payload"] == {
            "capability_id": "filesystem.read",
            "arguments": {"path": "a.txt"},
        }

        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="result-1",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={"content": "ok"},
            ),
        )
        assert await task == {"content": "ok"}

    asyncio.run(scenario())


def test_remote_driver_timeout_sends_cancel() -> None:
    async def scenario() -> None:
        driver, _, socket = make_driver()
        with pytest.raises(asyncio.TimeoutError):
            await driver.execute(make_context(timeout_seconds=0.01), {})
        assert socket.messages[-1]["type"] == "capability.cancel"

    asyncio.run(scenario())


def test_remote_driver_expired_context_does_not_send_invoke() -> None:
    async def scenario() -> None:
        driver, _, socket = make_driver()
        with pytest.raises(asyncio.TimeoutError):
            await driver.execute(make_context(timeout_seconds=0), {})
        assert socket.messages == []

    asyncio.run(scenario())


def test_remote_driver_pre_cancelled_context_does_not_send_invoke() -> None:
    async def scenario() -> None:
        driver, _, socket = make_driver()
        context = make_context()
        context.cancel()
        with pytest.raises(asyncio.CancelledError):
            await driver.execute(context, {})
        assert socket.messages == []

    asyncio.run(scenario())


def test_remote_driver_cancellation_sends_cancel() -> None:
    async def scenario() -> None:
        driver, _, socket = make_driver()
        task = asyncio.create_task(driver.execute(make_context(), {}))
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert socket.messages[-1]["type"] == "capability.cancel"

    asyncio.run(scenario())