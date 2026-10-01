from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.application.user_tool_quota import ToolQuotaAdmission
from se.src.runtimes.agent.contracts.recovery import (
    RecoveryInferenceDisposition,
    recovery_plan_fingerprint,
)
from se.src.runtimes.agent.contracts.resume import (
    ResumeInvocationActionKind,
    ResumeTriggerType,
)
from se.src.runtimes.agent.recovery_planning import (
    AgentRecoveryPlanningService,
    RecoveryPlanDeferred,
    RecoveryPlanRejected,
)
from se.src.runtimes.agent.safe_point_reconstruction import (
    R7CSafePoint,
    SafePointReconstructionError,
    recovery_receipt_payload,
    recovery_safe_point_fingerprint,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)


NOW = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)


class _Agents:
    def __init__(
        self,
        *,
        execution,
        checkpoint,
        session,
        iteration=None,
        pending=(),
        result=None,
        task=None,
        branch=None,
        budget=None,
    ):
        self.execution = execution
        self.checkpoint = checkpoint
        self.session = session
        self.iteration = iteration
        self.pending = tuple(pending)
        self.result = result
        self.task = task
        self.branch = branch
        self.budget = budget

    async def get_execution(self, execution_id):
        return self.execution if execution_id == self.execution.id else None

    async def get_execution_checkpoint(self, checkpoint_id):
        return self.checkpoint if checkpoint_id == self.checkpoint.checkpoint_id else None

    async def get_session(self, session_id):
        return self.session if session_id == self.session.id else None

    async def get_task(self, task_id):
        return self.task if self.task is not None and task_id == self.task.id else None

    async def get_task_branch(self, branch_id):
        return (
            self.branch
            if self.branch is not None and branch_id == self.branch.branch_id
            else None
        )

    async def get_task_budget(self, task_id):
        return (
            self.budget
            if self.budget is not None and task_id == self.budget.task_id
            else None
        )

    async def get_iteration(self, iteration_id):
        return (
            self.iteration
            if self.iteration is not None and iteration_id == self.iteration.id
            else None
        )

    async def list_iterations(self, execution_id):
        if self.iteration is None:
            return []
        return (
            [self.iteration]
            if execution_id == self.iteration.execution_id
            else []
        )

    async def list_checkpoint_pending_invocations(self, checkpoint_id):
        assert checkpoint_id == self.checkpoint.checkpoint_id
        return list(self.pending)

    async def get_tool_result(self, execution_id, tool_call_id):
        if self.result is None:
            return None
        if (
            execution_id == self.result.execution_id
            and tool_call_id == self.result.tool_call_id
        ):
            return self.result
        return None


class _Invocations:
    def __init__(self, records=()):
        self.records = {item.invocation_id: item for item in records}

    async def get_record(self, invocation_id):
        return self.records.get(invocation_id)


class _Uow:
    def __init__(self, agents, invocations):
        self.agents = agents
        self.capability_invocations = invocations
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.commit_calls += 1
        raise AssertionError("R12-F1 planner must not commit")


class _Factory:
    def __init__(self, uow):
        self.uow = uow

    def __call__(self):
        return self.uow


class _Runtime:
    def __init__(self):
        self.resolve_calls = []
        self.reconcile_calls = []
        self.tool_quota_service = None

    def _resolve_continuation_target(self, invocation, *, target_connection_id):
        self.resolve_calls.append((invocation.invocation_id, target_connection_id))
        return object(), SimpleNamespace(implementation_id="client:tool.echo")

    async def reconcile_remote_invocation(self, *args, **kwargs):
        self.reconcile_calls.append((args, kwargs))
        raise AssertionError("read-only R12-F1 must not reconcile/mutate R6")


class _Quota:
    enabled = True

    def __init__(self, admission):
        self.admission = admission
        self.find_calls = []

    async def find_tool_call_authority(self, **kwargs):
        self.find_calls.append(dict(kwargs))
        return self.admission


def _execution(*, iteration=0, task_id=None, branch_id=None):
    return SimpleNamespace(
        id="exec-r12-f1",
        session_id="session-r12-f1",
        agent_id="agent-r12-f1",
        task_id=task_id,
        branch_id=branch_id,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id="corr-r12-f1",
        state="WAITING",
        wait_reason="RECOVERY",
        revision=7,
        owner_instance_id=None,
        lease_expires_at=None,
        lease_generation=4,
        current_checkpoint_id="cp-r12-f1",
    )


def _receipt(execution):
    return recovery_receipt_payload(
        execution_id=execution.id,
        source_revision=execution.revision - 1,
        target_revision=execution.revision,
        checkpoint_id=execution.current_checkpoint_id,
        observed_owner_instance_id="worker-dead",
        observed_lease_generation=execution.lease_generation - 1,
        observed_lease_expires_at=NOW - timedelta(seconds=5),
        takeover_now_utc=NOW,
    )


