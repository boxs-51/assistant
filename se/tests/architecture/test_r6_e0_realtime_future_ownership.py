from __future__ import annotations

import asyncio
import gc

import pytest

from se.src.runtimes.connection.multiplexer import RemoteConnectionLost
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.realtime import RealtimeMultiplexer
from se.src.runtimes.connection.registry import ConnectionRegistry


class _RecordingSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class _FailingSocket:
    async def send_json(self, payload):
        raise ConnectionError("send failed")


class _DisconnectThenFailSocket:
    def __init__(self) -> None:
        self.realtime = None

    async def send_json(self, payload):
        assert self.realtime is not None
        await self.realtime.disconnect("conn-1")
        raise ConnectionError("send failed after disconnect")


def _realtime(socket) -> RealtimeMultiplexer:
    registry = ConnectionRegistry()
    registry.register(
        "session-1",
        "user-1",
        socket,
        connection_id="conn-1",
    )
    registry.activate("conn-1")
    return RealtimeMultiplexer(
        registry,
        default_timeout=1.0,
    )


def _invoke_envelope(invocation_id="inv-1"):
    return RealtimeEnvelope(
        type="capability.invoke",
        message_id=f"invoke-{invocation_id}",
        connection_id="conn-1",
        invocation_id=invocation_id,
        payload={},
    )


def _reconcile_envelope(invocation_id="inv-reconcile"):
    return RealtimeEnvelope(
        type="capability.reconcile",
        message_id=f"reconcile-{invocation_id}",
        connection_id="conn-1",
        invocation_id=invocation_id,
        payload={
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "request_fingerprint": "f" * 64,
        },
    )


async def _assert_no_unretrieved_future(
    operation,
):
    loop = asyncio.get_running_loop()
    contexts = []
    previous = loop.get_exception_handler()

    def capture(_loop, context):
        contexts.append(dict(context))

    loop.set_exception_handler(capture)
    try:
        with pytest.raises(RemoteConnectionLost):
            await operation()

        # Give Future finalizers and loop exception callbacks a deterministic
        # opportunity to run.  The pre-E0 implementation reports:
        # "Future exception was never retrieved".
        gc.collect()
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)

        messages = [
            str(context.get("message", ""))
            for context in contexts
        ]
        assert "Future exception was never retrieved" not in messages
    finally:
        loop.set_exception_handler(previous)


def test_invoke_send_failure_has_no_unretrieved_future():
    async def scenario():
        realtime = _realtime(_FailingSocket())
        await _assert_no_unretrieved_future(
            lambda: realtime.invoke(
                _invoke_envelope("inv-send-failure")
            )
        )
        assert await realtime.multiplexer.pending_count() == 0

    asyncio.run(scenario())


def test_reconcile_send_failure_has_no_unretrieved_future():
    async def scenario():
        realtime = _realtime(_FailingSocket())
        await _assert_no_unretrieved_future(
            lambda: realtime.reconcile(
                _reconcile_envelope("inv-reconcile-failure")
            )
        )
        assert await realtime.multiplexer.pending_count() == 0

    asyncio.run(scenario())


def test_send_failure_racing_disconnect_drains_rejected_future():
    async def scenario():
        socket = _DisconnectThenFailSocket()
        realtime = _realtime(socket)
        socket.realtime = realtime

        await _assert_no_unretrieved_future(
            lambda: realtime.invoke(
                _invoke_envelope("inv-disconnect-race")
            )
        )
        assert await realtime.multiplexer.pending_count() == 0

    asyncio.run(scenario())


def test_invoke_caller_cancellation_releases_pending_and_sends_cancel():
    async def scenario():
        socket = _RecordingSocket()
        realtime = _realtime(socket)

        task = asyncio.create_task(
            realtime.invoke(
                _invoke_envelope("inv-caller-cancel"),
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)
        assert socket.messages[0]["type"] == "capability.invoke"
        assert await realtime.multiplexer.pending_count() == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await realtime.multiplexer.pending_count() == 0
        assert [
            item["type"] for item in socket.messages
        ] == [
            "capability.invoke",
            "capability.cancel",
        ]

    asyncio.run(scenario())


def test_reconcile_caller_cancellation_releases_pending_without_remote_cancel():
    async def scenario():
        socket = _RecordingSocket()
        realtime = _realtime(socket)

        task = asyncio.create_task(
            realtime.reconcile(
                _reconcile_envelope("inv-reconcile-cancel"),
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)
        assert socket.messages[0]["type"] == "capability.reconcile"
        assert await realtime.multiplexer.pending_count() == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await realtime.multiplexer.pending_count() == 0
        assert [
            item["type"] for item in socket.messages
        ] == ["capability.reconcile"]

    asyncio.run(scenario())
