from __future__ import annotations

import asyncio

import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
)
from se.src.runtimes.capability.contracts.error import (
    CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT,
    CAPABILITY_CONTINUATION_STALE,
    CAPABILITY_CONTINUATION_UNSAFE,
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    CapabilityWaitReason,
    ExistingInvocationContinuationMode,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.registry import ConnectionRegistry, ConnectionStateError


CAPABILITY_ID = "tool.r7e"
USER_ID = "user-r7e"
CLIENT_ID = "client-r7e"
K1 = "conn-r7e-k1"
K2 = "conn-r7e-k2"
EXECUTION_ID = "exec-r7e"
TOOL_CALL_ID = "call-r7e"


class _Realtime:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = []

    async def invoke(self, envelope, timeout=None):
        self.calls.append(envelope)
        if self.error is not None:
            raise self.error
        return {
            "source": "client",
            "invocation_id": envelope.invocation_id,
        }

    async def cancel(self, connection_id, invocation_id):
        return None


async def _runtime(
    *,
    idempotency: CapabilityIdempotency,
    outcome: RemoteOutcomeState,
    realtime: _Realtime | None = None,
    invocation_attempt: int = 1,
    persist_attempts: bool = True,
):
    definition = CapabilityDefinition(
        id=CAPABILITY_ID,
        version="1.0",
        name=CAPABILITY_ID,
        description="R7-E continuation test",
        idempotency=idempotency,
    )
    catalog = CapabilityCatalog()
    catalog.register_definition(definition)
    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id=f"{K2}:{CAPABILITY_ID}",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id=USER_ID,
        connection_id=K2,
        metadata={"client_id": CLIENT_ID},
    )
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    connections = ConnectionRegistry()
    connections.register(
        "session-r7e",
        USER_ID,
        socket=object(),
        metadata={"client_id": CLIENT_ID},
        connection_id=K2,
    )
    connections.activate(K2)

    store = InMemoryCapabilityInvocationStore()
    lifecycle = CapabilityInvocationLifecycle(store)
    runtime = CapabilityRuntime(
        authorization=AuthorizationService(),
        catalog=catalog,
        connection_registry=connections,
        realtime=realtime or _Realtime(),
        invocation_lifecycle=lifecycle,
    )

    arguments = {"value": "r7e"}
    fingerprint = capability_request_fingerprint(
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        arguments=arguments,
    )
    invocation = CapabilityInvocation(
        invocation_id="inv-r7e",
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        kind=definition.kind,
        execution_mode=definition.execution_mode,
        idempotency=idempotency,
        request_fingerprint=fingerprint,
        owner_user_id=USER_ID,
        origin_client_id=CLIENT_ID,
        remote_outcome_state=outcome,
        implementation_id=f"{K1}:{CAPABILITY_ID}",
        driver_kind="REMOTE_CLIENT",
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        session_id="session-r7e",
        execution_id=EXECUTION_ID,
        tool_call_id=TOOL_CALL_ID,
        connection_id=K1,
        attempt=invocation_attempt,
        max_attempts=max(1, invocation_attempt),
        arguments=arguments,
    )
    await lifecycle.create(invocation)

    if persist_attempts:
        for number in range(1, invocation_attempt + 1):
            await store.save_attempt(
                CapabilityInvocationAttempt(
                    attempt_id=f"att-old-{number}",
                    invocation_id=invocation.invocation_id,
                    attempt_number=number,
                    implementation_id=f"{K1}:{CAPABILITY_ID}",
                    driver_kind="REMOTE_CLIENT",
                    connection_id=K1,
                    state=CapabilityInvocationState.FAILED,
                )
            )

    return runtime, store, invocation, fingerprint


