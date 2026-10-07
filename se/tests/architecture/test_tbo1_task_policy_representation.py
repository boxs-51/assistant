from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import (
    AgentTask,
    AgentTaskCreateRequest,
    TaskMode,
)
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.transport.gateway.api.v1.multi_agent_router import router


def identity(user_id="user-1"):
    return Identity(
        user_id=user_id,
        organization_id="org-1",
        auth_type="api_key",
    )


def registry_with_worker():
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name="worker",
            goal="Work",
            instruction="Complete the assigned task.",
        )
    )
    return registry


class RecordingTaskStore:
    def __init__(self):
        self.saved = None

    async def save_task(self, values):
        self.saved = dict(values)


def test_tbo1_legacy_create_request_defaults_to_finite_without_horizons():
    request = AgentTaskCreateRequest(
        session_id="session-1",
        assigned_agent_id="worker",
    )

    assert request.task_mode is TaskMode.FINITE
    assert request.task_horizon_at is None
    assert request.review_horizon_at is None


@pytest.mark.parametrize("mode", [TaskMode.FINITE, TaskMode.RECURRING])
def test_tbo1_create_request_accepts_bounded_representation(mode):
    request = AgentTaskCreateRequest(
        session_id="session-1",
        assigned_agent_id="worker",
        task_mode=mode,
        task_horizon_at=100.5,
        review_horizon_at=50.25,
    )

    assert request.task_mode is mode
    assert request.task_horizon_at == 100.5
    assert request.review_horizon_at == 50.25


@pytest.mark.parametrize(
    "field",
    ["task_horizon_at", "review_horizon_at"],
)
def test_tbo1_negative_horizon_is_rejected_by_schema(field):
    values = {
        "session_id": "session-1",
        "assigned_agent_id": "worker",
        field: -0.1,
    }

    with pytest.raises(ValidationError):
        AgentTaskCreateRequest(**values)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["task_horizon_at", "review_horizon_at"])
def test_tbo1_non_finite_horizon_is_rejected_before_persistence(field, value):
    create_values = {
        "session_id": "session-1",
        "assigned_agent_id": "worker",
        field: value,
    }
    with pytest.raises(ValidationError):
        AgentTaskCreateRequest(**create_values)

    task_values = {
        "task_id": "task-1",
        "session_id": "session-1",
        "created_by": "user-1",
        "assigned_agent_id": "worker",
        "created_at": 1.0,
        "updated_at": 1.0,
        field: value,
    }
    with pytest.raises(ValidationError):
        AgentTask(**task_values)


@pytest.mark.asyncio
async def test_tbo1_coordinator_persists_and_materializes_policy_representation():
    store = RecordingTaskStore()
    coordinator = MultiAgentCoordinator(
        registry_with_worker(),
        durable_store=store,
    )
    session = coordinator.create_session(identity(), ["worker"])

    task = await coordinator.create_task_async(
        session_id=session.session_id,
        assigned_agent_id="worker",
        task_input={"query": "repeat"},
        identity=identity(),
        task_mode=TaskMode.RECURRING,
        task_horizon_at=500.0,
        review_horizon_at=250.0,
    )

    assert task.task_mode is TaskMode.RECURRING
    assert task.task_horizon_at == 500.0
    assert task.review_horizon_at == 250.0
    assert store.saved["task_mode"] == "RECURRING"
    assert store.saved["task_horizon_at"] == 500.0
    assert store.saved["review_horizon_at"] == 250.0

    record = SimpleNamespace(
        **store.saved,
        created_at=task.created_at,
        updated_at=task.updated_at,
        output=None,
        error=None,
    )
    materialized = MultiAgentCoordinator._task_from_record(record)

    assert materialized.task_mode is TaskMode.RECURRING
    assert materialized.task_horizon_at == 500.0
    assert materialized.review_horizon_at == 250.0


def test_tbo1_task_policy_does_not_bypass_existing_session_ownership():
    coordinator = MultiAgentCoordinator(registry_with_worker())
    session = coordinator.create_session(identity(), ["worker"])

    with pytest.raises(LookupError):
        coordinator.create_task(
            session.session_id,
            assigned_agent_id="worker",
            task_input={},
            identity=identity("other-user"),
            task_mode=TaskMode.RECURRING,
        )


def test_tbo1_adds_no_task_policy_mutation_endpoint():
    paths = {
        route.path
        for route in router.routes
        if hasattr(route, "path")
    }

    assert "/v1/multi-agent/tasks/{task_id}/policy" not in paths
    assert "/v1/multi-agent/tasks/{task_id}/renew" not in paths
