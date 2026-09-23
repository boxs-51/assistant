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
from se.src.main import execute_retried_agent_task_control_plane
from se.src.runtimes.agent.contracts.retry import (
    RetryAdmission,
    RetryReplayResult,
)
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
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
