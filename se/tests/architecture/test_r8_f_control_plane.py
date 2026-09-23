from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskForkRequest
from se.src.main import execute_forked_agent_task_control_plane
from se.src.runtimes.agent.contracts.fork import (
    ForkActivationResult,
    ForkAdmission,
    ForkExecutionBootstrap,
    ForkReplayResult,
)
from se.src.runtimes.agent.persistence import ForkControlError


TASK = "task-r8-f-control"
BRANCH = "branch-r8-f-control"
EXECUTION = "exec-r8-f-control"
SOURCE_BRANCH = "source-branch-r8-f-control"
SOURCE_EXECUTION = "source-exec-r8-f-control"
SOURCE_CHECKPOINT = "source-cp-r8-f-control"
REQUEST = "fork-request-r8-f-control"
USER = "user-r8-f-control"
AGENT = "agent-r8-f-control"


def _identity(user_id: str = USER) -> Identity:
    return Identity(
        user_id=user_id,
        auth_type="api_key",
        scopes={"*"},
    )


def _request() -> AgentTaskForkRequest:
    return AgentTaskForkRequest(
        fork_request_id=REQUEST,
        source_branch_id=SOURCE_BRANCH,
        source_execution_id=SOURCE_EXECUTION,
        source_checkpoint_id=SOURCE_CHECKPOINT,
        overlay_messages=[
            {"role": "user", "content": "fork-local"}
        ],
    )


def _admission() -> ForkAdmission:
    return ForkAdmission(
        task_id=TASK,
        fork_request_id=REQUEST,
        plan_fingerprint="p" * 64,
        branch_id=BRANCH,
        branch_revision=0,
        execution_id=EXECUTION,
        execution_revision=1,
        task_revision=2,
        task_budget_revision=3,
    )


class _Context:
    def __init__(self):
        self.restored = None

    def restore_active_budget(self, remaining):
        self.restored = remaining
        return remaining


class _Store:
    def __init__(
        self,
        *,
        current_state="RUNNING",
        current_revision=1,
        activation_error=None,
        latest_after_error=None,
    ):
        self.admission = _admission()
        self.context = _Context()
        self.current = SimpleNamespace(
            id=EXECUTION,
            agent_id=AGENT,
            state=current_state,
            revision=current_revision,
        )
        self.activation_error = activation_error
        self.latest_after_error = latest_after_error
        self.replay_calls = 0
        self.prepare_calls = 0
        self.activation_calls = 0
        self.load_calls = 0

    async def load_fork_replay(self, **kwargs):
        self.replay_calls += 1
        assert kwargs["task_id"] == TASK
        assert kwargs["fork_request_id"] == REQUEST
        assert kwargs["target_user_id"] == USER
        return ForkReplayResult(
            admission=self.admission,
            execution_state=str(self.current.state),
            execution_revision=int(self.current.revision),
        )

    async def load_execution(self, execution_id):
        assert execution_id == EXECUTION
        self.load_calls += 1
        if self.activation_error is not None and self.load_calls > 1:
            if self.latest_after_error is not None:
                return self.latest_after_error
        return self.current

    async def prepare_fork_execution_context(
        self,
        execution_id,
        *,
        identity,
        agent,
    ):
        self.prepare_calls += 1
        assert execution_id == EXECUTION
        assert identity.user_id == USER
        assert agent.name == AGENT
        return ForkExecutionBootstrap(
            execution_id=EXECUTION,
            expected_execution_revision=1,
            task_id=TASK,
            branch_id=BRANCH,
            fork_request_id=REQUEST,
            plan_fingerprint="p" * 64,
            runtime_seed_fingerprint="s" * 64,
            branch_context_revision=0,
            context=self.context,
        )

    async def activate_fork_execution(self, bootstrap, *, identity):
        self.activation_calls += 1
        assert bootstrap.execution_id == EXECUTION
        assert identity.user_id == USER
        if self.activation_error is not None:
            raise self.activation_error
        self.current = SimpleNamespace(
            id=EXECUTION,
            agent_id=AGENT,
            state="RUNNING",
            revision=2,
        )
        return ForkActivationResult(
            task_id=TASK,
            branch_id=BRANCH,
            execution_id=EXECUTION,
            source_execution_revision=1,
            activated_execution_revision=2,
            remaining_active_budget_seconds=25.0,
        )


class _Planner:
    def __init__(self):
        self.calls = 0

    async def build_fork_plan(self, **kwargs):
        self.calls += 1
        raise AssertionError(
            "committed replay must not rebuild ForkPlan"
        )


class _BudgetService:
    def __init__(self):
        self.consume_calls = 0

    async def consume_fork_plan(self, plan):
        self.consume_calls += 1
        raise AssertionError(
            "committed replay must not consume a second ForkPlan"
        )


