from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.contracts.resume import DurableExecutionCheckpoint
from se.src.runtimes.agent.fork_planning import (
    AgentForkPlanningService,
    ForkPlanDeferred,
    ForkPlanRejected,
)


class _Store:
    def __init__(self):
        self.task = SimpleNamespace(
            id="task-1",
            revision=3,
            session_id="session-1",
            created_by="user-1",
            assigned_agent_id="agent-1",
            status="WAITING",
        )
        self.branch = SimpleNamespace(
            branch_id="branch-1",
            task_id="task-1",
            revision=2,
            resolution_state="OPEN",
            current_execution_id="exec-1",
            parent_branch_id=None,
            base_execution_id=None,
            base_checkpoint_id=None,
        )
        self.execution = SimpleNamespace(
            id="exec-1",
            revision=7,
            session_id="session-1",
            agent_id="agent-1",
            task_id="task-1",
            branch_id="branch-1",
            state="WAITING",
            current_checkpoint_id="cp-7",
            correlation_id="corr-1",
            parent_execution_id=None,
            retry_of_execution_id=None,
            base_execution_id=None,
            base_checkpoint_id=None,
        )
        self.checkpoint = DurableExecutionCheckpoint(
            checkpoint_id="cp-7",
            execution_id="exec-1",
            execution_revision=7,
            session_id="session-1",
            task_id="task-1",
            branch_id="branch-1",
            iteration=4,
            wait_reason="RESOURCE",
            transcript_snapshot=(
                {
                    "role": "user",
                    "content": "base",
                    "tool_calls": [],
                    "name": None,
                    "tool_call_id": None,
                    "metadata": {},
                },
            ),
        )
        self.pending = ()
        self.invocations = ()
        self.results = {}
        self.transcript = (
            InferenceMessage(role="user", content="base"),
        )
        self.budget = SimpleNamespace(
            task_id="task-1",
            revision=5,
            state="OPEN",
            policy_fingerprint="p" * 64,
            used_executions=1,
            active_executions=0,
            active_branches=1,
            max_total_executions=8,
            max_active_executions=4,
            max_active_branches=4,
        )
        self.read_calls = []
        self.write_calls = []

    async def load_task(self, task_id):
        self.read_calls.append(("task", task_id))
        return self.task

    async def load_task_branch(self, branch_id):
        self.read_calls.append(("branch", branch_id))
        return self.branch

    async def load_execution(self, execution_id):
        self.read_calls.append(("execution", execution_id))
        return self.execution

    async def load_current_checkpoint(self, execution_id):
        self.read_calls.append(("checkpoint", execution_id))
        return self.checkpoint

    async def load_checkpoint_pending_invocations(self, checkpoint_id):
        self.read_calls.append(("pending", checkpoint_id))
        return self.pending

    async def load_fork_capability_invocations(self, execution_id):
        self.read_calls.append(("invocations", execution_id))
        return self.invocations

    async def load_fork_committed_tool_result(self, execution_id, tool_call_id):
        self.read_calls.append(("tool-result", execution_id, tool_call_id))
        return self.results.get(tool_call_id)

    async def load_fork_safe_checkpoint_transcript(
        self,
        execution_id,
        checkpoint_id,
    ):
        self.read_calls.append(("transcript", execution_id, checkpoint_id))
        return self.transcript

    async def load_task_budget(self, task_id):
        self.read_calls.append(("budget", task_id))
        return self.budget

    def __getattr__(self, name):
        if name.startswith(("save_", "compare_and_set_", "update_", "commit_")):
            async def forbidden(*args, **kwargs):
                self.write_calls.append(name)
                raise AssertionError(f"planner attempted write: {name}")
            return forbidden
        raise AttributeError(name)


def _planner(store=None):
    store = store or _Store()
    return store, AgentForkPlanningService(store)


async def _build(planner, **updates):
    values = {
        "fork_request_id": "fork-request-1",
        "task_id": "task-1",
        "source_branch_id": "branch-1",
        "source_execution_id": "exec-1",
        "source_checkpoint_id": "cp-7",
        "target_user_id": "user-1",
        "overlay_messages": ({"role": "user", "content": "branch-local"},),
    }
    values.update(updates)
    return await planner.build_fork_plan(**values)


