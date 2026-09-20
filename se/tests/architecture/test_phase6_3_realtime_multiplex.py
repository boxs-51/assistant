from __future__ import annotations

import asyncio

import pytest

from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.runtimes.connection.realtime import (
    RealtimeMultiplexer,
    RemoteCapabilityError,
)


class FakeSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def make_active_connection() -> tuple[ConnectionRegistry, FakeSocket]:
    registry = ConnectionRegistry()
    socket = FakeSocket()
    registry.register(
        "sess-1",
        "user-1",
        socket,
        connection_id="conn-1",
    )
    registry.activate("conn-1")
    return registry, socket


def test_invoke_correlates_result_and_is_idempotent() -> None:
    async def scenario() -> None:
        registry, socket = make_active_connection()
        realtime = RealtimeMultiplexer(registry)
        invocation = RealtimeEnvelope(
            type="capability.invoke",
            message_id="msg-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            payload={"x": 1},
        )

        task = asyncio.create_task(realtime.invoke(invocation))
        await asyncio.sleep(0)

        assert socket.messages[0]["type"] == "capability.invoke"

        result = await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="msg-2",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={"output": {"ok": True}},
            ),
        )
        duplicate = await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="msg-3",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={"output": {"ok": False}},
            ),
        )

        assert result is True
        assert duplicate is False
        assert await task == {"ok": True}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "output",
    [
        {"value": "ok"},
        "plain text",
        42,
        [1, "two", None],
        None,
    ],
)
def test_result_output_round_trip_preserves_exact_json_value(output) -> None:
    async def scenario() -> None:
        registry, _ = make_active_connection()
        realtime = RealtimeMultiplexer(registry)
        task = asyncio.create_task(realtime.invoke(RealtimeEnvelope(
            type="capability.invoke",
            message_id="invoke-exact",
            connection_id="conn-1",
            invocation_id="inv-exact",
            payload={},
        )))
        await asyncio.sleep(0)
        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="result-exact",
                connection_id="conn-1",
                invocation_id="inv-exact",
                payload={"output": output},
            ),
        )
        assert await task == output

    asyncio.run(scenario())


def test_timeout_sends_cancel() -> None:
    async def scenario() -> None:
        registry, socket = make_active_connection()
        realtime = RealtimeMultiplexer(registry, default_timeout=0.01)
        invocation = RealtimeEnvelope(
            type="capability.invoke",
            message_id="msg-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            payload={},
        )

        with pytest.raises(asyncio.TimeoutError):
            await realtime.invoke(invocation)

        assert socket.messages[-1]["type"] == "capability.cancel"

    asyncio.run(scenario())


def test_reconciliation_query_is_correlated_without_execution_permission() -> None:
    async def scenario() -> None:
        registry, socket = make_active_connection()
        realtime = RealtimeMultiplexer(registry)
        task = asyncio.create_task(
            realtime.reconcile(
                RealtimeEnvelope(
                    type="capability.reconcile",
                    message_id="reconcile-1",
                    connection_id="conn-1",
                    invocation_id="inv-1",
                    payload={
                        "capability_id": "tool.echo",
                        "capability_version": "1.0",
                        "request_fingerprint": "abc",
                    },
                )
            )
        )
        await asyncio.sleep(0)
        assert socket.messages == [
            {
                **socket.messages[0],
                "type": "capability.reconcile",
            }
        ]
        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.reconciliation",
                message_id="reconciliation-1",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={
                    "status": "NOT_FOUND",
                    "capability_id": "tool.echo",
                    "capability_version": "1.0",
                    "request_fingerprint": "abc",
                },
            ),
        )
        assert await task == {
            "status": "NOT_FOUND",
            "capability_id": "tool.echo",
            "capability_version": "1.0",
            "request_fingerprint": "abc",
        }

    asyncio.run(scenario())


def test_disconnect_fails_only_pending_invocations_for_connection() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        registry.register("sess-a", "user-1", socket_a, connection_id="conn-a")
        registry.register("sess-b", "user-1", socket_b, connection_id="conn-b")
        registry.activate("conn-a")
        registry.activate("conn-b")
        realtime = RealtimeMultiplexer(registry)

        task_a = asyncio.create_task(realtime.invoke(
            RealtimeEnvelope(
                type="capability.invoke",
                message_id="msg-a",
                connection_id="conn-a",
                invocation_id="inv-a",
                payload={},
            )
        ))
        task_b = asyncio.create_task(realtime.invoke(
            RealtimeEnvelope(
                type="capability.invoke",
                message_id="msg-b",
                connection_id="conn-b",
                invocation_id="inv-b",
                payload={},
            )
        ))
        await asyncio.sleep(0)

        assert await realtime.disconnect("conn-a") == 1
        assert await realtime.handle_inbound(
            "conn-b",
            RealtimeEnvelope(
                type="capability.result",
                message_id="msg-b-result",
                connection_id="conn-b",
                invocation_id="inv-b",
                payload={"output": {"ok": True}},
            ),
        )

        with pytest.raises(ConnectionError):
            await task_a
        assert await task_b == {"ok": True}

    asyncio.run(scenario())


