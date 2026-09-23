from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import (
    AgentTaskAggregateRequest,
    AgentTaskBranchResolutionRequest,
    AgentTaskRetryRequest,
)
from se.src.main import (
    execute_aggregated_agent_task_control_plane,
    execute_retried_agent_task_control_plane,
)
from se.src.runtimes.agent.contracts.aggregate import AggregateAdmission
from se.src.runtimes.agent.contracts.retry import (
    RetryAdmission,
    RetryReplayResult,
)
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.persistence import AggregateControlError
from se.src.runtimes.agent.task_budget import (
    AggregateAdmissionError,
    BranchResolutionError,
    RetryConsumeConflict,
)
from se.src.transport.gateway.api.v1.multi_agent_router import (
    map_error,
    router,
)


USER = "user-r9-g"
TASK = "task-r9-g"
BRANCH = "branch-r9-g"
SOURCE = "source-r9-g"
EXECUTION = "retry-r9-g"


def _identity() -> Identity:
    return Identity(user_id=USER, auth_type="api_key", scopes={"*"})


def test_r9_g_routes_and_stable_error_envelope_are_public():
    paths = {route.path for route in router.routes}
    assert {
        "/v1/multi-agent/tasks/{task_id}/retry",
        "/v1/multi-agent/tasks/{task_id}/branches/discard",
        "/v1/multi-agent/tasks/{task_id}/branches/adopt",
        "/v1/multi-agent/tasks/{task_id}/aggregate",
    }.issubset(paths)

    for error, code in (
        (
            RetryConsumeConflict(
                "RETRY_REQUEST_CONFLICT", "semantic drift"
            ),
            "RETRY_REQUEST_CONFLICT",
        ),
        (
            BranchResolutionError(
                "BRANCH_DISCARD_LAST_OPEN_FORBIDDEN", "last branch"
            ),
            "BRANCH_DISCARD_LAST_OPEN_FORBIDDEN",
        ),
        (
            AggregateAdmissionError(
                "AGGREGATE_INPUT_CONFLICT", "source changed"
            ),
            "AGGREGATE_INPUT_CONFLICT",
        ),
    ):
        mapped = map_error(error)
        assert mapped.status_code == 409
        assert mapped.detail["code"] == code
        assert mapped.detail["retryable"] is False


def test_r9_g_active_branch_discard_conflict_maps_to_409():
    mapped = map_error(
        BranchResolutionError(
            "BRANCH_EXECUTION_ACTIVE",
            "branch execution still owns runtime authority",
        )
    )
    assert mapped.status_code == 409
    assert mapped.detail == {
        "code": "BRANCH_EXECUTION_ACTIVE",
        "message": "branch execution still owns runtime authority",
        "retryable": False,
    }


def test_r9_g_aggregate_control_conflict_keeps_public_envelope():
    mapped = map_error(
        AggregateControlError(
            "AGGREGATE_ACTIVATION_CONFLICT",
            "another activation won",
            retryable=True,
        )
    )
    assert mapped.status_code == 409
    assert mapped.detail == {
        "code": "AGGREGATE_ACTIVATION_CONFLICT",
        "message": "another activation won",
        "retryable": True,
    }


