from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.context_builder import AgentContextSnapshot
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeResult,
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
    resume_plan_fingerprint,
)
from se.src.runtimes.agent.contracts.tool import ToolExecutionResult
from se.src.runtimes.agent.resume_claim import ResumeActivationError
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)


EXECUTION = "exec-r7f4"
SESSION = "session-r7f4"
AGENT = "agent-r7f4"
USER = "user-r7f4"
CLIENT = "client-r7f4"
K2 = "conn-r7f4-k2"
CHECKPOINT = "cp-r7f4"


def _identity() -> Identity:
    return Identity(
        user_id=USER,
        session_id=SESSION,
        auth_type="api_key",
        scopes={"*"},
    )


def _action(
    ordinal: int,
    kind: ResumeInvocationActionKind,
) -> ResumeInvocationAction:
    suffix = ordinal + 1
    outcome = {
        ResumeInvocationActionKind.REUSE_COMMITTED:
            RemoteOutcomeState.TERMINAL_COMMITTED,
        ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED:
            RemoteOutcomeState.NOT_DISPATCHED,
        ResumeInvocationActionKind.REPLAY_SAFE:
            RemoteOutcomeState.OUTCOME_UNKNOWN,
    }[kind]
    return ResumeInvocationAction(
        invocation_id=f"inv-{suffix}",
        tool_call_id=f"call-{suffix}",
        ordinal=ordinal,
        capability_id=f"tool.r7f4.{suffix}",
        capability_version="1.0",
        request_fingerprint=(str(suffix) * 64)[:64],
        idempotency=(
            CapabilityIdempotency.NON_IDEMPOTENT
            if kind is ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED
            else CapabilityIdempotency.IDEMPOTENT
        ),
        expected_invocation_revision=10 + ordinal,
        expected_invocation_state=(
            CapabilityInvocationState.COMPLETED
            if kind is ResumeInvocationActionKind.REUSE_COMMITTED
            else CapabilityInvocationState.WAITING
        ),
        expected_remote_outcome_state=outcome,
        action=kind,
    )


def _plan() -> ResumePlan:
    actions = (
        _action(0, ResumeInvocationActionKind.REUSE_COMMITTED),
        _action(1, ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED),
        _action(2, ResumeInvocationActionKind.REPLAY_SAFE),
    )
    plan = ResumePlan(
        execution_id=EXECUTION,
        checkpoint_id=CHECKPOINT,
        expected_execution_revision=2,
        plan_fingerprint="",
        agent_id=AGENT,
        session_id=SESSION,
        task_id=None,
        branch_id=None,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id="corr-r7f4",
        trace_id="trace-r7f4",
        request_id="request-r7f4",
        iteration=1,
        ordered_tool_call_ids=tuple(item.tool_call_id for item in actions),
        transcript_snapshot=(
            InferenceMessage(role="assistant", content="resume-prefix"),
        ),
        remaining_active_budget_seconds=20.0,
        wait_expires_at=None,
        target_user_id=USER,
        target_client_id=CLIENT,
        target_connection_id=K2,
        invocation_actions=actions,
    )
    return replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))


def _context(plan: ResumePlan) -> AgentExecutionContext:
    context = AgentExecutionContext.create(
        execution_id=plan.execution_id,
        agent_id=plan.agent_id,
        session_id=plan.session_id,
        correlation_id=plan.correlation_id,
        identity=_identity(),
        limits=AgentExecutionLimits(),
        request_id=plan.request_id,
        connection_id=plan.target_connection_id,
        remaining_active_budget_seconds=plan.remaining_active_budget_seconds,
        activate_budget=False,
    )
    context.iteration = plan.iteration
    context.resume_revision = plan.expected_execution_revision
    context.resume_transcript = [
        item.model_dump(mode="json") for item in plan.transcript_snapshot
    ]
    return context


def _consumed(plan: ResumePlan) -> ResumeClaimConsumeResult:
    return ResumeClaimConsumeResult(
        claim_id="claim-r7f4",
        resume_request_id="resume-request-r7f4",
        execution_id=plan.execution_id,
        checkpoint_id=plan.checkpoint_id,
        source_execution_revision=plan.expected_execution_revision,
        consumed_execution_revision=plan.expected_execution_revision + 1,
        remaining_active_budget_seconds=plan.remaining_active_budget_seconds,
        bound_client_id=plan.target_client_id,
        bound_connection_id=plan.target_connection_id,
    )


