from __future__ import annotations

import pytest
from sqlalchemy import select

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
    InvalidInvocationTransition,
    transition_invocation,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.connection.multiplexer import RemoteConnectionLost
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)


def invocation() -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id="inv-1",
        capability_id="tool.echo",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
    )


def test_terminal_invocation_states_are_immutable():
    item = invocation()
    transition_invocation(item, CapabilityInvocationState.DISPATCHING)
    transition_invocation(item, CapabilityInvocationState.RUNNING)
    transition_invocation(item, CapabilityInvocationState.COMPLETED, output="ok")

    with pytest.raises(InvalidInvocationTransition):
        transition_invocation(item, CapabilityInvocationState.RUNNING)


def test_waiting_requires_a_canonical_reason():
    item = invocation()
    transition_invocation(item, CapabilityInvocationState.DISPATCHING)
    with pytest.raises(InvalidInvocationTransition, match="wait_reason"):
        transition_invocation(item, CapabilityInvocationState.WAITING)
    _, item = transition_invocation(
        item,
        CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
    )
    assert item.wait_reason is CapabilityWaitReason.CONNECTION


@pytest.mark.asyncio
async def test_lifecycle_uses_cas_and_emits_canonical_events():
    events = []
    store = InMemoryCapabilityInvocationStore()
    async def publish(event):
        events.append(event)

    lifecycle = CapabilityInvocationLifecycle(store, publish)
    item = invocation()

    await lifecycle.create(item)
    await lifecycle.transition(item, CapabilityInvocationState.DISPATCHING)

    assert [event.event_name for event in events] == [
        "capability.invocation.created",
        "capability.invocation.dispatched",
    ]
    stale = item.model_copy(update={"revision": 0})
    with pytest.raises(RuntimeError, match="Concurrent"):
        await lifecycle.transition(stale, CapabilityInvocationState.RUNNING)


@pytest.mark.asyncio
async def test_capability_runtime_records_one_terminal_result():
    class EchoDriver(BaseCapabilityDriver):
        async def execute(self, context, arguments):
            return arguments["value"]

    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store)
    )
    runtime.register_capability(
        EchoDriver(
            CapabilityDefinition(
                id="tool.echo",
                name="tool.echo",
                description="echo",
            )
        )
    )
    result = await runtime.execute_capability(
        "tool.echo",
        {"value": "ok"},
        Identity(user_id="u1", auth_type="jwt"),
        invocation_id="inv-runtime",
        turn_id="turn-1",
    )

    persisted = store.items["inv-runtime"]
    assert result.output == "ok"
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.output == "ok"
    assert persisted.turn_id == "turn-1"
    assert persisted.revision == 3
    attempts = list(store.attempts.values())
    assert len(attempts) == 1
    assert attempts[0].invocation_id == "inv-runtime"
    assert attempts[0].state is CapabilityInvocationState.COMPLETED


@pytest.mark.asyncio
async def test_disconnect_retries_server_with_same_invocation_id():
    definition = CapabilityDefinition(
        id="tool.hybrid",
        name="tool.hybrid",
        description="client first, server fallback",
        idempotency=CapabilityIdempotency.IDEMPOTENT,
    )
    catalog = CapabilityCatalog()
    catalog.register_definition(definition)
    client = CapabilityImplementation.from_definition(
        definition,
        implementation_id="client-1:tool.hybrid",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id="u1",
        connection_id="conn-1",
    )
    server = CapabilityImplementation.from_definition(
        definition,
        implementation_id="server:tool.hybrid",
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="PYTHON",
    )
    for implementation in (client, server):
        catalog.register_implementation(implementation)
        catalog.transition_implementation(
            implementation.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    class Availability:
        def is_active(self, connection_id):
            return True

    class DisconnectingRealtime:
        async def invoke(self, envelope, timeout=None):
            raise RemoteConnectionLost("conn-1", envelope.invocation_id)

        async def cancel(self, connection_id, invocation_id):
            return None

    class ServerDriver(BaseCapabilityDriver):
        async def execute(self, context, arguments):
            return {"source": "server", "invocation_id": context.invocation_id}

    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=Availability()
        ),
        realtime=DisconnectingRealtime(),
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
    )
    runtime.driver_registry.bind(
        server.implementation_id, ServerDriver(definition)
    )
    identity = Identity(user_id="u1", auth_type="jwt", session_id="sess-1")

    result = await runtime.execute_capability(
        definition.capability_id,
        {},
        identity,
        invocation_id="inv-fallback",
        session_id="sess-1",
        connection_id="conn-1",
        metadata={"max_attempts": 2},
    )

    assert result.invocation_id == "inv-fallback"
    assert result.output == {"source": "server", "invocation_id": "inv-fallback"}
    assert result.metadata["attempt"] == 2
    attempts = sorted(store.attempts.values(), key=lambda item: item.attempt_number)
    assert [item.implementation_id for item in attempts] == [
        "client-1:tool.hybrid",
        "server:tool.hybrid",
    ]
    assert [item.state for item in attempts] == [
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.COMPLETED,
    ]
    assert store.items["inv-fallback"].state is CapabilityInvocationState.COMPLETED
    assert (
        store.items["inv-fallback"].remote_outcome_state.value
        == "OUTCOME_UNKNOWN"
    )


@pytest.mark.asyncio
async def test_sql_store_persists_invocation_attempt_and_cas():
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": ":memory:"},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    uow_factory = lambda: SqlAlchemyUnitOfWork(driver)
    lifecycle = CapabilityInvocationLifecycle(
        SqlCapabilityInvocationStore(uow_factory)
    )
    item = invocation().model_copy(update={"invocation_id": "inv-sql"})

    await lifecycle.create(item)
    item.attempt = 1
    attempt = await lifecycle.start_attempt(
        item,
        implementation_id="server:tool.echo",
        driver_kind="PYTHON",
        connection_id=None,
    )
    await lifecycle.transition(item, CapabilityInvocationState.DISPATCHING)
    await lifecycle.transition(item, CapabilityInvocationState.RUNNING)
    await lifecycle.finish_attempt(attempt, CapabilityInvocationState.COMPLETED)
    await lifecycle.transition(
        item, CapabilityInvocationState.COMPLETED, output={"ok": True}
    )

    async with uow_factory() as uow:
        stored = await uow.session.get(CapabilityInvocationRecord, "inv-sql")
        attempts = (
            await uow.session.execute(
                select(CapabilityInvocationAttemptRecord).where(
                    CapabilityInvocationAttemptRecord.invocation_id == "inv-sql"
                )
            )
        ).scalars().all()
        assert stored.state == "COMPLETED"
        assert stored.revision == 3
        assert stored.output == {"ok": True}
        assert len(attempts) == 1
        assert attempts[0].state == "COMPLETED"

    await driver.disconnect()
