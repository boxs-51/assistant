from __future__ import annotations

import asyncio

import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
)
from se.src.runtimes.capability.contracts.error import (
    CapabilityError,
    REMOTE_OUTCOME_UNKNOWN,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
    InvalidInvocationTransition,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.multiplexer import RemoteConnectionLost
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.realtime import (
    RealtimeMultiplexer,
    RemoteCapabilityError,
)
from se.src.runtimes.connection.registry import (
    ConnectionRegistry,
    ConnectionStateError,
)


class _Availability:
    def is_active(self, connection_id):
        return True


class _ServerDriver(BaseCapabilityDriver):
    def __init__(self, definition, calls):
        super().__init__(definition)
        self.calls = calls

    async def execute(self, context, arguments):
        self.calls.append(context.invocation_id)
        return {
            "source": "server",
            "invocation_id": context.invocation_id,
        }


class _DisconnectingRealtime:
    async def invoke(self, envelope, timeout=None):
        raise RemoteConnectionLost(
            envelope.connection_id,
            envelope.invocation_id,
        )

    async def cancel(self, connection_id, invocation_id):
        return None


class _PreDispatchUnavailableRealtime:
    async def invoke(self, envelope, timeout=None):
        raise ConnectionStateError("connection became inactive before send")

    async def cancel(self, connection_id, invocation_id):
        return None


class _SuccessfulRealtime:
    async def invoke(self, envelope, timeout=None):
        return {
            "source": "client",
            "invocation_id": envelope.invocation_id,
        }

    async def cancel(self, connection_id, invocation_id):
        return None


class _RemoteErrorRealtime:
    async def invoke(self, envelope, timeout=None):
        raise RemoteCapabilityError(
            "client denied",
            code="CLIENT_DENIED",
            details={"reason": "policy"},
            retryable=False,
        )

    async def cancel(self, connection_id, invocation_id):
        return None


def _runtime(
    *,
    idempotency,
    realtime,
    include_server=True,
):
    definition = CapabilityDefinition(
        id="tool.remote",
        name="tool.remote",
        description="remote",
        idempotency=idempotency,
    )
    catalog = CapabilityCatalog()
    catalog.register_definition(definition)
    client = CapabilityImplementation.from_definition(
        definition,
        implementation_id="conn-1:tool.remote",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id="user-1",
        connection_id="conn-1",
        metadata={"client_id": "client-1"},
    )
    catalog.register_implementation(client)
    catalog.transition_implementation(
        client.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    server = None
    server_calls = []
    if include_server:
        server = CapabilityImplementation.from_definition(
            definition,
            implementation_id="server:tool.remote",
            location=CapabilityExecutionLocation.SERVER,
            driver_kind="PYTHON",
        )
        catalog.register_implementation(server)
        catalog.transition_implementation(
            server.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=_Availability(),
        ),
        realtime=realtime,
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
    )
    if server is not None:
        runtime.driver_registry.bind(
            server.implementation_id,
            _ServerDriver(definition, server_calls),
        )
    return runtime, store, server_calls


def _identity():
    return Identity(
        user_id="user-1",
        auth_type="jwt",
        session_id="session-1",
    )


def test_r6_b_remote_outcome_transition_is_cas_guarded():
    async def scenario():
        store = InMemoryCapabilityInvocationStore()
        lifecycle = CapabilityInvocationLifecycle(store)
        invocation = CapabilityInvocation(
            invocation_id="inv-state",
            capability_id="tool.remote",
            capability_version="1.0",
            kind="TOOL",
            execution_mode="ONE_SHOT",
            remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        )
        await lifecycle.create(invocation)
        await lifecycle.update_remote_outcome(
            invocation,
            RemoteOutcomeState.IN_FLIGHT,
        )
        assert invocation.revision == 1
        await lifecycle.update_remote_outcome(
            invocation,
            RemoteOutcomeState.OUTCOME_UNKNOWN,
        )
        assert invocation.revision == 2
        with pytest.raises(InvalidInvocationTransition):
            await lifecycle.update_remote_outcome(
                invocation,
                RemoteOutcomeState.NOT_DISPATCHED,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "idempotency",
    [
        CapabilityIdempotency.NON_IDEMPOTENT,
        CapabilityIdempotency.UNKNOWN,
    ],
)
def test_r6_b_unknown_outcome_blocks_unsafe_fallback(idempotency):
    async def scenario():
        runtime, store, server_calls = _runtime(
            idempotency=idempotency,
            realtime=_DisconnectingRealtime(),
        )
        with pytest.raises(CapabilityError) as raised:
            await runtime.execute_capability(
                "tool.remote",
                {"value": "x"},
                _identity(),
                invocation_id=f"inv-{idempotency.value}",
                connection_id="conn-1",
                metadata={"max_attempts": 2},
            )

        assert raised.value.code == REMOTE_OUTCOME_UNKNOWN
        assert server_calls == []
        persisted = store.items[f"inv-{idempotency.value}"]
        assert persisted.state is CapabilityInvocationState.WAITING
        assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.OUTCOME_UNKNOWN
        )
        assert len(store.attempts) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "idempotency",
    [
        CapabilityIdempotency.IDEMPOTENT,
        CapabilityIdempotency.DEDUPLICATED,
    ],
)
def test_r6_b_safe_idempotency_allows_same_invocation_fallback(idempotency):
    async def scenario():
        runtime, store, server_calls = _runtime(
            idempotency=idempotency,
            realtime=_DisconnectingRealtime(),
        )
        invocation_id = f"inv-safe-{idempotency.value}"
        result = await runtime.execute_capability(
            "tool.remote",
            {"value": "x"},
            _identity(),
            invocation_id=invocation_id,
            connection_id="conn-1",
            metadata={"max_attempts": 2},
        )

        assert result.output["source"] == "server"
        assert result.invocation_id == invocation_id
        assert server_calls == [invocation_id]
        persisted = store.items[invocation_id]
        assert persisted.state is CapabilityInvocationState.COMPLETED
        # The original client effect remains uncertain even though policy
        # permits a safe replay on another implementation.
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.OUTCOME_UNKNOWN
        )
        attempts = sorted(
            store.attempts.values(),
            key=lambda item: item.attempt_number,
        )
        assert len(attempts) == 2

    asyncio.run(scenario())