def _checkpoint(execution, *, iteration=0, frozen_iteration_id=None, active_ids=()):
    receipt = _receipt(execution)
    metadata = {
        "r12_recovery_fingerprint": recovery_safe_point_fingerprint(receipt),
        "r12_recovery_receipt": receipt,
        "r12_recovery_iteration_id": frozen_iteration_id,
        "r12_recovery_active_tool_call_ids": list(active_ids),
    }
    return SimpleNamespace(
        checkpoint_id=execution.current_checkpoint_id,
        execution_id=execution.id,
        execution_revision=execution.revision,
        session_id=execution.session_id,
        task_id=execution.task_id,
        branch_id=execution.branch_id,
        iteration=iteration,
        wait_reason="RECOVERY",
        remaining_active_budget_seconds=30.0,
        wait_expires_at=NOW + timedelta(minutes=5),
        origin_client_id="client-stable",
        origin_connection_id="conn-old",
        metadata_json=metadata,
    )


def _safe_point(*, iteration=0, iteration_id=None, calls=(), pending=()):
    return R7CSafePoint(
        transcript_snapshot=(),
        iteration_number=iteration,
        iteration_id=iteration_id,
        ordered_tool_calls=tuple(calls),
        ordered_pending_invocations=tuple(pending),
        checkpoint_id="cp-r12-f1",
        checkpoint_revision=7,
    )


def _pending(*, outcome="NOT_DISPATCHED"):
    return SimpleNamespace(
        checkpoint_id="cp-r12-f1",
        ordinal=0,
        invocation_id="inv-r12-f1",
        invocation_revision=3,
        tool_call_id="call-r12-f1",
        capability_id="tool.echo",
        capability_version="1.0",
        request_fingerprint="fp-r12-f1",
        idempotency="IDEMPOTENT",
        observed_remote_outcome_state=outcome,
        origin_client_id="client-stable",
        origin_connection_id="conn-old",
    )


def _call():
    return SimpleNamespace(
        execution_id="exec-r12-f1",
        iteration_id="iter-r12-f1",
        tool_call_id="call-r12-f1",
        invocation_id="inv-r12-f1",
        capability_id="tool.echo",
    )


def _invocation(*, outcome="NOT_DISPATCHED", state="WAITING", wait_reason="CONNECTION"):
    return SimpleNamespace(
        invocation_id="inv-r12-f1",
        capability_id="tool.echo",
        capability_version="1.0",
        kind=CapabilityKind.TOOL.value,
        execution_mode=CapabilityExecutionMode.ONE_SHOT.value,
        idempotency=CapabilityIdempotency.IDEMPOTENT.value,
        request_fingerprint="fp-r12-f1",
        owner_user_id="user-r12-f1",
        origin_client_id="client-stable",
        remote_outcome_state=outcome,
        implementation_id="client:old",
        driver_kind="REMOTE_CLIENT",
        state=state,
        wait_reason=wait_reason,
        session_id="session-r12-f1",
        turn_id=None,
        execution_id="exec-r12-f1",
        workflow_id=None,
        tool_call_id="call-r12-f1",
        connection_id="conn-old",
        attempt=1,
        max_attempts=2,
        arguments={"x": 1},
        output={"ok": True} if state == "COMPLETED" else None,
        error=None,
        created_at=NOW - timedelta(minutes=1),
        started_at=NOW - timedelta(seconds=30),
        updated_at=NOW,
        completed_at=NOW if state == "COMPLETED" else None,
        deadline_at=None,
        correlation_id="corr-r12-f1",
        trace_id=None,
        revision=4,
    )


def _service(
    *,
    execution,
    checkpoint,
    safe_point,
    iteration=None,
    pending=(),
    invocation_records=(),
    result=None,
    runtime=None,
    quota=None,
):
    agents = _Agents(
        execution=execution,
        checkpoint=checkpoint,
        session=SimpleNamespace(
            id=execution.session_id,
            owner_user_id="user-r12-f1",
        ),
        iteration=iteration,
        pending=pending,
        result=result,
    )
    uow = _Uow(agents, _Invocations(invocation_records))

    async def reconstruct(_uow, _execution, *, require_pending_invocation_authority):
        assert _uow is uow
        assert _execution is execution
        assert require_pending_invocation_authority is True
        if isinstance(safe_point, Exception):
            raise safe_point
        return safe_point

    runtime = runtime or _Runtime()
    return (
        AgentRecoveryPlanningService(
            _Factory(uow),
            runtime,
            tool_quota_service=quota,
            now_utc=lambda: NOW,
            safe_point_reconstructor=reconstruct,
        ),
        uow,
        runtime,
    )


