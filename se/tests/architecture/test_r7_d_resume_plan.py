from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    CheckpointPendingInvocation,
    DurableExecutionCheckpoint,
    InferenceMessage,
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
)
from se.src.runtimes.agent.persistence import DurableAgentStore, ExecutionConflictError
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.resume_planning import (
    AgentResumePlanningService,
    ResumePlanDeferred,
    ResumePlanRejected,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityImplementationState,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.reconciliation import (
    RemoteReconciliationResult,
    RemoteReconciliationStatus,
)
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.transport.gateway.api.v1.events_router import _resume_execution


NOW = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)


def _invocation(
    *,
    outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
    idempotency=CapabilityIdempotency.IDEMPOTENT,
    revision=4,
    request_fingerprint="a" * 64,
):
    return CapabilityInvocation(
        invocation_id="inv-1",
        capability_id="desktop.echo",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=idempotency,
        request_fingerprint=request_fingerprint,
        owner_user_id="user-1",
        origin_client_id="client-1",
        remote_outcome_state=outcome,
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        execution_id="exec-1",
        tool_call_id="call-1",
        connection_id="conn-old",
        revision=revision,
    )


class _InvocationStore:
    def __init__(self, invocation):
        self.invocation = invocation

    async def get(self, invocation_id):
        assert invocation_id == self.invocation.invocation_id
        return self.invocation.model_copy(deep=True)


class _ConnectionRegistry:
    def __init__(
        self,
        *,
        usable=True,
        user_id="user-1",
        client_id="client-1",
    ):
        self.snapshot = SimpleNamespace(
            is_usable=usable,
            user_id=user_id,
            metadata={"client_id": client_id},
        )

    def get(self, connection_id):
        assert connection_id == "conn-new"
        return self.snapshot


class _Catalog:
    def __init__(self, *, ready=True):
        self.ready = ready

    def list_implementations_for_connection(self, connection_id):
        assert connection_id == "conn-new"
        if not self.ready:
            return []
        return [
            SimpleNamespace(
                capability_id="desktop.echo",
                version="1.0",
                state=CapabilityImplementationState.ENABLED,
            )
        ]


class _CapabilityRuntime:
    def __init__(
        self,
        invocation,
        *,
        reconciliation_status=None,
        ready=True,
        reconciled_invocation=None,
        connection_usable=True,
        connection_user_id="user-1",
        connection_client_id="client-1",
    ):
        self.store = _InvocationStore(invocation)
        self.invocation_lifecycle = SimpleNamespace(store=self.store)
        self.catalog = _Catalog(ready=ready)
        self.connection_registry = _ConnectionRegistry(
            usable=connection_usable,
            user_id=connection_user_id,
            client_id=connection_client_id,
        )
        self.reconciliation_status = reconciliation_status
        self.reconciled_invocation = reconciled_invocation
        self.reconcile_calls = 0

    async def reconcile_remote_invocation(self, invocation_id, connection_id):
        self.reconcile_calls += 1
        if self.reconciled_invocation is not None:
            self.store.invocation = self.reconciled_invocation
        status = self.reconciliation_status or RemoteReconciliationStatus.UNKNOWN
        terminal_type = None
        terminal_payload = None
        if status is RemoteReconciliationStatus.TERMINAL:
            terminal_type = "result"
            terminal_payload = {"output": {"ok": True}}
        return RemoteReconciliationResult(
            invocation_id=invocation_id,
            status=status,
            capability_id="desktop.echo",
            capability_version="1.0",
            request_fingerprint="a" * 64,
            terminal_type=terminal_type,
            terminal_payload=terminal_payload,
        )


