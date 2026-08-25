import pytest

from src.agent.registry import AgentRegistry
from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.identity import Identity
from src.domain.schemas.multi_agent import AgentMessageType, AgentTaskStatus
from src.runtimes.agent.coordinator import MultiAgentCoordinator


def identity(user_id="user-1"):
    return Identity(user_id=user_id, organization_id="org-1", auth_type="api_key")


def registry_with_agents():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="planner", goal="Plan", instruction="Plan tasks"))
    registry.register(AgentDefinition(name="worker", goal="Work", instruction="Complete tasks"))
    return registry


def test_multi_agent_session_supports_membership_and_messages():
    coordinator = MultiAgentCoordinator(registry_with_agents())
    session = coordinator.create_session(identity(), ["planner"])
    coordinator.add_agent(session.session_id, "worker", identity())

    message = coordinator.send_message(
        session.session_id,
        sender_id="planner",
        message_type=AgentMessageType.AGENT_MESSAGE,
        payload={"text": "start"},
        identity=identity(),
        recipient_id="worker",
    )

    assert message.recipient_id == "worker"
    assert coordinator.list_messages(session.session_id, identity())[0].payload["text"] == "start"


def test_multi_agent_task_delegation_and_cancellation():
    coordinator = MultiAgentCoordinator(registry_with_agents())
    session = coordinator.create_session(identity(), ["planner", "worker"])
    task = coordinator.create_task(
        session.session_id,
        assigned_agent_id="worker",
        task_input={"query": "research"},
        identity=identity(),
    )

    assert task.status == AgentTaskStatus.ASSIGNED
    cancelled = coordinator.cancel_task(task.task_id, identity())
    assert cancelled.status == AgentTaskStatus.CANCELLED


def test_multi_agent_access_is_isolated_by_session_owner():
    coordinator = MultiAgentCoordinator(registry_with_agents())
    session = coordinator.create_session(identity(), ["planner"])

    with pytest.raises(LookupError):
        coordinator.list_messages(session.session_id, identity("other-user"))

    with pytest.raises(PermissionError):
        coordinator.create_task(
            session.session_id,
            assigned_agent_id="worker",
            task_input={},
            identity=identity(),
        )