def test_r9_g_aggregate_structural_input_is_rejected_before_control_plane():
    invalid_payloads = (
        {
            "aggregate_request_id": "aggregate-too-short",
            "target_branch_id": BRANCH,
            "source_branch_ids": [BRANCH],
        },
        {
            "aggregate_request_id": "aggregate-duplicate",
            "target_branch_id": BRANCH,
            "source_branch_ids": [BRANCH, BRANCH],
        },
        {
            "aggregate_request_id": "aggregate-target-missing",
            "target_branch_id": BRANCH,
            "source_branch_ids": ["branch-2", "branch-3"],
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            AgentTaskAggregateRequest(**payload)


def test_r9_g_aggregate_budget_error_keeps_aggregate_taxonomy():
    mapped = map_error(
        AggregateAdmissionError(
            "AGGREGATE_BUDGET_EXCEEDED",
            "aggregate execution capacity exhausted",
        )
    )
    assert mapped.status_code == 409
    assert mapped.detail == {
        "code": "AGGREGATE_BUDGET_EXCEEDED",
        "message": "aggregate execution capacity exhausted",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_r9_g_retry_committed_replay_is_identity_only():
    admission = RetryAdmission(
        task_id=TASK,
        retry_request_id="request-r9-g",
        plan_fingerprint="p" * 64,
        branch_id=BRANCH,
        branch_revision=1,
        source_execution_id=SOURCE,
        source_checkpoint_id=None,
        execution_id=EXECUTION,
        execution_revision=2,
        task_revision=3,
        task_budget_revision=4,
    )

    class Store:
        async def load_retry_replay(self, **values):
            assert values["target_user_id"] == USER
            return RetryReplayResult(admission, "RUNNING", 2)

        async def load_execution(self, execution_id):
            assert execution_id == EXECUTION
            return SimpleNamespace(
                id=EXECUTION,
                agent_id="agent-r9-g",
                state="RUNNING",
                revision=2,
            )

    container = SimpleNamespace(
        agent_durable_store=Store(),
        retry_planning_service=SimpleNamespace(
            build_retry_plan=lambda **_: (_ for _ in ()).throw(
                AssertionError("replay must not replan")
            )
        ),
        task_budget_service=SimpleNamespace(),
        agent_registry=SimpleNamespace(),
        agent_execution_supervisor=SimpleNamespace(),
        agent_runtime=SimpleNamespace(),
    )
    response = await execute_retried_agent_task_control_plane(
        container,
        TASK,
        AgentTaskRetryRequest(
            retry_request_id="request-r9-g",
            branch_id=BRANCH,
            source_execution_id=SOURCE,
        ),
        _identity(),
    )
    assert response == {
        "task_id": TASK,
        "retry_request_id": "request-r9-g",
        "branch_id": BRANCH,
        "execution_id": EXECUTION,
        "execution_state": "RUNNING",
        "execution_revision": 2,
        "started": False,
    }


@pytest.mark.asyncio
async def test_r9_g_aggregate_activated_replay_is_identity_only():
    aggregate_execution = "aggregate-r9-g-replay"
    admission = AggregateAdmission(
        task_id=TASK,
        aggregate_request_id="aggregate-g-replay",
        plan_fingerprint="e" * 64,
        runtime_seed_fingerprint="f" * 64,
        target_branch_id=BRANCH,
        branch_revision=2,
        execution_id=aggregate_execution,
        execution_revision=2,
        source_branch_ids=(BRANCH, "branch-2"),
        task_revision=3,
        task_budget_revision=4,
    )

    class Store:
        async def load_execution(self, execution_id):
            assert execution_id == aggregate_execution
            return SimpleNamespace(
                id=execution_id,
                agent_id="agent-r9-g",
                state="RUNNING",
                revision=2,
            )

        async def prepare_aggregate_execution_context(self, *args, **kwargs):
            raise AssertionError("activated replay must not bootstrap again")

    async def aggregate_branches(*args, **kwargs):
        return admission

    container = SimpleNamespace(
        agent_durable_store=Store(),
        task_budget_service=SimpleNamespace(
            aggregate_branches=aggregate_branches
        ),
        agent_registry=SimpleNamespace(
            get=lambda *_: (_ for _ in ()).throw(
                AssertionError("identity replay must not resolve agent")
            )
        ),
        agent_execution_supervisor=SimpleNamespace(),
        agent_runtime=SimpleNamespace(),
    )
    response = await execute_aggregated_agent_task_control_plane(
        container,
        TASK,
        AgentTaskAggregateRequest(
            aggregate_request_id="aggregate-g-replay",
            target_branch_id=BRANCH,
            source_branch_ids=[BRANCH, "branch-2"],
        ),
        _identity(),
    )
    assert response == {
        "task_id": TASK,
        "aggregate_request_id": "aggregate-g-replay",
        "target_branch_id": BRANCH,
        "source_branch_ids": [BRANCH, "branch-2"],
        "execution_id": aggregate_execution,
        "execution_state": "RUNNING",
        "execution_revision": 2,
        "started": False,
    }


@pytest.mark.asyncio
async def test_r9_g_aggregate_public_command_reports_started_after_handoff():
    aggregate_execution = "aggregate-r9-g"
    admission = AggregateAdmission(
        task_id=TASK,
        aggregate_request_id="aggregate-g-start",
        plan_fingerprint="a" * 64,
        runtime_seed_fingerprint="b" * 64,
        target_branch_id=BRANCH,
        branch_revision=2,
        execution_id=aggregate_execution,
        execution_revision=1,
        source_branch_ids=(BRANCH, "branch-2"),
        task_revision=3,
        task_budget_revision=4,
    )

    class Context:
        def __init__(self):
            self.restored = None

        def restore_active_budget(self, seconds):
            self.restored = seconds

    context = Context()
    bootstrap = SimpleNamespace(
        execution_id=aggregate_execution,
        context=context,
    )

    class Store:
        def __init__(self):
            self.loads = 0

        async def load_execution(self, execution_id):
            assert execution_id == aggregate_execution
            self.loads += 1
            revision = 1 if self.loads == 1 else 2
            return SimpleNamespace(
                id=aggregate_execution,
                agent_id="agent-r9-g",
                state="RUNNING",
                revision=revision,
            )

        async def prepare_aggregate_execution_context(self, *args, **kwargs):
            return bootstrap

        async def activate_aggregate_execution(self, *args, **kwargs):
            return SimpleNamespace(
                activated_execution_revision=2,
                remaining_active_budget_seconds=23.0,
            )

    class CompletedTask:
        def add_done_callback(self, callback):
            self.callback = callback

    class Supervisor:
        async def reserve(self, ctx):
            assert ctx is context
            return "token"

        async def start_reserved(self, token, ctx, runner):
            assert token == "token"
            assert ctx is context
            assert callable(runner)
            return CompletedTask()

        async def release_reserved(self, token):
            raise AssertionError("successful handoff must keep ownership")

    service = SimpleNamespace(
        aggregate_branches=lambda *args, **kwargs: admission
    )

    async def aggregate_branches(*args, **kwargs):
        return admission

    service.aggregate_branches = aggregate_branches
    container = SimpleNamespace(
        agent_durable_store=Store(),
        task_budget_service=service,
        agent_registry=SimpleNamespace(
            get=lambda agent_id: SimpleNamespace(name=agent_id)
        ),
        agent_execution_supervisor=Supervisor(),
        agent_runtime=SimpleNamespace(
            execute=lambda *args, **kwargs: None,
        ),
    )

    response = await execute_aggregated_agent_task_control_plane(
        container,
        TASK,
        AgentTaskAggregateRequest(
            aggregate_request_id="aggregate-g-start",
            target_branch_id=BRANCH,
            source_branch_ids=[BRANCH, "branch-2"],
        ),
        _identity(),
    )
    assert response["execution_id"] == aggregate_execution
    assert response["execution_revision"] == 2
    assert response["started"] is True
    assert context.restored == 23.0


@pytest.mark.asyncio
async def test_r9_g_aggregate_handoff_failure_is_settled_and_released():
    aggregate_execution = "aggregate-r9-g-fail"
    admission = AggregateAdmission(
        task_id=TASK,
        aggregate_request_id="aggregate-g-fail",
        plan_fingerprint="c" * 64,
        runtime_seed_fingerprint="d" * 64,
        target_branch_id=BRANCH,
        branch_revision=2,
        execution_id=aggregate_execution,
        execution_revision=1,
        source_branch_ids=(BRANCH, "branch-2"),
        task_revision=3,
        task_budget_revision=4,
    )

    class Context:
        def restore_active_budget(self, seconds):
            self.remaining = seconds

    context = Context()
    bootstrap = SimpleNamespace(
        execution_id=aggregate_execution,
        context=context,
    )

    class Store:
        async def load_execution(self, execution_id):
            return SimpleNamespace(
                id=execution_id,
                agent_id="agent-r9-g",
                state="RUNNING",
                revision=1,
            )

        async def prepare_aggregate_execution_context(self, *args, **kwargs):
            return bootstrap

        async def activate_aggregate_execution(self, *args, **kwargs):
            return SimpleNamespace(
                activated_execution_revision=2,
                remaining_active_budget_seconds=17.0,
            )

    class Supervisor:
        def __init__(self):
            self.released = []

        async def reserve(self, ctx):
            return "aggregate-token"

        async def start_reserved(self, *args, **kwargs):
            raise RuntimeError("handoff exploded")

        async def release_reserved(self, token):
            self.released.append(token)

    class Runtime:
        def __init__(self):
            self.cancelled = []

        async def cancel_activated_aggregate_execution(
            self,
            ctx,
            revision,
            *,
            error_message,
        ):
            self.cancelled.append((ctx, revision, error_message))

        async def execute(self, *args, **kwargs):
            raise AssertionError("runner must not start")

    async def aggregate_branches(*args, **kwargs):
        return admission

    supervisor = Supervisor()
    runtime = Runtime()
    container = SimpleNamespace(
        agent_durable_store=Store(),
        task_budget_service=SimpleNamespace(
            aggregate_branches=aggregate_branches
        ),
        agent_registry=SimpleNamespace(
            get=lambda agent_id: SimpleNamespace(name=agent_id)
        ),
        agent_execution_supervisor=supervisor,
        agent_runtime=runtime,
    )

    with pytest.raises(RuntimeError, match="handoff exploded"):
        await execute_aggregated_agent_task_control_plane(
            container,
            TASK,
            AgentTaskAggregateRequest(
                aggregate_request_id="aggregate-g-fail",
                target_branch_id=BRANCH,
                source_branch_ids=[BRANCH, "branch-2"],
            ),
            _identity(),
        )

    assert supervisor.released == ["aggregate-token"]
    assert len(runtime.cancelled) == 1
    assert runtime.cancelled[0][0] is context
    assert runtime.cancelled[0][1] == 2
    assert "AGGREGATE_RUNTIME_HANDOFF_FAILED" in runtime.cancelled[0][2]


@pytest.mark.asyncio
async def test_r9_g_coordinator_dispatches_explicit_commands():
    task_record = SimpleNamespace(
        id=TASK,
        created_by=USER,
        session_id="session-r9-g",
        assigned_agent_id="agent-r9-g",
        revision=0,
        parent_task_id=None,
        connection_id=None,
        client_id=None,
        status="RUNNING",
        wait_reasons=[],
        input={},
        output=None,
        error=None,
        created_at=None,
        updated_at=None,
    )

    class Store:
        async def load_task(self, task_id):
            assert task_id == TASK
            return task_record

    registry = SimpleNamespace(get=lambda _agent_id: None)
    coordinator = MultiAgentCoordinator(
        registry,
        durable_store=Store(),
        retry_executor=lambda *_: {
            "task_id": TASK,
            "retry_request_id": "retry-g",
            "branch_id": BRANCH,
            "execution_id": EXECUTION,
            "execution_state": "RUNNING",
            "execution_revision": 2,
            "started": True,
        },
        discard_executor=lambda *_: {
            "task_id": TASK,
            "branch_id": BRANCH,
            "resolution_state": "DISCARDED",
            "task_status": "RUNNING",
        },
        adopt_executor=lambda *_: {
            "task_id": TASK,
            "branch_id": BRANCH,
            "resolution_state": "ADOPTED",
            "task_status": "COMPLETED",
            "execution_id": EXECUTION,
        },
        aggregate_executor=lambda *_: {
            "task_id": TASK,
            "aggregate_request_id": "aggregate-g",
            "target_branch_id": BRANCH,
            "source_branch_ids": [BRANCH, "branch-2"],
            "execution_id": "aggregate-exec",
            "execution_state": "RUNNING",
            "execution_revision": 1,
            "started": False,
        },
    )
    retry = await coordinator.retry_task(
        TASK,
        AgentTaskRetryRequest(
            retry_request_id="retry-g",
            branch_id=BRANCH,
            source_execution_id=SOURCE,
        ),
        _identity(),
    )
    discard = await coordinator.discard_task_branch(
        TASK, AgentTaskBranchResolutionRequest(branch_id=BRANCH), _identity()
    )
    adopt = await coordinator.adopt_task_branch(
        TASK, AgentTaskBranchResolutionRequest(branch_id=BRANCH), _identity()
    )
    aggregate = await coordinator.aggregate_task_branches(
        TASK,
        AgentTaskAggregateRequest(
            aggregate_request_id="aggregate-g",
            target_branch_id=BRANCH,
            source_branch_ids=[BRANCH, "branch-2"],
        ),
        _identity(),
    )
    assert retry.started is True
    assert discard.resolution_state == "DISCARDED"
    assert adopt.task_status == "COMPLETED"
    assert aggregate.execution_id == "aggregate-exec"
