from __future__ import annotations

import math

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import (
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_OUTCOME_UNKNOWN,
    REMOTE_RESULT_RECONCILIATION_REQUIRED,
    R6_RECONCILIATION_ERROR_CODES,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.fingerprint import (
    capability_request_fingerprint,
)
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


def test_r6_a_definition_defaults_to_fail_safe_unknown_idempotency():
    definition = CapabilityDefinition(
        id="tool.legacy",
        name="tool.legacy",
        description="legacy",
    )
    assert definition.idempotency is CapabilityIdempotency.UNKNOWN


def test_r6_a_request_fingerprint_is_canonical_and_semantic():
    first = capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="2.0",
        arguments={"b": 2, "a": {"y": 2, "x": 1}},
    )
    reordered = capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="2.0",
        arguments={"a": {"x": 1, "y": 2}, "b": 2},
    )
    different_version = capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="2.1",
        arguments={"a": {"x": 1, "y": 2}, "b": 2},
    )

    assert first == reordered
    assert len(first) == 64
    assert first != different_version

    with pytest.raises(ValueError):
        capability_request_fingerprint(
            capability_id="tool.echo",
            capability_version="2.0",
            arguments={"value": math.nan},
        )


def test_r6_a_reconciliation_error_codes_are_stable():
    assert R6_RECONCILIATION_ERROR_CODES == {
        "REMOTE_OUTCOME_UNKNOWN",
        "REMOTE_RESULT_RECONCILIATION_REQUIRED",
        "REMOTE_INVOCATION_CONFLICT",
    }
    assert REMOTE_OUTCOME_UNKNOWN in R6_RECONCILIATION_ERROR_CODES
    assert (
        REMOTE_RESULT_RECONCILIATION_REQUIRED
        in R6_RECONCILIATION_ERROR_CODES
    )
    assert REMOTE_INVOCATION_CONFLICT in R6_RECONCILIATION_ERROR_CODES


@pytest.mark.asyncio
async def test_r6_a_runtime_snapshots_identity_idempotency_and_fingerprint():
    class EchoDriver(BaseCapabilityDriver):
        async def execute(self, context, arguments):
            return arguments

    definition = CapabilityDefinition(
        id="tool.echo",
        version="3.4",
        name="tool.echo",
        description="echo",
        idempotency=CapabilityIdempotency.DEDUPLICATED,
    )
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store)
    )
    runtime.register_capability(EchoDriver(definition))

    result = await runtime.execute_capability(
        "tool.echo",
        {"value": "x"},
        Identity(user_id="user-r6-a", auth_type="jwt"),
        invocation_id="inv-runtime-r6-a",
    )

    persisted = await store.get("inv-runtime-r6-a")
    assert persisted is not None
    assert result.output == {"value": "x"}
    assert persisted.capability_version == "3.4"
    assert persisted.idempotency is CapabilityIdempotency.DEDUPLICATED
    assert persisted.owner_user_id == "user-r6-a"
    assert persisted.origin_client_id is None
    assert persisted.request_fingerprint == capability_request_fingerprint(
        capability_id="tool.echo",
        capability_version="3.4",
        arguments={"value": "x"},
    )
    # R6-B owns live remote certainty transitions.
    assert persisted.remote_outcome_state is None


@pytest.mark.asyncio
async def test_r6_a_sql_store_reads_new_fields_and_keeps_revision_cas():
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
    store = SqlCapabilityInvocationStore(uow_factory)

    fingerprint = capability_request_fingerprint(
        capability_id="tool.remote",
        capability_version="7.0",
        arguments={"value": "x"},
    )
    item = CapabilityInvocation(
        invocation_id="inv-r6-a-sql",
        capability_id="tool.remote",
        capability_version="7.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        request_fingerprint=fingerprint,
        owner_user_id="user-r6-a",
        origin_client_id="client-r6-a",
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        state=CapabilityInvocationState.CREATED,
    )
    await store.create(item)

    loaded = await store.get(item.invocation_id)
    assert loaded is not None
    assert loaded.idempotency is CapabilityIdempotency.NON_IDEMPOTENT
    assert loaded.remote_outcome_state is RemoteOutcomeState.NOT_DISPATCHED
    assert loaded.request_fingerprint == fingerprint
    assert loaded.owner_user_id == "user-r6-a"
    assert loaded.origin_client_id == "client-r6-a"

    loaded.remote_outcome_state = RemoteOutcomeState.IN_FLIGHT
    loaded.revision = 1
    assert await store.compare_and_set(
        loaded,
        expected_revision=0,
    ) is True

    stale = loaded.model_copy(
        update={
            "revision": 2,
            "remote_outcome_state": RemoteOutcomeState.OUTCOME_UNKNOWN,
        }
    )
    assert await store.compare_and_set(
        stale,
        expected_revision=0,
    ) is False

    attempt = CapabilityInvocationAttempt(
        attempt_id="att-r6-a-1",
        invocation_id=item.invocation_id,
        attempt_number=1,
        implementation_id="client-r6-a:tool.remote",
        driver_kind="REMOTE_CLIENT",
        connection_id="conn-r6-a",
        state=CapabilityInvocationState.RUNNING,
        metadata={"phase": "remote"},
    )
    await store.save_attempt(attempt)
    attempts = await store.list_attempts(item.invocation_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_id == "att-r6-a-1"
    assert attempts[0].metadata == {"phase": "remote"}

    reloaded = await store.get(item.invocation_id)
    assert reloaded is not None
    assert reloaded.revision == 1
    assert (
        reloaded.remote_outcome_state
        is RemoteOutcomeState.IN_FLIGHT
    )

    await driver.disconnect()
