from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.recovery import (
    RecoveryActivationResult,
    RecoveryContinuationAuthority,
    RecoveryInferenceDisposition,
    RecoveryInvocationAction,
    RecoveryPlan,
    recovery_plan_fingerprint,
)
from se.src.runtimes.agent.contracts.resume import (
    ResumeInvocationActionKind,
    ResumeTriggerType,
)
from se.src.runtimes.agent.contracts.tool import ToolExecutionResult
from se.src.runtimes.agent.recovery_execution import (
    AgentRecoveryExecutionService,
    RecoveryExecutionError,
)
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)


EXECUTION = "exec-r12-f3a"
SESSION = "session-r12-f3a"
AGENT = "agent-r12-f3a"
USER = "user-r12-f3a"
CLIENT = "client-r12-f3a"
OLD_CONNECTION = "conn-old-r12-f3a"
NEW_CONNECTION = "conn-new-r12-f3a"
INVOCATION = "inv-r12-f3a"
TOOL_CALL = "call-r12-f3a"
CAPABILITY = "tool.echo"
IMPLEMENTATION = "client:tool.echo"


class _InvocationStore:
    def __init__(self, invocation):
        self.invocation = invocation

    async def get(self, invocation_id):
        assert invocation_id == self.invocation.invocation_id
        return self.invocation


class _CapabilityRuntime:
    def __init__(self, invocation, *, implementation_id=IMPLEMENTATION):
        self.invocation_lifecycle = SimpleNamespace(
            store=_InvocationStore(invocation)
        )
        self.tool_quota_service = None
        self._implementation_id = implementation_id
        self.connection_registry = SimpleNamespace(
            get=lambda connection_id: SimpleNamespace(
                metadata={"client_id": CLIENT},
                connection_id=connection_id,
            )
        )

    def _resolve_continuation_target(
        self,
        invocation,
        *,
        target_connection_id,
    ):
        assert invocation.invocation_id == INVOCATION
        assert target_connection_id == NEW_CONNECTION
        return (
            object(),
            SimpleNamespace(implementation_id=self._implementation_id),
        )


class _ContinuationExecutor:
    def __init__(self):
        self.calls = []

    async def continue_invocation(self, context, action):
        self.calls.append(action)
        return ToolExecutionResult(
            execution_id=context.execution_id,
            iteration=context.iteration,
            invocation_id=action.invocation_id,
            tool_call_id=action.tool_call_id,
            capability_id=action.capability_id,
            success=True,
            output={"continued": True},
            metadata={"attempt": 1},
        )


class _Store:
    def __init__(self, *, fence_results=None, committed=None):
        self.fence_results = list(fence_results or [])
        self.committed = dict(committed or {})
        self.fence_calls = []
        self.save_calls = []
        self.promote_calls = []

    async def has_active_execution_lease_fence(self, execution_id, **kwargs):
        self.fence_calls.append((execution_id, kwargs))
        if self.fence_results:
            return self.fence_results.pop(0)
        return True

    async def load_tool_result(self, execution_id, tool_call_id):
        return self.committed.get(tool_call_id)

    async def save_tool_result(self, values):
        self.save_calls.append(dict(values))
        record = SimpleNamespace(
            **values,
            commit_state="COMMITTED",
        )
        self.committed[values["tool_call_id"]] = record
        return record

    async def load_committed_tool_result(self, execution_id, tool_call_id):
        self.promote_calls.append((execution_id, tool_call_id))
        return self.committed.get(tool_call_id)


def _invocation():
    return SimpleNamespace(
        invocation_id=INVOCATION,
        execution_id=EXECUTION,
        tool_call_id=TOOL_CALL,
        capability_id=CAPABILITY,
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint="fp-r12-f3a",
        revision=7,
        state=CapabilityInvocationState.CREATED,
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        origin_client_id=CLIENT,
        connection_id=OLD_CONNECTION,
        owner_user_id=USER,
        arguments={"value": "hello"},
        workflow_id=None,
        session_id=SESSION,
    )


