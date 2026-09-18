from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionResult,
    AgentLoopState,
    InferenceMessage,
)
from se.src.runtimes.capability.builtins import register_builtin_support
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def test_builtin_support_registers_skills_specialists_and_coordinator():
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(), catalog=CapabilityCatalog()
    )
    container = SimpleNamespace(
        capability_runtime=runtime,
        agent_registry=AgentRegistry(),
        agent_runtime=SimpleNamespace(),
        authorization_service=runtime.authorization,
    )

    result = register_builtin_support(container)

    assert result["agents"] == [
        "agent-command-reviewer",
        "agent-coordinator",
        "agent-web-researcher",
    ]
    assert container.agent_registry.list_all() == []
    assert container.support_loader.is_loaded("agent-coordinator") is False
    summaries = container.support_loader.list_agent_summaries(
        Identity(auth_type="guest")
    )
    assert {item.name for item in summaries} == set(result["agents"])
    assert container.agent_registry.list_all() == []
    coordinator = container.agent_registry.get("agent-coordinator")
    assert coordinator.tools == ["agent-command-reviewer", "agent-web-researcher"]
    assert container.support_loader.is_loaded("agent-coordinator") is True
    assert container.support_loader.is_loaded("agent-web-researcher") is False
    assert container.support_loader.is_loaded("skill-web-research") is False
    container.agent_registry.get("agent-web-researcher")
    assert container.support_loader.is_loaded("skill-web-research") is True
    assert runtime.catalog.get_definition("skill-command-safety").kind.value == "SKILL"
    assert runtime.catalog.get_implementation(
        "server:agent:agent-coordinator"
    ).state.value == "ENABLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "reason"),
    [(None, "AGENT_NOT_SPECIFIED"), ("missing-agent", "AGENT_NOT_FOUND")],
)
async def test_agent_request_without_available_id_notifies_and_falls_back_to_direct(
    agent_id, reason
):
    runtime = WorkflowRuntime()
    runtime.container = SimpleNamespace(
        agent_registry=SimpleNamespace(get=lambda _agent_id: None)
    )
    runtime._execute_direct = AsyncMock()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    if agent_id:
        body["agent_id"] = agent_id
    event = BaseEvent(
        event_name="context.event.built",
        session_id="session-1",
        turn_id="turn-1",
        payload={},
    )

    await runtime._execute_agent(event, body)

    notice = runtime._execute_direct.await_args.kwargs["fallback_notice"]
    assert notice["status"] == "AGENT_FALLBACK"
    assert notice["reason"] == reason
    assert notice["fallback"] == "DIRECT"


@pytest.mark.asyncio
async def test_agent_request_propagates_requested_model_to_agent_runtime():
    runtime = WorkflowRuntime()
    agent = SimpleNamespace(model=None)
    result = AgentExecutionResult(
        execution_id="execution-1",
        agent_id="agent-coordinator",
        state=AgentLoopState.COMPLETED,
        final_message=InferenceMessage(role="assistant", content="done"),
    )
    agent_runtime = SimpleNamespace(execute=AsyncMock(return_value=result))
    runtime.event_bus = SimpleNamespace(publish=AsyncMock())
    runtime.container = SimpleNamespace(
        capability_runtime=None,
        agent_registry=SimpleNamespace(get=lambda _agent_id: agent),
        agent_runtime=agent_runtime,
    )
    body = {
        "agent_id": "agent-coordinator",
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"routing": {"prefer_provider": "gemini"}},
    }
    event = BaseEvent(
        event_name="context.event.built",
        session_id="session-1",
        turn_id="turn-1",
        payload={"identity": Identity(user_id="user-1", auth_type="guest")},
    )

    await runtime._execute_agent(event, body)

    context = agent_runtime.execute.await_args.args[0]
    assert context.metadata["model"] == "gemini-2.5-flash"
    assert context.metadata["routing"]["prefer_provider"] == "gemini"