class _PlanStore:
    def __init__(self):
        self.execution = SimpleNamespace(
            id="exec-1",
            state="WAITING",
            wait_reason="CONNECTION",
            revision=7,
            current_checkpoint_id="cp-1",
            agent_id="agent-1",
            session_id="session-1",
            task_id="task-1",
            branch_id=None,
            parent_execution_id=None,
            retry_of_execution_id=None,
            base_execution_id=None,
            base_checkpoint_id=None,
            correlation_id="corr-1",
            context_state={"request_id": "req-1", "trace_id": "trace-1"},
        )
        self.checkpoint = DurableExecutionCheckpoint(
            checkpoint_id="cp-1",
            execution_id="exec-1",
            execution_revision=7,
            session_id="session-1",
            task_id="task-1",
            iteration=3,
            wait_reason="CONNECTION",
            remaining_active_budget_seconds=30.0,
            wait_expires_at=NOW + timedelta(minutes=5),
            origin_client_id="client-1",
            origin_connection_id="conn-old",
            transcript_snapshot=({"role": "user", "content": "hello"},),
            metadata={"request_id": "req-1", "trace_id": "trace-1"},
        )
        self.pending = (
            CheckpointPendingInvocation(
                checkpoint_id="cp-1",
                ordinal=0,
                invocation_id="inv-1",
                invocation_revision=2,
                tool_call_id="call-1",
                capability_id="desktop.echo",
                capability_version="1.0",
                request_fingerprint="a" * 64,
                idempotency="IDEMPOTENT",
                observed_remote_outcome_state="OUTCOME_UNKNOWN",
                origin_client_id="client-1",
                origin_connection_id="conn-old",
            ),
        )
        self.committed = None
        self.committed_by_tool_call = {}
        self.tool_call_ids = ["call-1"]
        self.task = SimpleNamespace(
            id="task-1",
            session_id="session-1",
            created_by="user-1",
            status="RUNNING",
        )

    async def load_execution(self, execution_id):
        return self.execution if execution_id == "exec-1" else None

    async def load_task(self, task_id):
        return self.task if task_id == "task-1" else None

    async def load_current_checkpoint(self, execution_id):
        return self.checkpoint

    async def load_checkpoint_pending_invocations(self, checkpoint_id):
        return self.pending

    async def load_iteration(
        self,
        execution_id,
        *,
        iteration_number=None,
        iteration_id=None,
    ):
        assert iteration_number == 3
        return SimpleNamespace(tool_call_ids=list(self.tool_call_ids))

    async def load_committed_checkpoint_transcript(
        self,
        execution_id,
        checkpoint_id,
        *,
        active_tool_call_ids=(),
    ):
        assert active_tool_call_ids == ("call-1",)
        return ({"role": "user", "content": "hello"},)

    async def load_committed_tool_result(self, execution_id, tool_call_id):
        return self.committed_by_tool_call.get(tool_call_id, self.committed)


def _service(store, caps):
    return AgentResumePlanningService(
        store,
        caps,
        now_utc=lambda: NOW,
    )


async def _build(service):
    return await service.build_resume_plan(
        "exec-1",
        "cp-1",
        target_user_id="user-1",
        target_client_id="client-1",
        target_connection_id="conn-new",
    )


@pytest.mark.asyncio
async def test_r7_d_unknown_idempotent_builds_replay_safe_plan_without_claim():
    store = _PlanStore()
    caps = _CapabilityRuntime(_invocation())

    plan = await _build(_service(store, caps))

    assert plan.expected_execution_revision == 7
    assert plan.ordered_tool_call_ids == ("call-1",)
    assert [item.action for item in plan.invocation_actions] == [
        ResumeInvocationActionKind.REPLAY_SAFE
    ]
    assert plan.invocation_actions[0].expected_invocation_revision == 4
    assert len(plan.plan_fingerprint) == 64
    assert store.execution.state == "WAITING"
    assert store.execution.revision == 7
    assert caps.reconcile_calls == 1


@pytest.mark.asyncio
async def test_r7_d_not_dispatched_classifies_without_reconciliation():
    store = _PlanStore()
    store.pending = (
        replace(
            store.pending[0],
            observed_remote_outcome_state="NOT_DISPATCHED",
        ),
    )
    caps = _CapabilityRuntime(
        _invocation(outcome=RemoteOutcomeState.NOT_DISPATCHED)
    )

    plan = await _build(_service(store, caps))

    assert plan.invocation_actions[0].action is (
        ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED
    )
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_reconciled_terminal_reuses_committed_projection():
    store = _PlanStore()
    store.committed = SimpleNamespace(
        commit_state="COMMITTED",
        execution_id="exec-1",
        tool_call_id="call-1",
        invocation_id="inv-1",
        capability_id="desktop.echo",
        success=True,
        output={"ok": True},
        error_code=None,
        error_message=None,
        retryable=False,
    )
    reconciled = _invocation(
        outcome=RemoteOutcomeState.TERMINAL_COMMITTED,
        revision=5,
    )
    reconciled.state = CapabilityInvocationState.COMPLETED
    reconciled.wait_reason = None
    reconciled.output = {"ok": True}
    caps = _CapabilityRuntime(
        _invocation(),
        reconciliation_status=RemoteReconciliationStatus.TERMINAL,
        reconciled_invocation=reconciled,
    )

    plan = await _build(_service(store, caps))

    action = plan.invocation_actions[0]
    assert action.action is ResumeInvocationActionKind.REUSE_COMMITTED
    assert action.expected_invocation_revision == 5
    assert action.expected_remote_outcome_state is (
        RemoteOutcomeState.TERMINAL_COMMITTED
    )