def test_r6_b_proven_not_dispatched_can_fallback_even_when_unknown():
    async def scenario():
        runtime, store, server_calls = _runtime(
            idempotency=CapabilityIdempotency.UNKNOWN,
            realtime=_PreDispatchUnavailableRealtime(),
        )
        result = await runtime.execute_capability(
            "tool.remote",
            {},
            _identity(),
            invocation_id="inv-not-dispatched",
            connection_id="conn-1",
            metadata={"max_attempts": 2},
        )
        assert result.output["source"] == "server"
        assert server_calls == ["inv-not-dispatched"]
        persisted = store.items["inv-not-dispatched"]
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.NOT_DISPATCHED
        )

    asyncio.run(scenario())


def test_r6_b_remote_success_commits_terminal_outcome():
    async def scenario():
        runtime, store, _ = _runtime(
            idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            realtime=_SuccessfulRealtime(),
            include_server=False,
        )
        result = await runtime.execute_capability(
            "tool.remote",
            {},
            _identity(),
            invocation_id="inv-client-success",
            connection_id="conn-1",
        )
        assert result.output["source"] == "client"
        persisted = store.items["inv-client-success"]
        assert persisted.state is CapabilityInvocationState.COMPLETED
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.TERMINAL_COMMITTED
        )

    asyncio.run(scenario())


def test_r6_b_remote_error_is_a_committed_terminal_remote_outcome():
    async def scenario():
        runtime, store, _ = _runtime(
            idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            realtime=_RemoteErrorRealtime(),
            include_server=False,
        )
        with pytest.raises(CapabilityError) as raised:
            await runtime.execute_capability(
                "tool.remote",
                {},
                _identity(),
                invocation_id="inv-client-error",
                connection_id="conn-1",
            )
        assert raised.value.code == "CLIENT_DENIED"
        persisted = store.items["inv-client-error"]
        assert persisted.state is CapabilityInvocationState.FAILED
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.TERMINAL_COMMITTED
        )

    asyncio.run(scenario())


def test_r6_b_send_failure_is_normalized_to_remote_connection_lost():
    class FailingSocket:
        async def send_json(self, payload):
            raise ConnectionError("send failed")

    async def scenario():
        registry = ConnectionRegistry()
        registry.register(
            "session-1",
            "user-1",
            FailingSocket(),
            connection_id="conn-1",
        )
        registry.activate("conn-1")
        realtime = RealtimeMultiplexer(registry)

        with pytest.raises(RemoteConnectionLost):
            await realtime.invoke(
                RealtimeEnvelope(
                    type="capability.invoke",
                    message_id="msg-r6-b",
                    connection_id="conn-1",
                    invocation_id="inv-send-fail",
                    payload={},
                )
            )
        assert await realtime.multiplexer.pending_count() == 0

    asyncio.run(scenario())
