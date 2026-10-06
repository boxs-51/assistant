from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskCreateRequest, TaskMode
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.transport.gateway.api.v1.multi_agent_router import create_agent_task


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


class RecordingCoordinator:
    def __init__(self):
        self.kwargs = None

    async def create_task_async(self, **kwargs):
        self.kwargs = dict(kwargs)
        return self.kwargs


@pytest.mark.asyncio
async def test_tbo1_create_api_forwards_explicit_policy_representation():
    coordinator = RecordingCoordinator()
    body = AgentTaskCreateRequest(
        session_id="session-1",
        assigned_agent_id="worker",
        input={"query": "repeat"},
        task_mode=TaskMode.RECURRING,
        task_horizon_at=500.0,
        review_horizon_at=250.0,
    )

    result = await create_agent_task(
        body,
        coordinator=coordinator,
        identity=identity(),
        container=SimpleNamespace(),
    )

    assert result["task_mode"] is TaskMode.RECURRING
    assert result["task_horizon_at"] == 500.0
    assert result["review_horizon_at"] == 250.0


@pytest.mark.asyncio
async def test_tbo1_create_api_preserves_legacy_defaults():
    coordinator = RecordingCoordinator()
    body = AgentTaskCreateRequest(
        session_id="session-1",
        assigned_agent_id="worker",
    )

    result = await create_agent_task(
        body,
        coordinator=coordinator,
        identity=identity(),
        container=SimpleNamespace(),
    )

    assert result["task_mode"] is TaskMode.FINITE
    assert result["task_horizon_at"] is None
    assert result["review_horizon_at"] is None


@pytest.mark.asyncio
async def test_tbo1_policy_fields_do_not_bypass_existing_owner_permission():
    coordinator = MultiAgentCoordinator(registry_with_worker())
    session = coordinator.create_session(identity(), ["worker"])
    body = AgentTaskCreateRequest(
        session_id=session.session_id,
        assigned_agent_id="worker",
        task_mode=TaskMode.RECURRING,
    )

    with pytest.raises(HTTPException) as error:
        await create_agent_task(
            body,
            coordinator=coordinator,
            identity=identity("other-user"),
            container=SimpleNamespace(),
        )

    assert error.value.status_code == 404