def test_progress_is_forwarded_without_completing_invocation() -> None:
   async def scenario() -> None:
       registry, _ = make_active_connection()
       progress_messages = []

       async def on_progress(envelope):
           progress_messages.append(envelope.payload)

       realtime = RealtimeMultiplexer(
           registry,
           progress_handler=on_progress,
       )
       invocation = RealtimeEnvelope(
           type="capability.invoke",
           message_id="msg-1",
           connection_id="conn-1",
           invocation_id="inv-1",
           payload={},
       )
       task = asyncio.create_task(realtime.invoke(invocation))
       await asyncio.sleep(0)

       assert await realtime.handle_inbound(
           "conn-1",
           RealtimeEnvelope(
               type="capability.progress",
               message_id="msg-progress",
               connection_id="conn-1",
               invocation_id="inv-1",
               payload={"percent": 50},
           ),
       )
       assert progress_messages == [{"percent": 50}]
       assert await realtime.multiplexer.pending_count("conn-1") == 1

       await realtime.handle_inbound(
           "conn-1",
           RealtimeEnvelope(
               type="capability.result",
               message_id="msg-result",
               connection_id="conn-1",
               invocation_id="inv-1",
               payload={"output": {"ok": True}},
           ),
       )
       assert await task == {"ok": True}

   asyncio.run(scenario())


def test_remote_error_is_correlated_to_invocation() -> None:
    async def scenario() -> None:
        registry, _ = make_active_connection()
        realtime = RealtimeMultiplexer(registry)
        invocation = RealtimeEnvelope(
            type="capability.invoke",
            message_id="msg-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            payload={},
        )
        task = asyncio.create_task(realtime.invoke(invocation))
        await asyncio.sleep(0)

        await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.error",
                message_id="msg-2",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={"message": "permission denied"},
            ),
        )

        with pytest.raises(RemoteCapabilityError, match="permission denied"):
            await task

    asyncio.run(scenario())


def test_inbound_connection_identity_is_enforced() -> None:
    async def scenario() -> None:
        registry, _ = make_active_connection()
        realtime = RealtimeMultiplexer(registry)

        with pytest.raises(ValueError, match="connection_id"):
            await realtime.handle_inbound(
                "conn-1",
                RealtimeEnvelope(
                    type="capability.result",
                    message_id="msg-1",
                    connection_id="conn-2",
                    invocation_id="inv-1",
                    payload={},
                ),
            )

    asyncio.run(scenario())


def test_cross_connection_result_injection_is_rejected() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        registry.register("sess-a", "user-1", socket_a, connection_id="conn-a")
        registry.register("sess-b", "user-1", socket_b, connection_id="conn-b")
        registry.activate("conn-a")
        registry.activate("conn-b")
        realtime = RealtimeMultiplexer(registry, default_timeout=0.1)

        task = asyncio.create_task(realtime.invoke(
            RealtimeEnvelope(
                type="capability.invoke",
                message_id="msg-a",
                connection_id="conn-a",
                invocation_id="inv-a",
                payload={},
            )
        ))
        await asyncio.sleep(0)

        accepted = await realtime.handle_inbound(
            "conn-b",
            RealtimeEnvelope(
                type="capability.result",
                message_id="msg-injected",
                connection_id="conn-b",
                invocation_id="inv-a",
                payload={"output": {"injected": True}},
            ),
        )
        assert accepted is False
        assert not task.done()

        assert await realtime.handle_inbound(
            "conn-a",
            RealtimeEnvelope(
                type="capability.result",
                message_id="msg-a-result",
                connection_id="conn-a",
                invocation_id="inv-a",
                payload={"output": {"ok": True}},
            ),
        )
        assert await task == {"ok": True}

    asyncio.run(scenario())


def test_correlated_message_requires_invocation_id() -> None:
    with pytest.raises(ValueError, match="invocation_id"):
        RealtimeEnvelope(
            type="capability.result",
            message_id="msg-1",
            connection_id="conn-1",
        )

    with pytest.raises(ValueError, match="connection_id"):
        RealtimeEnvelope(
            type="capability.invoke",
            message_id="msg-2",
            invocation_id="inv-2",
        )
