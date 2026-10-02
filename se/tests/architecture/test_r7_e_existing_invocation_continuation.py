from __future__ import annotations

import asyncio

import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.tool import ToolExecutionResult
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.agent.contracts.resume import (
    ResumeInvocationAction,
    ResumeInvocationActionKind,
)
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
)
from se.src.runtimes.capability.contracts.error import (
    CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT,
    CAPABILITY_CONTINUATION_STALE,
    CAPABILITY_CONTINUATION_UNSAFE,
    CapabilityContinuationDispatchGuardError,
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
    store.items[invocation.invocation_id].max_attempts = 5

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



@pytest.mark.asyncio
async def test_r7_e_wrong_mode_state_wait_reason_and_lineage_fail_before_attempt():
    cases = (
        ("wrong-mode", None, None, None),
        ("state", CapabilityInvocationState.COMPLETED, None, None),
        ("wait", None, CapabilityWaitReason.DEPENDENCY, None),
        ("lineage", None, None, "missing"),
    )
    for case, state, wait_reason, lineage in cases:
        runtime, store, invocation, fingerprint = await _runtime(
            idempotency=CapabilityIdempotency.IDEMPOTENT,
            outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
        )
        current = store.items[invocation.invocation_id]
        if state is not None:
            current.state = state
        if wait_reason is not None:
            current.wait_reason = wait_reason
        if lineage == "missing":
            current.execution_id = None

        mode = (
            ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED
            if case == "wrong-mode"
            else ExistingInvocationContinuationMode.REPLAY_SAFE
        )
        with pytest.raises(CapabilityError) as raised:
            await runtime.continue_invocation(
                invocation.invocation_id,
                target_connection_id=K2,
                mode=mode,
                expected_revision=invocation.revision,
                expected_request_fingerprint=fingerprint,
            )
        assert raised.value.code in {
            CAPABILITY_CONTINUATION_UNSAFE,
            "CAPABILITY_CONTINUATION_INVALID_STATE",
        }
        assert [item.attempt_number for item in await store.list_attempts(
            invocation.invocation_id
        )] == [1]


@pytest.mark.asyncio
async def test_r7_e_durable_argument_fingerprint_corruption_rejects_before_attempt():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )
    store.items[invocation.invocation_id].arguments = {"value": "corrupted"}

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


@pytest.mark.asyncio
async def test_r7_e_target_must_be_new_active_same_principal_client_generation():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )

    with pytest.raises(CapabilityError) as same_generation:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K1,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )
    assert same_generation.value.code == "CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE"

    runtime.connection_registry.disconnect(K2)
    with pytest.raises(CapabilityError) as inactive:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )
    assert inactive.value.code == "CAPABILITY_CONTINUATION_TARGET_UNAVAILABLE"

    runtime.connection_registry.register(
        "foreign-session",
        "foreign-user",
        socket=object(),
        metadata={"client_id": CLIENT_ID},
        connection_id="conn-r7e-foreign",
    )
    runtime.connection_registry.activate("conn-r7e-foreign")
    with pytest.raises(CapabilityError) as foreign:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id="conn-r7e-foreign",
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )
    assert foreign.value.code == "CAPABILITY_UNAUTHORIZED"
    assert [item.attempt_number for item in await store.list_attempts(
        invocation.invocation_id
    )] == [1]


@pytest.mark.asyncio
async def test_r7_e_unknown_idempotency_cannot_replay_unknown_outcome():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.UNKNOWN,
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
    assert [item.attempt_number for item in await store.list_attempts(
        invocation.invocation_id
    )] == [1]