class _ContinuationExecutor:
    def __init__(self, *, provisional: bool = False) -> None:
        self.provisional = provisional
        self.continued: list[ResumeInvocationAction] = []
        self.ordinary_execute_calls = 0

    async def execute(self, context, request):
        self.ordinary_execute_calls += 1
        raise AssertionError("R7-F4 must not enter ordinary execute().")

    async def continue_invocation(self, context, action):
        self.continued.append(action)
        # Complete in reverse-ish wall-clock order to prove coordinator/order
        # independence from network completion.
        await asyncio.sleep(0.002 if action.ordinal == 1 else 0)
        return ToolExecutionResult(
            execution_id=context.execution_id,
            iteration=context.iteration,
            invocation_id=action.invocation_id,
            tool_call_id=action.tool_call_id,
            capability_id=action.capability_id,
            success=not self.provisional,
            output=(
                {"ordinal": action.ordinal}
                if not self.provisional
                else None
            ),
            error_code=(
                None if not self.provisional else "REMOTE_OUTCOME_UNKNOWN"
            ),
            error_message=(
                None if not self.provisional else "connection lost"
            ),
            retryable=self.provisional,
            metadata={
                "attempt": 2,
                "r7_resume_action": action.action.value,
                "force_provisional": self.provisional,
            },
        )


class _Store:
    def __init__(self, plan: ResumePlan) -> None:
        self.plan = plan
        self.execution = SimpleNamespace(
            id=plan.execution_id,
            state="RUNNING",
            revision=plan.expected_execution_revision + 1,
            current_checkpoint_id=plan.checkpoint_id,
            bound_client_id=plan.target_client_id,
            bound_connection_id=plan.target_connection_id,
            session_id=plan.session_id,
            agent_id=plan.agent_id,
            task_id=plan.task_id,
            branch_id=plan.branch_id,
            parent_execution_id=plan.parent_execution_id,
            retry_of_execution_id=plan.retry_of_execution_id,
            base_execution_id=plan.base_execution_id,
            base_checkpoint_id=plan.base_checkpoint_id,
            correlation_id=plan.correlation_id,
            remaining_active_budget_seconds=plan.remaining_active_budget_seconds,
        )
        self.results: dict[str, SimpleNamespace] = {}
        self.iterations: dict[int, SimpleNamespace] = {}
        self.persisted_transcripts: list[list[dict]] = []

        reuse = plan.invocation_actions[0]
        self.results[reuse.tool_call_id] = SimpleNamespace(
            execution_id=plan.execution_id,
            iteration_id=f"{plan.execution_id}:iteration:{plan.iteration}",
            invocation_id=reuse.invocation_id,
            tool_call_id=reuse.tool_call_id,
            capability_id=reuse.capability_id,
            success=True,
            output={"ordinal": reuse.ordinal},
            error_code=None,
            error_message=None,
            retryable=False,
            extra_metadata={"attempt": 1},
            commit_state="COMMITTED",
            attempt=1,
        )

    async def load_execution(self, execution_id):
        assert execution_id == self.plan.execution_id
        return self.execution

    async def load_committed_tool_result(self, execution_id, tool_call_id):
        item = self.results.get(tool_call_id)
        if item is None or item.commit_state != "COMMITTED":
            return None
        return item

    async def save_tool_result(self, values):
        commit_state = (
            "PROVISIONAL"
            if values["extra_metadata"].get("force_provisional")
            else "COMMITTED"
        )
        record = SimpleNamespace(
            **values,
            commit_state=commit_state,
        )
        self.results[values["tool_call_id"]] = record
        return record

    async def update_checkpoint(self, execution_id, values):
        assert execution_id == self.plan.execution_id
        self.persisted_transcripts.append(list(values.get("transcript") or []))
        return self.execution

    async def load_iteration(self, execution_id, *, iteration_number=None, **kwargs):
        return self.iterations.get(iteration_number)

    async def save_iteration(self, values):
        record = SimpleNamespace(**values)
        self.iterations[values["iteration"]] = record
        return record

    async def update_iteration(self, iteration_id, values):
        number = int(iteration_id.rsplit(":", 1)[-1])
        record = self.iterations[number]
        for key, value in values.items():
            setattr(record, key, value)
        return record

    async def compare_and_set_execution(self, execution_id, expected_revision, values):
        assert execution_id == self.plan.execution_id
        assert self.execution.revision == expected_revision
        for key, value in values.items():
            setattr(self.execution, key, value)
        self.execution.revision = expected_revision + 1
        return self.execution


class _ContextBuilder:
    def __init__(self) -> None:
        self.last_messages = ()

    async def build(self, context, request):
        messages = tuple(
            InferenceMessage.model_validate(item)
            for item in request.prior_messages
        )
        self.last_messages = messages
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=messages,
            tools=(),
            metadata={},
        )


class _Inference:
    async def complete(self, request):
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                content="resumed-final",
            ),
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="test",
            model="test",
        )


class _Policy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