@pytest.mark.asyncio
async def test_r7_d_unknown_non_idempotent_never_produces_claimable_plan():
    store = _PlanStore()
    caps = _CapabilityRuntime(
        _invocation(idempotency=CapabilityIdempotency.NON_IDEMPOTENT)
    )
    store.pending = (
        replace(
            store.pending[0],
            idempotency="NON_IDEMPOTENT",
        ),
    )

    with pytest.raises(ResumePlanDeferred) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "REMOTE_OUTCOME_UNSAFE_TO_REPLAY"
    assert store.execution.state == "WAITING"
    assert store.execution.revision == 7


@pytest.mark.asyncio
async def test_r7_d_foreign_client_is_rejected_before_reconciliation():
    store = _PlanStore()
    caps = _CapabilityRuntime(_invocation())
    service = _service(store, caps)

    with pytest.raises(ResumePlanRejected) as raised:
        await service.build_resume_plan(
            "exec-1",
            "cp-1",
            target_user_id="user-1",
            target_client_id="client-foreign",
            target_connection_id="conn-new",
        )

    assert raised.value.code == "FOREIGN_CLIENT"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_semantic_fingerprint_drift_is_rejected():
    store = _PlanStore()
    caps = _CapabilityRuntime(
        _invocation(request_fingerprint="b" * 64)
    )

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "INVOCATION_SEMANTIC_CONFLICT"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_missing_target_capability_defers_safe_future_action():
    store = _PlanStore()
    store.pending = (
        replace(
            store.pending[0],
            observed_remote_outcome_state="NOT_DISPATCHED",
        ),
    )
    caps = _CapabilityRuntime(
        _invocation(outcome=RemoteOutcomeState.NOT_DISPATCHED),
        ready=False,
    )

    with pytest.raises(ResumePlanDeferred) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "PENDING_CAPABILITY_NOT_READY"


@pytest.mark.asyncio
async def test_r7_d_normalized_checkpoint_loader_uses_execution_pointer():
    execution = SimpleNamespace(
        id="exec-1",
        current_checkpoint_id="cp-1",
        revision=7,
    )
    checkpoint = SimpleNamespace(
        checkpoint_id="cp-1",
        execution_id="exec-1",
        execution_revision=7,
        session_id="session-1",
        task_id="task-1",
        branch_id=None,
        parent_checkpoint_id=None,
        iteration=3,
        wait_reason="CONNECTION",
        remaining_active_budget_seconds=30.0,
        wait_expires_at=NOW + timedelta(minutes=5),
        origin_client_id="client-1",
        origin_connection_id="conn-old",
        transcript_snapshot=[{"role": "user", "content": "hello"}],
        transcript_ref=None,
        transcript_version=None,
        side_effect_watermark=None,
        legacy_source_key=None,
        metadata_json={"trace_id": "trace-1"},
        created_at=NOW,
    )
    pending = [
        SimpleNamespace(
            checkpoint_id="cp-1",
            ordinal=0,
            invocation_id="inv-1",
            invocation_revision=2,
            tool_call_id="call-1",
            capability_id="desktop.echo",
            capability_version="1.0",
            request_fingerprint="a" * 64,
            idempotency="IDEMPOTENT",
            observed_remote_outcome_state="OUTCOME_UNKNOWN",
            origin_client_id="client-1",
            origin_connection_id="conn-old",
        )
    ]

    class Agents:
        async def get_execution(self, execution_id):
            return execution

        async def get_execution_checkpoint(self, checkpoint_id):
            return checkpoint

        async def list_checkpoint_pending_invocations(self, checkpoint_id):
            return pending

    class Uow:
        def __init__(self):
            self.agents = Agents()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def commit(self):
            return None

    store = DurableAgentStore(Uow)
    loaded = await store.load_current_checkpoint("exec-1")
    refs = await store.load_checkpoint_pending_invocations("cp-1")

    assert loaded.checkpoint_id == "cp-1"
    assert loaded.execution_revision == 7
    assert loaded.metadata == {"trace_id": "trace-1"}
    assert [item.invocation_id for item in refs] == ["inv-1"]