@pytest.mark.asyncio
async def test_r7_e_dispatch_not_dispatched_reuses_invocation_and_adds_one_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        outcome=RemoteOutcomeState.NOT_DISPATCHED,
    )

    result = await runtime.continue_invocation(
        invocation.invocation_id,
        target_connection_id=K2,
        mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
        expected_revision=invocation.revision,
        expected_request_fingerprint=fingerprint,
    )

    persisted = await store.get(invocation.invocation_id)
    attempts = await store.list_attempts(invocation.invocation_id)
    assert persisted is not None
    assert result.invocation_id == invocation.invocation_id
    assert result.metadata["attempt"] == 2
    assert result.metadata["continuation_mode"] == "DISPATCH_NOT_DISPATCHED"
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
    assert persisted.request_fingerprint == fingerprint
    assert persisted.attempt == 2
    assert persisted.max_attempts == 2
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert {item.invocation_id for item in attempts} == {invocation.invocation_id}


@pytest.mark.asyncio
async def test_r7_e_replay_safe_allows_idempotent_unknown_same_id():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )

    result = await runtime.continue_invocation(
        invocation.invocation_id,
        target_connection_id=K2,
        mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
        expected_revision=invocation.revision,
        expected_request_fingerprint=fingerprint,
    )

    persisted = await store.get(invocation.invocation_id)
    attempts = await store.list_attempts(invocation.invocation_id)
    assert result.invocation_id == "inv-r7e"
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_r7_e_unsafe_non_idempotent_replay_creates_no_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )

    with pytest.raises(CapabilityError) as raised:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )

    assert raised.value.code == CAPABILITY_CONTINUATION_UNSAFE
    assert len(await store.list_attempts(invocation.invocation_id)) == 1
    persisted = await store.get(invocation.invocation_id)
    assert persisted is not None
    assert persisted.revision == invocation.revision
    assert persisted.state is CapabilityInvocationState.WAITING


@pytest.mark.asyncio
async def test_r7_e_stale_revision_and_fingerprint_reject_before_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )

    with pytest.raises(CapabilityError) as stale:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision + 1,
            expected_request_fingerprint=fingerprint,
        )
    assert stale.value.code == CAPABILITY_CONTINUATION_STALE

    with pytest.raises(CapabilityError) as conflict:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint="different",
        )
    assert conflict.value.code == REMOTE_INVOCATION_CONFLICT
    assert len(await store.list_attempts(invocation.invocation_id)) == 1


@pytest.mark.asyncio
async def test_r7_e_attempt_history_conflict_is_fail_closed():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
        invocation_attempt=1,
        persist_attempts=False,
    )

    with pytest.raises(CapabilityError) as raised:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )

    assert raised.value.code == CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT
    persisted = await store.get(invocation.invocation_id)
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.WAITING
    assert persisted.attempt == 1
    assert await store.list_attempts(invocation.invocation_id) == []


@pytest.mark.asyncio
async def test_r7_e_cancelled_before_begin_creates_no_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )
    cancelled = asyncio.Event()
    cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
            cancellation_event=cancelled,
        )

    assert len(await store.list_attempts(invocation.invocation_id)) == 1


@pytest.mark.asyncio
async def test_r7_e_replay_safe_predispatch_failure_never_downgrades_old_inflight():
    realtime = _Realtime(
        error=ConnectionStateError("K2 became inactive before send")
    )
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.IN_FLIGHT,
        realtime=realtime,
    )

    with pytest.raises(CapabilityError) as raised:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )

    assert raised.value.code == "REMOTE_CONNECTION_LOST"
    persisted = await store.get(invocation.invocation_id)
    attempts = await store.list_attempts(invocation.invocation_id)
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.WAITING
    assert persisted.remote_outcome_state is RemoteOutcomeState.IN_FLIGHT
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert attempts[-1].state is CapabilityInvocationState.FAILED



@pytest.mark.asyncio
async def test_r7_e_definition_idempotency_drift_rejects_before_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )
    changed = runtime.catalog.get_definition(CAPABILITY_ID).model_copy(
        update={"idempotency": CapabilityIdempotency.NON_IDEMPOTENT}
    )
    runtime.catalog.register_definition(changed, allow_update=True)

    with pytest.raises(CapabilityError) as raised:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )

    assert raised.value.code == REMOTE_INVOCATION_CONFLICT
    assert [item.attempt_number for item in await store.list_attempts(
        invocation.invocation_id
    )] == [1]