@pytest.mark.asyncio
async def test_r7_f4_continuation_coordinator_preserves_action_order():
    plan = _plan()
    context = _context(plan)
    context.restore_active_budget(plan.remaining_active_budget_seconds)
    context.tool_calls_used = 3
    executor = _ContinuationExecutor()
    coordinator = AgentToolExecutionCoordinator(executor)

    actions = plan.invocation_actions[1:]
    results = await coordinator.continue_invocations(
        context,
        actions,
        max_parallel=2,
    )

    assert [item.tool_call_id for item in results] == [
        action.tool_call_id for action in actions
    ]
    assert {item.action for item in executor.continued} == {
        ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
        ResumeInvocationActionKind.REPLAY_SAFE,
    }
    assert executor.ordinary_execute_calls == 0
    assert context.tool_calls_used == 3


@pytest.mark.asyncio
async def test_r7_f4_execute_claimed_resume_reuses_committed_continues_and_orders_model_context():
    plan = _plan()
    context = _context(plan)
    consumed = _consumed(plan)
    executor = _ContinuationExecutor()
    coordinator = AgentToolExecutionCoordinator(executor)
    store = _Store(plan)
    builder = _ContextBuilder()
    runtime = AgentRuntime(
        context_builder=builder,
        inference=_Inference(),
        tool_execution=coordinator,
        execution_policy=_Policy(),
        durable_store=store,
    )

    result = await runtime.execute_claimed_resume(
        context,
        plan=plan,
        consumed=consumed,
    )

    assert result.output == "resumed-final"
    assert [item.tool_call_id for item in result.last_tool_results] == list(
        plan.ordered_tool_call_ids
    )
    tool_messages = [
        item for item in builder.last_messages if item.role == "tool"
    ]
    assert [item.tool_call_id for item in tool_messages] == list(
        plan.ordered_tool_call_ids
    )
    assert [item.content for item in tool_messages] == [
        {"ordinal": 0},
        {"ordinal": 1},
        {"ordinal": 2},
    ]
    assert {
        item.action for item in executor.continued
    } == {
        ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
        ResumeInvocationActionKind.REPLAY_SAFE,
    }
    assert executor.ordinary_execute_calls == 0
    assert store.execution.state == "COMPLETED"
    assert store.execution.revision == 4


@pytest.mark.asyncio
async def test_r7_f4_provisional_continuation_never_enters_model_context_or_terminalizes_execution():
    plan = _plan()
    # Keep only one executable action so the failure surface is unambiguous.
    action = _action(0, ResumeInvocationActionKind.REPLAY_SAFE)
    plan = replace(
        plan,
        ordered_tool_call_ids=(action.tool_call_id,),
        invocation_actions=(action,),
        plan_fingerprint="",
    )
    plan = replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))
    context = _context(plan)
    consumed = _consumed(plan)
    executor = _ContinuationExecutor(provisional=True)
    coordinator = AgentToolExecutionCoordinator(executor)
    store = _Store(plan)
    store.results = {}
    builder = _ContextBuilder()
    runtime = AgentRuntime(
        context_builder=builder,
        inference=_Inference(),
        tool_execution=coordinator,
        execution_policy=_Policy(),
        durable_store=store,
    )

    with pytest.raises(ResumeActivationError) as raised:
        await runtime.execute_claimed_resume(
            context,
            plan=plan,
            consumed=consumed,
        )

    assert raised.value.code == "RESUME_ACTION_NOT_COMMITTED"
    assert builder.last_messages == ()
    assert store.execution.state == "RUNNING"
    assert store.execution.revision == 3
    assert executor.ordinary_execute_calls == 0


@pytest.mark.asyncio
async def test_r7_f4_canonical_committed_slot_without_pending_action_is_reused():
    base = _plan()
    # call-1 is already committed before the checkpoint pending set is frozen.
    # R7-D therefore needs actions only for call-2/call-3 while canonical
    # reconstruction must still emit all three slots in original order.
    plan = replace(
        base,
        invocation_actions=base.invocation_actions[1:],
        plan_fingerprint="",
    )
    plan = replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))
    context = _context(plan)
    consumed = _consumed(plan)
    executor = _ContinuationExecutor()
    coordinator = AgentToolExecutionCoordinator(executor)
    store = _Store(base)
    store.plan = plan
    builder = _ContextBuilder()
    runtime = AgentRuntime(
        context_builder=builder,
        inference=_Inference(),
        tool_execution=coordinator,
        execution_policy=_Policy(),
        durable_store=store,
    )

    result = await runtime.execute_claimed_resume(
        context,
        plan=plan,
        consumed=consumed,
    )

    assert [item.tool_call_id for item in result.last_tool_results] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    tool_messages = [
        item for item in builder.last_messages if item.role == "tool"
    ]
    assert [item.tool_call_id for item in tool_messages] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert {item.tool_call_id for item in executor.continued} == {
        "call-2",
        "call-3",
    }
    assert executor.ordinary_execute_calls == 0