def _action(*, kind=ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED):
    return RecoveryInvocationAction(
        invocation_id=INVOCATION,
        tool_call_id=TOOL_CALL,
        ordinal=0,
        capability_id=CAPABILITY,
        capability_version="1.0",
        request_fingerprint="fp-r12-f3a",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        expected_invocation_revision=7,
        expected_invocation_state=CapabilityInvocationState.CREATED,
        expected_remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        origin_client_id=CLIENT,
        origin_connection_id=OLD_CONNECTION,
        action=kind,
        continuation_authority=(
            None
            if kind is ResumeInvocationActionKind.REUSE_COMMITTED
            else RecoveryContinuationAuthority(
                target_client_id=CLIENT,
                target_connection_id=NEW_CONNECTION,
                implementation_id=IMPLEMENTATION,
            )
        ),
        tool_quota_authority=None,
    )


def _plan(action):
    values = {
        "execution_id": EXECUTION,
        "checkpoint_id": "cp-r12-f3a",
        "expected_execution_revision": 4,
        "recovery_fingerprint": "recovery-fp-r12-f3a",
        "expected_unowned_lease_generation": 8,
        "checkpoint_origin_client_id": CLIENT,
        "checkpoint_origin_connection_id": OLD_CONNECTION,
        "agent_id": AGENT,
        "session_id": SESSION,
        "task_id": None,
        "task_revision": None,
        "branch_id": None,
        "branch_revision": None,
        "parent_execution_id": None,
        "retry_of_execution_id": None,
        "base_execution_id": None,
        "base_checkpoint_id": None,
        "resolved_recovery_principal": USER,
        "iteration": 1,
        "recovery_iteration_id": f"{EXECUTION}:iteration:1",
        "inference_request_id": None,
        "inference_disposition": RecoveryInferenceDisposition.NO_INFERENCE,
        "ordered_tool_call_ids": (TOOL_CALL,),
        "transcript_snapshot": (),
        "remaining_active_budget_seconds": 30.0,
        "wait_expires_at": None,
        "task_budget_incarnation_generation": None,
        "task_budget_revision": None,
        "target_trigger": ResumeTriggerType.SERVER_RECOVERY,
        "target_client_id": (
            CLIENT
            if action.action is not ResumeInvocationActionKind.REUSE_COMMITTED
            else None
        ),
        "target_connection_id": (
            NEW_CONNECTION
            if action.action is not ResumeInvocationActionKind.REUSE_COMMITTED
            else None
        ),
        "invocation_actions": (action,),
    }
    return RecoveryPlan(
        **values,
        plan_fingerprint=recovery_plan_fingerprint(
            {**values, "plan_fingerprint": ""}
        ),
    )