@pytest.mark.asyncio
async def test_r12_f1_empty_recovery_cut_is_read_only_and_deterministic():
    execution = _execution()
    checkpoint = _checkpoint(execution)
    service, uow, _runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=_safe_point(),
    )

    plan = await service.build_recovery_plan(execution.id)

    assert plan.target_trigger is ResumeTriggerType.SERVER_RECOVERY
    assert plan.resolved_recovery_principal == "user-r12-f1"
    assert plan.recovery_iteration_id is None
    assert plan.iteration_state is None
    assert plan.inference_request_id is None
    assert plan.inference_disposition is RecoveryInferenceDisposition.NO_INFERENCE
    assert plan.ordered_tool_call_ids == ()
    assert plan.invocation_actions == ()
    assert plan.checkpoint_origin_client_id == "client-stable"
    assert plan.checkpoint_origin_connection_id == "conn-old"
    assert plan.target_client_id is None
    assert plan.target_connection_id is None
    assert plan.plan_fingerprint == recovery_plan_fingerprint(plan)
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_uses_exact_frozen_iteration_inference_identity():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="THINKING",
        inference_request_id="inf-frozen",
        inference_request={"messages": []},
        inference_response={"id": "resp-frozen"},
    )
    service, uow, _runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=_safe_point(iteration=1, iteration_id="iter-r12-f1"),
        iteration=iteration,
    )

    plan = await service.build_recovery_plan(execution.id)

    assert plan.recovery_iteration_id == "iter-r12-f1"
    assert plan.iteration_state == "THINKING"
    assert plan.inference_request_id == "inf-frozen"
    assert plan.inference_disposition is RecoveryInferenceDisposition.NO_INFERENCE
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_ambiguous_frozen_inference_defers_without_new_id():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="THINKING",
        inference_request_id="inf-frozen",
        inference_request={"messages": []},
        inference_response=None,
    )
    service, uow, _runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=_safe_point(iteration=1, iteration_id="iter-r12-f1"),
        iteration=iteration,
    )

    with pytest.raises(
        RecoveryPlanDeferred,
        match="RECOVERY_INFERENCE_OUTCOME_AMBIGUOUS",
    ):
        await service.build_recovery_plan(execution.id)

    assert iteration.inference_request_id == "inf-frozen"
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_propagates_unproven_post_recovery_progress_fail_closed():
    execution = _execution()
    checkpoint = _checkpoint(execution)
    service, uow, _runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=SafePointReconstructionError(
            "SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN: late row"
        ),
    )

    with pytest.raises(
        RecoveryPlanRejected,
        match="SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN",
    ):
        await service.build_recovery_plan(execution.id)

    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_uncertain_r6_outcome_defers_without_reconciliation():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
        active_ids=("call-r12-f1",),
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="WAITING_TOOL",
        inference_request_id="inf-done",
        inference_request={"messages": []},
        inference_response={"id": "resp-done"},
    )
    pending = _pending(outcome="OUTCOME_UNKNOWN")
    invocation = _invocation(outcome="OUTCOME_UNKNOWN")
    safe_point = _safe_point(
        iteration=1,
        iteration_id="iter-r12-f1",
        calls=(_call(),),
        pending=(
            {
                "ordinal": 0,
                "invocation_id": pending.invocation_id,
                "tool_call_id": pending.tool_call_id,
                "capability_id": pending.capability_id,
            },
        ),
    )
    runtime = _Runtime()
    service, uow, runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=safe_point,
        iteration=iteration,
        pending=(pending,),
        invocation_records=(invocation,),
        runtime=runtime,
    )

    with pytest.raises(
        RecoveryPlanDeferred,
        match="RECOVERY_RECONCILIATION_REQUIRED",
    ):
        await service.build_recovery_plan(
            execution.id,
            target_connection_id="conn-new",
        )

    assert runtime.reconcile_calls == []
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_not_dispatched_freezes_continuation_and_existing_quota():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
        active_ids=("call-r12-f1",),
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="WAITING_TOOL",
        inference_request_id="inf-done",
        inference_request={"messages": []},
        inference_response={"id": "resp-done"},
    )
    pending = _pending()
    invocation = _invocation()
    safe_point = _safe_point(
        iteration=1,
        iteration_id="iter-r12-f1",
        calls=(_call(),),
        pending=(
            {
                "ordinal": 0,
                "invocation_id": pending.invocation_id,
                "tool_call_id": pending.tool_call_id,
                "capability_id": pending.capability_id,
            },
        ),
    )
    quota = _Quota(
        ToolQuotaAdmission(
            owner_user_id="user-r12-f1",
            invocation_id="inv-r12-f1",
            capability_id="tool.echo",
            request_fingerprint="fp-r12-f1",
            idempotency_key="ubq3:key",
            reservation_id="ubq3:reservation",
            window_epoch=9,
            reservation_state="RESERVED",
            historical_bridge=False,
        )
    )
    runtime = _Runtime()
    runtime.tool_quota_service = quota
    service, uow, runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=safe_point,
        iteration=iteration,
        pending=(pending,),
        invocation_records=(invocation,),
        runtime=runtime,
        quota=quota,
    )

    plan = await service.build_recovery_plan(
        execution.id,
        target_connection_id="conn-new",
    )

    assert len(plan.invocation_actions) == 1
    action = plan.invocation_actions[0]
    assert action.action is ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED
    assert action.continuation_authority.target_connection_id == "conn-new"
    assert action.continuation_authority.target_client_id == "client-stable"
    assert action.origin_client_id == "client-stable"
    assert action.origin_connection_id == "conn-old"
    assert action.kind is CapabilityKind.TOOL
    assert action.execution_mode is CapabilityExecutionMode.ONE_SHOT
    assert action.tool_quota_authority.reservation_state == "RESERVED"
    assert action.tool_quota_authority.reservation_id == "ubq3:reservation"
    assert action.tool_quota_authority.idempotency_key == "ubq3:key"
    assert plan.target_client_id == "client-stable"
    assert plan.target_connection_id == "conn-new"
    assert runtime.resolve_calls == [("inv-r12-f1", "conn-new")]
    assert len(quota.find_calls) == 1
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_r12_f1_terminal_committed_reuses_frozen_watermark_result():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
        active_ids=("call-r12-f1",),
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="WAITING_TOOL",
        inference_request_id="inf-done",
        inference_request={"messages": []},
        inference_response={"id": "resp-done"},
    )
    pending = _pending(outcome="IN_FLIGHT")
    invocation = _invocation(
        outcome="TERMINAL_COMMITTED",
        state="COMPLETED",
        wait_reason=None,
    )
    result = SimpleNamespace(
        execution_id=execution.id,
        tool_call_id=pending.tool_call_id,
        invocation_id=pending.invocation_id,
        capability_id=pending.capability_id,
        commit_state="COMMITTED",
        success=True,
        output={"ok": True},
        error_code=None,
        error_message=None,
        retryable=False,
    )
    safe_point = _safe_point(
        iteration=1,
        iteration_id="iter-r12-f1",
        calls=(_call(),),
        pending=(),
    )
    service, uow, runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=safe_point,
        iteration=iteration,
        pending=(pending,),
        invocation_records=(invocation,),
        result=result,
    )

    plan = await service.build_recovery_plan(execution.id)

    assert len(plan.invocation_actions) == 1
    assert (
        plan.invocation_actions[0].action
        is ResumeInvocationActionKind.REUSE_COMMITTED
    )
    assert plan.invocation_actions[0].continuation_authority is None
    assert plan.invocation_actions[0].tool_quota_authority is None
    assert runtime.resolve_calls == []
    assert uow.commit_calls == 0