@pytest.mark.asyncio
async def test_r7_d_execution_resume_is_preflight_only_no_claim_or_runtime_start():
    action = ResumeInvocationAction(
        invocation_id="inv-1",
        tool_call_id="call-1",
        ordinal=0,
        capability_id="desktop.echo",
        capability_version="1.0",
        request_fingerprint="a" * 64,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        expected_invocation_revision=4,
        expected_invocation_state=CapabilityInvocationState.WAITING,
        expected_remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
        action=ResumeInvocationActionKind.REPLAY_SAFE,
    )
    plan = ResumePlan(
        execution_id="exec-1",
        checkpoint_id="cp-1",
        expected_execution_revision=7,
        plan_fingerprint="f" * 64,
        agent_id="agent-1",
        session_id="session-1",
        task_id=None,
        branch_id=None,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id="corr-1",
        trace_id=None,
        request_id=None,
        iteration=3,
        ordered_tool_call_ids=("call-1",),
        transcript_snapshot=(InferenceMessage(role="user", content="hello"),),
        remaining_active_budget_seconds=30.0,
        wait_expires_at=NOW + timedelta(minutes=5),
        target_user_id="user-1",
        target_client_id="client-1",
        target_connection_id="conn-new",
        invocation_actions=(action,),
    )

    class Planner:
        async def build_resume_plan(self, *args, **kwargs):
            return plan

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    class ForbiddenRuntime:
        async def claim_resume(self, *_args, **_kwargs):
            raise AssertionError("R7-D must not claim WAITING -> RUNNING")

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("R7-D must not execute AgentRuntime")

    snapshot = SimpleNamespace(
        is_usable=True,
        user_id="user-1",
        metadata={"client_id": "client-1"},
    )
    container = SimpleNamespace(
        connection_runtime=SimpleNamespace(
            registry=SimpleNamespace(get=lambda _: snapshot)
        ),
        resume_planning_service=Planner(),
        agent_runtime=ForbiddenRuntime(),
        continuation_service=SimpleNamespace(),
    )
    socket = Socket()
    envelope = RealtimeEnvelope(
        type="execution.resume",
        message_id="resume-1",
        connection_id="conn-new",
        execution_id="exec-1",
        payload={
            "execution_id": "exec-1",
            "checkpoint_id": "cp-1",
        },
    )

    await _resume_execution(
        socket,
        Identity(user_id="user-1", auth_type="jwt"),
        container,
        "conn-new",
        envelope,
    )

    assert len(socket.messages) == 1
    assert socket.messages[0]["type"] == "execution.resume.preflight"
    assert socket.messages[0]["payload"]["status"] == "PLAN_READY"
    assert socket.messages[0]["payload"]["activation"] == "R7_D_PREFLIGHT_ONLY"
    assert socket.messages[0]["payload"]["plan_fingerprint"] == "f" * 64