def _activation(plan):
    return RecoveryActivationResult(
        claim_id="claim-r12-f3a",
        resume_request_id="rr-r12-f3a",
        execution_id=EXECUTION,
        checkpoint_id=plan.checkpoint_id,
        source_execution_revision=plan.expected_execution_revision,
        consumed_execution_revision=plan.expected_execution_revision + 1,
        activation_owner_instance_id="worker-r12-f3a",
        lease_generation=plan.expected_unowned_lease_generation + 1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def _context(plan, activation):
    context = AgentExecutionContext.create(
        execution_id=EXECUTION,
        agent_id=AGENT,
        session_id=SESSION,
        correlation_id="corr-r12-f3a",
        identity=Identity(
            user_id=USER,
            session_id=SESSION,
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(),
        connection_id=plan.target_connection_id,
        remaining_active_budget_seconds=30.0,
        activate_budget=False,
        metadata={
            "client_id": plan.target_client_id,
            "r12_recovery_plan_fingerprint": plan.plan_fingerprint,
        },
    )
    context.iteration = plan.iteration
    context.resume_revision = activation.consumed_execution_revision
    return context


def _committed_record(*, output=None):
    return SimpleNamespace(
        id=f"{EXECUTION}:{TOOL_CALL}",
        execution_id=EXECUTION,
        iteration_id=f"{EXECUTION}:iteration:1",
        invocation_id=INVOCATION,
        tool_call_id=TOOL_CALL,
        capability_id=CAPABILITY,
        success=True,
        output=output if output is not None else {"reused": True},
        error_code=None,
        error_message=None,
        retryable=False,
        extra_metadata={"attempt": 1},
        attempt=1,
        commit_state="COMMITTED",
    )


@pytest.mark.asyncio
async def test_r12_f3a_continues_same_invocation_and_projects_only_under_fence():
    action = _action()
    plan = _plan(action)
    activation = _activation(plan)
    context = _context(plan, activation)
    store = _Store()
    runtime = _CapabilityRuntime(_invocation())
    executor = _ContinuationExecutor()
    coordinator = AgentToolExecutionCoordinator(executor)
    service = AgentRecoveryExecutionService(store, runtime, coordinator)

    results = await service.execute_active_tool_batch(
        context,
        plan=plan,
        activation=activation,
    )

    assert len(executor.calls) == 1
    assert executor.calls[0].invocation_id == INVOCATION
    assert len(store.save_calls) == 1
    assert store.save_calls[0]["invocation_id"] == INVOCATION
    assert results[0].tool_call_id == TOOL_CALL
    assert results[0].output == {"continued": True}
    assert all(
        call[1]["expected_lease_expires_at"]
        == activation.lease_expires_at
        for call in store.fence_calls
    )


@pytest.mark.asyncio
async def test_r12_f3a_implementation_drift_causes_zero_external_dispatch():
    action = _action()
    plan = _plan(action)
    activation = _activation(plan)
    context = _context(plan, activation)
    store = _Store()
    runtime = _CapabilityRuntime(
        _invocation(),
        implementation_id="client:replacement",
    )
    executor = _ContinuationExecutor()
    service = AgentRecoveryExecutionService(
        store,
        runtime,
        AgentToolExecutionCoordinator(executor),
    )

    with pytest.raises(RecoveryExecutionError) as exc_info:
        await service.execute_active_tool_batch(
            context,
            plan=plan,
            activation=activation,
        )

    assert exc_info.value.code == "RECOVERY_CONTINUATION_AFFINITY_CHANGED"
    assert executor.calls == []
    assert store.save_calls == []


@pytest.mark.asyncio
async def test_r12_f3a_lease_loss_after_dispatch_preserves_r6_but_blocks_agent_projection():
    action = _action()
    plan = _plan(action)
    activation = _activation(plan)
    context = _context(plan, activation)
    # Initial batch fence PASS, per-slot dispatch fence PASS, projection fence FAIL.
    store = _Store(fence_results=[True, True, False])
    runtime = _CapabilityRuntime(_invocation())
    executor = _ContinuationExecutor()
    service = AgentRecoveryExecutionService(
        store,
        runtime,
        AgentToolExecutionCoordinator(executor),
    )

    with pytest.raises(RecoveryExecutionError) as exc_info:
        await service.execute_active_tool_batch(
            context,
            plan=plan,
            activation=activation,
        )

    assert exc_info.value.code == "RECOVERY_ACTIVE_LEASE_FENCE_LOST"
    assert len(executor.calls) == 1
    assert store.save_calls == []
    assert store.promote_calls == []


@pytest.mark.asyncio
async def test_r12_f3a_reuse_committed_is_read_only_and_never_dispatches():
    action = _action(kind=ResumeInvocationActionKind.REUSE_COMMITTED)
    plan = _plan(action)
    activation = _activation(plan)
    context = _context(plan, activation)
    record = _committed_record()
    store = _Store(committed={TOOL_CALL: record})
    runtime = _CapabilityRuntime(_invocation())
    executor = _ContinuationExecutor()
    service = AgentRecoveryExecutionService(
        store,
        runtime,
        AgentToolExecutionCoordinator(executor),
    )

    results = await service.execute_active_tool_batch(
        context,
        plan=plan,
        activation=activation,
    )

    assert executor.calls == []
    assert store.save_calls == []
    assert store.promote_calls == []
    assert results[0].output == {"reused": True}