def _remote_invocation(**updates):
    values = {
        "invocation_id": "inv-1",
        "revision": 4,
        "capability_id": "tool.echo",
        "capability_version": "1",
        "request_fingerprint": "r" * 64,
        "idempotency": "IDEMPOTENT",
        "state": "COMPLETED",
        "remote_outcome_state": "TERMINAL_COMMITTED",
        "driver_kind": "REMOTE_CLIENT",
        "tool_call_id": "call-1",
        "output": {"ok": True},
        "error": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _committed_result(**updates):
    values = {
        "execution_id": "exec-1",
        "invocation_id": "inv-1",
        "tool_call_id": "call-1",
        "capability_id": "tool.echo",
        "success": True,
        "output": {"ok": True},
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "attempt": 1,
        "commit_state": "COMMITTED",
    }
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_r8_c_valid_plan_is_read_only_and_stable():
    store, planner = _planner()
    first = await _build(planner)
    second = await _build(
        planner,
        fork_request_id="another-request-id",
    )

    assert first.task_id == "task-1"
    assert first.source_branch_id == "branch-1"
    assert first.expected_task_revision == 3
    assert first.expected_branch_revision == 2
    assert first.expected_execution_revision == 7
    assert first.expected_task_budget_revision == 5
    assert first.plan_fingerprint == second.plan_fingerprint
    assert first.overlay_messages[0]["content"] == "branch-local"
    assert store.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda s: setattr(s.task, "created_by", "user-2"), "FORK_FOREIGN_PRINCIPAL"),
        (lambda s: setattr(s.task, "status", "COMPLETED"), "FORK_TASK_STATE_INVALID"),
        (lambda s: setattr(s.branch, "resolution_state", "CANCELLED"), "FORK_BRANCH_NOT_OPEN"),
        (lambda s: setattr(s.branch, "current_execution_id", "exec-old"), "FORK_BRANCH_CURRENT_EXECUTION_CONFLICT"),
        (lambda s: setattr(s.execution, "state", "RUNNING"), "FORK_SOURCE_NOT_WAITING"),
        (lambda s: setattr(s.execution, "branch_id", "branch-other"), "FORK_EXECUTION_LINEAGE_CONFLICT"),
        (lambda s: setattr(s, "checkpoint", replace(s.checkpoint, branch_id="branch-other")), "FORK_CHECKPOINT_LINEAGE_CONFLICT"),
    ],
)
async def test_r8_c_rejects_source_lineage_conflicts(mutation, code):
    store, planner = _planner()
    mutation(store)
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == code
    assert store.write_calls == []


@pytest.mark.asyncio
async def test_r8_c_any_checkpoint_pending_snapshot_rejects_even_terminal():
    store, planner = _planner()
    store.pending = (
        SimpleNamespace(
            observed_remote_outcome_state="TERMINAL_COMMITTED"
        ),
    )
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_PENDING_INVOCATIONS"
    assert ("invocations", "exec-1") not in store.read_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "outcome", "code"),
    [
        ("RUNNING", "IN_FLIGHT", "FORK_SIDE_EFFECT_UNRESOLVED"),
        ("WAITING", "OUTCOME_UNKNOWN", "FORK_SIDE_EFFECT_UNRESOLVED"),
        ("COMPLETED", "NOT_DISPATCHED", "FORK_REMOTE_OUTCOME_UNSAFE"),
        ("COMPLETED", "IN_FLIGHT", "FORK_REMOTE_OUTCOME_UNSAFE"),
        ("COMPLETED", "OUTCOME_UNKNOWN", "FORK_REMOTE_OUTCOME_UNSAFE"),
        ("COMPLETED", None, "FORK_REMOTE_OUTCOME_UNSAFE"),
    ],
)
async def test_r8_c_rejects_unresolved_or_unknown_remote_effects(
    state,
    outcome,
    code,
):
    store, planner = _planner()
    store.invocations = (
        _remote_invocation(state=state, remote_outcome_state=outcome),
    )
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == code
    assert store.write_calls == []


@pytest.mark.asyncio
async def test_r8_c_remote_terminal_effect_requires_matching_committed_projection():
    store, planner = _planner()
    store.invocations = (_remote_invocation(),)

    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_COMMITTED_RESULT_MISSING"

    store.results["call-1"] = _committed_result(
        invocation_id="different",
    )
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_COMMITTED_RESULT_CONFLICT"

    store.results["call-1"] = _committed_result()
    plan = await _build(planner)
    assert len(plan.side_effects) == 1
    assert plan.side_effects[0].remote_outcome_state == "TERMINAL_COMMITTED"


@pytest.mark.asyncio
async def test_r8_c_terminal_invocation_without_tool_projection_rejects():
    store, planner = _planner()
    store.invocations = (_remote_invocation(tool_call_id=None),)
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_SIDE_EFFECT_PROJECTION_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("active_branches", 4, "FORK_BRANCH_CAPACITY_UNAVAILABLE"),
        ("used_executions", 8, "FORK_EXECUTION_CAPACITY_UNAVAILABLE"),
        ("active_executions", 4, "FORK_EXECUTION_CAPACITY_UNAVAILABLE"),
    ],
)
async def test_r8_c_budget_capacity_is_deferred_not_reserved(
    field,
    value,
    code,
):
    store, planner = _planner()
    setattr(store.budget, field, value)
    before = dict(vars(store.budget))

    with pytest.raises(ForkPlanDeferred) as exc:
        await _build(planner)

    assert exc.value.code == code
    assert vars(store.budget) == before
    assert store.write_calls == []


@pytest.mark.asyncio
async def test_r8_c_closed_or_missing_budget_rejects():
    store, planner = _planner()
    store.budget.state = "CLOSED"
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_TASK_BUDGET_CLOSED"

    store.budget = None
    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner)
    assert exc.value.code == "FORK_TASK_BUDGET_REQUIRED"


@pytest.mark.asyncio
async def test_r8_c_overlay_is_validated_and_semantic():
    store, planner = _planner()
    first = await _build(
        planner,
        overlay_messages=({"role": "user", "content": {"b": 2, "a": 1}},),
    )
    second = await _build(
        planner,
        overlay_messages=({"content": {"a": 1, "b": 2}, "role": "user"},),
    )
    assert first.plan_fingerprint == second.plan_fingerprint

    with pytest.raises(ForkPlanRejected) as exc:
        await _build(planner, overlay_messages=({"content": "missing-role"},))
    assert exc.value.code == "FORK_OVERLAY_INVALID"