@pytest.mark.asyncio
async def test_r7_e_nonterminal_prior_attempt_history_is_fail_closed():
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )
    store.attempts["att-old-1"].state = CapabilityInvocationState.RUNNING

    with pytest.raises(CapabilityError) as raised:
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
        )

    assert raised.value.code == CAPABILITY_CONTINUATION_ATTEMPT_CONFLICT
    assert [item.attempt_number for item in await store.list_attempts(
        invocation.invocation_id
    )] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("idempotency", "outcome", "action_kind"),
    [
        (
            CapabilityIdempotency.NON_IDEMPOTENT,
            RemoteOutcomeState.NOT_DISPATCHED,
            ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
        ),
        (
            CapabilityIdempotency.IDEMPOTENT,
            RemoteOutcomeState.OUTCOME_UNKNOWN,
            ResumeInvocationActionKind.REPLAY_SAFE,
        ),
    ],
)
async def test_r7_f4_agent_adapter_enters_exact_r7_e_continuation_path(
    idempotency,
    outcome,
    action_kind,
):
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=idempotency,
        outcome=outcome,
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy=None,
        execution_policy=None,
    )
    context = AgentExecutionContext.create(
        execution_id=EXECUTION_ID,
        agent_id="agent-r7f4",
        session_id="session-r7e",
        correlation_id="corr-r7f4",
        identity=Identity(
            user_id=USER_ID,
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(),
        connection_id=K2,
    )
    context.iteration = 3
    action = ResumeInvocationAction(
        invocation_id=invocation.invocation_id,
        tool_call_id=TOOL_CALL_ID,
        ordinal=0,
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        request_fingerprint=fingerprint,
        idempotency=idempotency,
        expected_invocation_revision=invocation.revision,
        expected_invocation_state=CapabilityInvocationState.WAITING,
        expected_remote_outcome_state=outcome,
        action=action_kind,
    )

    result = await adapter.continue_invocation(context, action)

    persisted = await store.get(invocation.invocation_id)
    attempts = await store.list_attempts(invocation.invocation_id)
    assert result.invocation_id == invocation.invocation_id
    assert result.tool_call_id == TOOL_CALL_ID
    assert result.metadata["r7_resume_action"] == action_kind.value
    assert result.metadata["continuation_mode"] == action_kind.value
    assert persisted is not None
    assert persisted.attempt == 2
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
    assert [item.attempt_number for item in attempts] == [1, 2]


@pytest.mark.asyncio
async def test_r12_f3a_dispatch_guard_runs_after_attempt_setup_before_remote_send():
    realtime = _Realtime()
    runtime, store, invocation, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        outcome=RemoteOutcomeState.NOT_DISPATCHED,
        realtime=realtime,
    )
    observed = []

    async def reject_at_canonical_boundary(
        current_invocation,
        selected_implementation_id,
        target_connection_id,
        origin_connection_id,
    ):
        observed.append(
            (
                current_invocation.state,
                current_invocation.attempt,
                selected_implementation_id,
                target_connection_id,
                origin_connection_id,
            )
        )
        raise CapabilityContinuationDispatchGuardError(
            "RECOVERY_ACTIVE_LEASE_FENCE_LOST",
            "lease changed after outer prepare",
            retryable=True,
        )

    with pytest.raises(
        CapabilityContinuationDispatchGuardError,
        match="lease changed after outer prepare",
    ):
        await runtime.continue_invocation(
            invocation.invocation_id,
            target_connection_id=K2,
            mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
            expected_revision=invocation.revision,
            expected_request_fingerprint=fingerprint,
            continuation_dispatch_guard=reject_at_canonical_boundary,
        )

    assert realtime.calls == []
    assert len(observed) == 1
    state, attempt_number, implementation_id, target, origin = observed[0]
    assert state is CapabilityInvocationState.RUNNING
    assert attempt_number == 2
    assert implementation_id == f"{K2}:{CAPABILITY_ID}"
    assert target == K2
    assert origin == K1

    persisted = await store.get(invocation.invocation_id)
    attempts = await store.list_attempts(invocation.invocation_id)
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.WAITING
    assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
    assert persisted.remote_outcome_state is RemoteOutcomeState.NOT_DISPATCHED
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert attempts[-1].state is CapabilityInvocationState.FAILED