@pytest.mark.asyncio
async def test_r7_d_rejects_invocation_revision_regression_below_checkpoint_watermark():
    store = _PlanStore()
    store.pending = (
        replace(store.pending[0], invocation_revision=5),
    )
    caps = _CapabilityRuntime(_invocation(revision=4))

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "INVOCATION_REVISION_REGRESSION"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_rejects_remote_outcome_regression_from_unknown_to_not_dispatched():
    store = _PlanStore()
    caps = _CapabilityRuntime(
        _invocation(
            outcome=RemoteOutcomeState.NOT_DISPATCHED,
            revision=5,
        )
    )

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "REMOTE_OUTCOME_REGRESSION"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_terminal_task_never_produces_plan_ready():
    store = _PlanStore()
    store.task.status = "CANCELLED"
    caps = _CapabilityRuntime(_invocation())

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "TASK_TERMINAL"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_planner_revalidates_target_connection_authority():
    store = _PlanStore()
    caps = _CapabilityRuntime(
        _invocation(),
        connection_usable=False,
    )

    with pytest.raises(ResumePlanDeferred) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "RESUME_CONNECTION_UNAVAILABLE"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_reuse_committed_rejects_mismatched_terminal_projection():
    store = _PlanStore()
    store.committed = SimpleNamespace(
        commit_state="COMMITTED",
        execution_id="exec-1",
        tool_call_id="call-1",
        invocation_id="inv-other",
        capability_id="desktop.echo",
        success=True,
        output={"ok": True},
        error_code=None,
        error_message=None,
        retryable=False,
    )
    invocation = _invocation(
        outcome=RemoteOutcomeState.TERMINAL_COMMITTED,
        revision=5,
    )
    invocation.state = CapabilityInvocationState.COMPLETED
    invocation.wait_reason = None
    invocation.output = {"ok": True}
    caps = _CapabilityRuntime(invocation)

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "COMMITTED_TOOL_RESULT_CONFLICT"



@pytest.mark.asyncio
async def test_r7_d_partial_lifecycle_store_cannot_publish_waiting_without_checkpoint():
    class PartialDurableStore:
        def __init__(self):
            self.cas_calls = 0

        async def load_execution(self, execution_id):
            return None

        async def save_execution(self, values):
            return values

        async def compare_and_set_execution(
            self,
            execution_id,
            revision,
            values,
        ):
            self.cas_calls += 1
            return SimpleNamespace(revision=revision + 1)

    store = PartialDurableStore()
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )
    context = SimpleNamespace(
        execution_id="exec-1",
        task_id=None,
        parent_execution_id=None,
    )

    assert runtime._has_execution_lifecycle_store() is True
    assert runtime._has_normalized_waiting_authority(context) is False

    with pytest.raises(ExecutionConflictError, match="NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE"):
        await runtime._transition_running_durable(
            context,
            7,
            {"state": "WAITING", "wait_reason": "CONNECTION"},
            checkpoint_values={
                "checkpoint_id": "cp-1",
                "execution_id": "exec-1",
            },
            pending_invocations=(),
        )

    assert store.cas_calls == 0


@pytest.mark.asyncio
async def test_r7_d_execution_and_checkpoint_wait_reason_must_match():
    store = _PlanStore()
    store.execution.wait_reason = "HITL"
    caps = _CapabilityRuntime(_invocation())

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "WAIT_REASON_CONFLICT"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_authority", ["checkpoint", "snapshot", "invocation"])
async def test_r7_d_connection_resume_requires_stable_origin_client_authority(
    missing_authority,
):
    store = _PlanStore()
    invocation = _invocation()
    if missing_authority == "checkpoint":
        store.checkpoint = replace(store.checkpoint, origin_client_id=None)
    elif missing_authority == "snapshot":
        store.pending = (
            replace(store.pending[0], origin_client_id=None),
        )
    else:
        invocation.origin_client_id = None
    caps = _CapabilityRuntime(invocation)

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "RESUME_ORIGIN_CLIENT_MISSING"
    assert caps.reconcile_calls == 0


@pytest.mark.asyncio
async def test_r7_d_parallel_active_batch_requires_pending_or_committed_coverage():
    store = _PlanStore()
    store.tool_call_ids = ["call-1", "call-2", "call-3"]
    store.pending = (
        replace(
            store.pending[0],
            ordinal=2,
            tool_call_id="call-3",
            invocation_id="inv-3",
        ),
    )
    store.committed_by_tool_call["call-1"] = SimpleNamespace(
        commit_state="COMMITTED",
        execution_id="exec-1",
        tool_call_id="call-1",
    )
    caps = _CapabilityRuntime(_invocation())

    with pytest.raises(ResumePlanRejected) as raised:
        await _build(_service(store, caps))

    assert raised.value.code == "CHECKPOINT_ACTIVE_BATCH_INCOMPLETE"
    assert "call-2" in str(raised.value)
    assert caps.reconcile_calls == 0
