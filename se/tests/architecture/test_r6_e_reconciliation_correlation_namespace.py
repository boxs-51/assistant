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


def _reconcile_envelope(invocation_id: str) -> RealtimeEnvelope:
    return RealtimeEnvelope(
        type="capability.reconcile",
        message_id=f"reconcile-{invocation_id}",
        connection_id="conn-1",
        invocation_id=invocation_id,
        payload={
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "request_fingerprint": "abc",
        },
    )


def _invoke_envelope(invocation_id: str) -> RealtimeEnvelope:
    return RealtimeEnvelope(
        type="capability.invoke",
        message_id=f"invoke-{invocation_id}",
        connection_id="conn-1",
        invocation_id=invocation_id,
        payload={},
    )


def test_reconcile_send_failure_releases_reconciliation_namespace() -> None:
    async def scenario() -> None:
        realtime = _realtime(_FailingSocket())
        loop = asyncio.get_running_loop()
        contexts = []
        previous = loop.get_exception_handler()

        def capture(_loop, context):
            contexts.append(dict(context))

        loop.set_exception_handler(capture)
        try:
            with pytest.raises(RemoteConnectionLost):
                await realtime.reconcile(
                    _reconcile_envelope("inv-send-failure")
                )

            assert await realtime.multiplexer.pending_count() == 0
            assert (
                await realtime.reconciliation_multiplexer.pending_count()
                == 0
            )

            gc.collect()
            await asyncio.sleep(0)
            gc.collect()
            await asyncio.sleep(0)
            assert "Future exception was never retrieved" not in {
                str(item.get("message", ""))
                for item in contexts
            }
        finally:
            loop.set_exception_handler(previous)

    asyncio.run(scenario())


def test_reconcile_caller_cancel_releases_only_reconciliation_namespace() -> None:
    async def scenario() -> None:
        socket = _RecordingSocket()
        realtime = _realtime(socket)

        task = asyncio.create_task(
            realtime.reconcile(
                _reconcile_envelope("inv-caller-cancel"),
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)

        assert socket.messages[0]["type"] == "capability.reconcile"
        assert await realtime.multiplexer.pending_count() == 0
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 1
        )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await realtime.multiplexer.pending_count() == 0
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 0
        )
        assert [
            item["type"] for item in socket.messages
        ] == ["capability.reconcile"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("terminal_type", "payload"),
    [
        ("capability.cancelled", {}),
        ("capability.result", {"output": {"late": True}}),
        (
            "capability.error",
            {
                "code": "LATE_ERROR",
                "message": "late execution terminal",
            },
        ),
    ],
)
def test_late_execution_terminal_cannot_complete_reconciliation_waiter(
    terminal_type,
    payload,
) -> None:
    async def scenario() -> None:
        socket = _RecordingSocket()
        realtime = _realtime(socket)
        invocation_id = "inv-shared"

        task = asyncio.create_task(
            realtime.reconcile(
                _reconcile_envelope(invocation_id),
                timeout=5.0,
            )
        )
        await asyncio.sleep(0)

        assert await realtime.multiplexer.pending_count() == 0
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 1
        )

        accepted_late_terminal = await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type=terminal_type,
                message_id=f"late-{terminal_type}",
                connection_id="conn-1",
                invocation_id=invocation_id,
                payload=payload,
            ),
        )

        assert accepted_late_terminal is False
        assert task.done() is False
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 1
        )

        reconciliation_payload = {
            "status": "TERMINAL",
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "request_fingerprint": "abc",
            "terminal_type": "cancelled",
            "terminal_payload": {},
        }
        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.reconciliation",
                message_id="reconciliation-final",
                connection_id="conn-1",
                invocation_id=invocation_id,
                payload=reconciliation_payload,
            ),
        )
        assert await task == reconciliation_payload
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 0
        )

    asyncio.run(scenario())


def test_disconnect_fails_execution_and_reconciliation_waiters() -> None:
    async def scenario() -> None:
        socket = _RecordingSocket()
        realtime = _realtime(socket)
        invocation_id = "inv-both-domains"

        execution = asyncio.create_task(
            realtime.invoke(
                _invoke_envelope(invocation_id),
                timeout=30.0,
            )
        )
        reconciliation = asyncio.create_task(
            realtime.reconcile(
                _reconcile_envelope(invocation_id),
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)

        assert await realtime.multiplexer.pending_count() == 1
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 1
        )
        assert await realtime.disconnect("conn-1") == 2

        with pytest.raises(RemoteConnectionLost):
            await execution
        with pytest.raises(RemoteConnectionLost):
            await reconciliation

        assert await realtime.multiplexer.pending_count() == 0
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 0
        )

    asyncio.run(scenario())