class _Supervisor:
    def __init__(self):
        self.reserve_calls = 0
        self.release_calls = 0
        self.start_calls = 0
        self.reserved = False

    async def reserve(self, context):
        assert not self.reserved
        self.reserve_calls += 1
        self.reserved = True
        return SimpleNamespace(execution_id=EXECUTION, token="token")

    async def release_reserved(self, token):
        self.release_calls += 1
        was_reserved = self.reserved
        self.reserved = False
        return was_reserved

    async def start_reserved(self, token, context, runner):
        assert self.reserved
        self.start_calls += 1
        self.reserved = False
        task = asyncio.create_task(runner())
        await asyncio.sleep(0)
        return task


class _Runtime:
    def __init__(self):
        self.execute_calls = 0
        self.cancel_calls = 0

    async def execute(self, context, *, durable_revision):
        self.execute_calls += 1
        assert durable_revision == 2
        assert context.restored == 25.0
        return SimpleNamespace(state="COMPLETED")

    async def cancel_activated_fork_execution(
        self,
        context,
        revision,
        *,
        error_message,
    ):
        self.cancel_calls += 1
        assert revision == 2
        assert error_message


def _container(store):
    planner = _Planner()
    budget = _BudgetService()
    supervisor = _Supervisor()
    runtime = _Runtime()
    container = SimpleNamespace(
        agent_durable_store=store,
        fork_planning_service=planner,
        task_budget_service=budget,
        agent_execution_supervisor=supervisor,
        agent_runtime=runtime,
        agent_registry=SimpleNamespace(
            get=lambda agent_id: (
                SimpleNamespace(name=AGENT)
                if agent_id == AGENT
                else None
            )
        ),
    )
    return container, planner, budget, supervisor, runtime


@pytest.mark.asyncio
async def test_r8_f_restart_replay_preactivation_starts_same_execution_once():
    store = _Store()
    container, planner, budget, supervisor, runtime = _container(store)

    result = await execute_forked_agent_task_control_plane(
        container,
        TASK,
        _request(),
        _identity(),
    )
    await asyncio.sleep(0)

    assert result["branch_id"] == BRANCH
    assert result["execution_id"] == EXECUTION
    assert result["execution_revision"] == 2
    assert result["started"] is True
    assert store.replay_calls == 1
    assert store.prepare_calls == 1
    assert store.activation_calls == 1
    assert planner.calls == 0
    assert budget.consume_calls == 0
    assert supervisor.reserve_calls == 1
    assert supervisor.start_calls == 1
    assert supervisor.release_calls == 0
    assert supervisor.reserved is False
    assert runtime.execute_calls == 1
    assert runtime.cancel_calls == 0


@pytest.mark.asyncio
async def test_r8_f_replay_after_activation_is_identity_only():
    store = _Store(current_state="RUNNING", current_revision=2)
    container, planner, budget, supervisor, runtime = _container(store)

    result = await execute_forked_agent_task_control_plane(
        container,
        TASK,
        _request(),
        _identity(),
    )

    assert result["execution_id"] == EXECUTION
    assert result["execution_revision"] == 2
    assert result["started"] is False
    assert store.prepare_calls == 0
    assert store.activation_calls == 0
    assert planner.calls == 0
    assert budget.consume_calls == 0
    assert supervisor.reserve_calls == 0
    assert supervisor.start_calls == 0
    assert runtime.execute_calls == 0


@pytest.mark.asyncio
async def test_r8_f_preactivation_rejection_releases_local_reservation():
    error = ForkControlError(
        "FORK_BRANCH_CONTEXT_CHANGED",
        "changed before activation",
    )
    store = _Store(activation_error=error)
    container, _planner, _budget, supervisor, runtime = _container(store)

    with pytest.raises(ForkControlError) as exc:
        await execute_forked_agent_task_control_plane(
            container,
            TASK,
            _request(),
            _identity(),
        )

    assert exc.value.code == "FORK_BRANCH_CONTEXT_CHANGED"
    assert supervisor.reserve_calls == 1
    assert supervisor.release_calls == 1
    assert supervisor.start_calls == 0
    assert supervisor.reserved is False
    assert runtime.execute_calls == 0
    assert runtime.cancel_calls == 0

    # A later local attempt is not blocked by leaked process ownership.
    token = await supervisor.reserve(store.context)
    assert token.execution_id == EXECUTION
    assert supervisor.reserved is True


@pytest.mark.asyncio
async def test_r8_f_activation_loser_replays_durable_winner_without_cleanup():
    error = ForkControlError(
        "FORK_ACTIVATION_CONFLICT",
        "activation CAS lost",
        retryable=True,
    )
    latest = SimpleNamespace(
        id=EXECUTION,
        agent_id=AGENT,
        state="RUNNING",
        revision=2,
    )
    store = _Store(
        activation_error=error,
        latest_after_error=latest,
    )
    container, _planner, _budget, supervisor, runtime = _container(store)

    result = await execute_forked_agent_task_control_plane(
        container,
        TASK,
        _request(),
        _identity(),
    )

    assert result["execution_id"] == EXECUTION
    assert result["execution_revision"] == 2
    assert result["started"] is False
    assert supervisor.release_calls == 1
    assert supervisor.start_calls == 0
    assert runtime.execute_calls == 0
    assert runtime.cancel_calls == 0