@pytest.mark.asyncio
async def test_r12_f1_outcome_unknown_cannot_regress_to_not_dispatched():
    execution = _execution(iteration=1)
    checkpoint = _checkpoint(
        execution,
        iteration=1,
        frozen_iteration_id="iter-r12-f1",
        active_ids=("call-r12-f1",),
    )
    iteration = SimpleNamespace(
        id="iter-r12-f1",
        execution_id=execution.id,
        iteration=1,
        state="WAITING_TOOL",
        inference_request_id="inf-done",
        inference_request={"messages": []},
        inference_response={"id": "resp-done"},
    )
    pending = _pending(outcome="OUTCOME_UNKNOWN")
    invocation = _invocation(outcome="NOT_DISPATCHED")
    safe_point = _safe_point(
        iteration=1,
        iteration_id="iter-r12-f1",
        calls=(_call(),),
        pending=(
            {
                "ordinal": 0,
                "invocation_id": pending.invocation_id,
                "tool_call_id": pending.tool_call_id,
                "capability_id": pending.capability_id,
            },
        ),
    )
    runtime = _Runtime()
    service, uow, runtime = _service(
        execution=execution,
        checkpoint=checkpoint,
        safe_point=safe_point,
        iteration=iteration,
        pending=(pending,),
        invocation_records=(invocation,),
        runtime=runtime,
    )

    with pytest.raises(
        RecoveryPlanRejected,
        match="RECOVERY_REMOTE_OUTCOME_REGRESSION",
    ):
        await service.build_recovery_plan(
            execution.id,
            target_connection_id="conn-new",
        )

    assert runtime.resolve_calls == []
    assert uow.commit_calls == 0
