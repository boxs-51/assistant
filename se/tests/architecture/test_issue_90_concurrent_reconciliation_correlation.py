from __future__ import annotations

import asyncio

import pytest

from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.realtime import RealtimeMultiplexer
from se.src.runtimes.connection.registry import ConnectionRegistry


class _RecordingSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def _envelope(connection_id: str, message_id: str) -> RealtimeEnvelope:
    return RealtimeEnvelope(
        type="capability.reconcile",
        message_id=message_id,
        connection_id=connection_id,
        invocation_id="inv-shared-r7j",
        payload={
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "request_fingerprint": "f" * 64,
        },
    )


def test_same_invocation_reconciles_concurrently_across_connection_generations() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        socket_a = _RecordingSocket()
        socket_b = _RecordingSocket()
        for connection_id, socket in (
            ("conn-generation-a", socket_a),
            ("conn-generation-b", socket_b),
        ):
            registry.register(
                "session-1",
                "user-1",
                socket,
                connection_id=connection_id,
            )
            registry.activate(connection_id)

        realtime = RealtimeMultiplexer(registry, default_timeout=2.0)

        task_a = asyncio.create_task(
            realtime.reconcile(
                _envelope("conn-generation-a", "reconcile-a"),
            )
        )
        task_b = asyncio.create_task(
            realtime.reconcile(
                _envelope("conn-generation-b", "reconcile-b"),
            )
        )
        await asyncio.sleep(0)

        assert len(socket_a.messages) == 1
        assert len(socket_b.messages) == 1
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 2
        )
        assert (
            await realtime.reconciliation_multiplexer.pending_count(
                "conn-generation-a"
            )
            == 1
        )
        assert (
            await realtime.reconciliation_multiplexer.pending_count(
                "conn-generation-b"
            )
            == 1
        )

        payload_a = {"status": "NOT_FOUND", "source": "generation-a"}
        payload_b = {"status": "NOT_FOUND", "source": "generation-b"}
        assert await realtime.handle_inbound(
            "conn-generation-a",
            RealtimeEnvelope(
                type="capability.reconciliation",
                message_id="response-a",
                connection_id="conn-generation-a",
                invocation_id="inv-shared-r7j",
                payload=payload_a,
            ),
        )
        assert await realtime.handle_inbound(
            "conn-generation-b",
            RealtimeEnvelope(
                type="capability.reconciliation",
                message_id="response-b",
                connection_id="conn-generation-b",
                invocation_id="inv-shared-r7j",
                payload=payload_b,
            ),
        )

        result_a, result_b = await asyncio.gather(task_a, task_b)
        assert result_a == payload_a
        assert result_b == payload_b
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 0
        )

    asyncio.run(scenario())


def test_same_connection_duplicate_reconciliation_still_fails_closed() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        socket = _RecordingSocket()
        registry.register(
            "session-1",
            "user-1",
            socket,
            connection_id="conn-generation-a",
        )
        registry.activate("conn-generation-a")
        realtime = RealtimeMultiplexer(registry, default_timeout=2.0)

        first = asyncio.create_task(
            realtime.reconcile(
                _envelope("conn-generation-a", "reconcile-first"),
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)

        with pytest.raises(ValueError, match="already registered"):
            await realtime.reconcile(
                _envelope("conn-generation-a", "reconcile-second"),
                timeout=30.0,
            )

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (
            await realtime.reconciliation_multiplexer.pending_count()
            == 0
        )

    asyncio.run(scenario())