@pytest.mark.asyncio
async def test_r12_f3a_two_slot_guard_failure_preserves_both_presend_r6_truth():
    realtime = _Realtime()
    runtime, store, invocation_a, fingerprint = await _runtime(
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        outcome=RemoteOutcomeState.NOT_DISPATCHED,
        realtime=realtime,
    )

    invocation_b = invocation_a.model_copy(
        update={
            "invocation_id": "inv-r7e-b",
            "tool_call_id": "call-r7e-b",
            "revision": 0,
            "attempt": 1,
            "max_attempts": 1,
        }
    )
    await runtime.invocation_lifecycle.create(invocation_b)
    await store.save_attempt(
        CapabilityInvocationAttempt(
            attempt_id="att-old-b",
            invocation_id=invocation_b.invocation_id,
            attempt_number=1,
            implementation_id=f"{K1}:{CAPABILITY_ID}",
            driver_kind="REMOTE_CLIENT",
            connection_id=K1,
            state=CapabilityInvocationState.FAILED,
        )
    )

    persisted_a_before = await store.get(invocation_a.invocation_id)
    persisted_b_before = await store.get(invocation_b.invocation_id)
    assert persisted_a_before is not None
    assert persisted_b_before is not None

    action_a = ResumeInvocationAction(
        invocation_id=invocation_a.invocation_id,
        tool_call_id=invocation_a.tool_call_id,
        ordinal=0,
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        request_fingerprint=fingerprint,
        idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
        expected_invocation_revision=persisted_a_before.revision,
        expected_invocation_state=CapabilityInvocationState.WAITING,
        expected_remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        action=ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
    )
    action_b = action_a.model_copy(
        update={
            "invocation_id": invocation_b.invocation_id,
            "tool_call_id": invocation_b.tool_call_id,
            "ordinal": 1,
            "expected_invocation_revision": persisted_b_before.revision,
        }
    )

    context = AgentExecutionContext.create(
        execution_id=EXECUTION_ID,
        agent_id="agent-r12-f3a-batch",
        session_id="session-r7e",
        correlation_id="corr-r12-f3a-batch",
        identity=Identity(
            user_id=USER_ID,
            session_id="session-r7e",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(max_parallel_tools=2),
        connection_id=K2,
        remaining_active_budget_seconds=30.0,
    )
    context.iteration = 1

    class RuntimeExecutor:
        async def continue_invocation(
            self,
            current_context,
            action,
            *,
            continuation_dispatch_guard=None,
        ):
            result = await runtime.continue_invocation(
                action.invocation_id,
                target_connection_id=current_context.connection_id,
                mode=ExistingInvocationContinuationMode(
                    action.action.value
                ),
                expected_revision=action.expected_invocation_revision,
                expected_request_fingerprint=action.request_fingerprint,
                cancellation_event=current_context.cancellation_event,
                continuation_dispatch_guard=continuation_dispatch_guard,
            )
            return ToolExecutionResult(
                execution_id=current_context.execution_id,
                iteration=current_context.iteration,
                invocation_id=action.invocation_id,
                tool_call_id=action.tool_call_id,
                capability_id=action.capability_id,
                success=True,
                output=result.output,
                metadata=dict(result.metadata),
            )

    coordinator = AgentToolExecutionCoordinator(RuntimeExecutor())
    a_inside_guard = asyncio.Event()
    release_a = asyncio.Event()

    async def prepare(action):
        return action

    async def canonical_guard(
        raw_action,
        current_invocation,
        selected_implementation_id,
        target_connection_id,
        origin_connection_id,
    ):
        assert current_invocation.invocation_id == raw_action.invocation_id
        assert selected_implementation_id == f"{K2}:{CAPABILITY_ID}"
        assert target_connection_id == K2
        assert origin_connection_id == K1

        if raw_action.invocation_id == invocation_a.invocation_id:
            a_inside_guard.set()
            await release_a.wait()
            return

        await a_inside_guard.wait()
        release_a.set()
        raise CapabilityContinuationDispatchGuardError(
            "RECOVERY_ACTIVE_LEASE_FENCE_LOST",
            "slot B lost recovery authority at canonical send seam",
            retryable=True,
        )

    with pytest.raises(CapabilityContinuationDispatchGuardError):
        await coordinator.continue_invocations(
            context,
            [action_a, action_b],
            max_parallel=2,
            pre_dispatch_prepare=prepare,
            canonical_dispatch_guard=canonical_guard,
            preserve_started_on_failure=True,
        )

    assert realtime.calls == []

    persisted_a = await store.get(invocation_a.invocation_id)
    persisted_b = await store.get(invocation_b.invocation_id)
    assert persisted_a is not None
    assert persisted_b is not None
    for persisted in (persisted_a, persisted_b):
        assert persisted.state is CapabilityInvocationState.WAITING
        assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.NOT_DISPATCHED
        )

    attempts_a = await store.list_attempts(invocation_a.invocation_id)
    attempts_b = await store.list_attempts(invocation_b.invocation_id)
    assert [item.attempt_number for item in attempts_a] == [1, 2]
    assert [item.attempt_number for item in attempts_b] == [1, 2]
    assert attempts_a[-1].state is CapabilityInvocationState.FAILED
    assert attempts_b[-1].state is CapabilityInvocationState.FAILED
