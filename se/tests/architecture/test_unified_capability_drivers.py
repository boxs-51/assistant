from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
)
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import AgentCapabilityDriver
from se.src.runtimes.capability.drivers.skill_driver import (
    ExecutableSkillCapabilityDriver,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


@pytest.mark.asyncio
async def test_executable_skill_runs_through_capability_runtime():
    class Inference:
        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=1,
                message=InferenceMessage(role="assistant", content="reviewed"),
                provider="test",
                model="test-model",
            )

    inference = Inference()
    definition = CapabilityDefinition(
        id="skill.review",
        name="skill.review",
        description="Review text",
        kind=CapabilityKind.SKILL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
    )
    runtime = CapabilityRuntime()
    runtime.register_capability(
        ExecutableSkillCapabilityDriver(
            definition, "Review carefully.", inference
        )
    )

    result = await runtime.execute_capability(
        definition.capability_id,
        {"prompt": "draft"},
        Identity(user_id="u1", auth_type="jwt"),
    )

    assert result.output["message"]["content"] == "reviewed"
    assert inference.requests[0].messages[0].content == "Review carefully."
    assert inference.requests[0].messages[1].content == "draft"


@pytest.mark.asyncio
async def test_agent_capability_creates_distinct_child_execution_identity():
    class AgentRuntime:
        def __init__(self):
            self.context = None

        async def execute(self, context):
            self.context = context
            return SimpleNamespace(
                error_code=None,
                error_message=None,
                model_dump=lambda mode=None: {
                    "execution_id": context.execution_id,
                    "output": "done",
                },
            )

    agent_runtime = AgentRuntime()
    agent = AgentDefinition(
        name="agent.research",
        goal="Research",
        instruction="Use evidence.",
    )
    definition = CapabilityDefinition(
        id=agent.name,
        name=agent.name,
        description=agent.goal,
        kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
    )
    runtime = CapabilityRuntime()
    runtime.register_capability(
        AgentCapabilityDriver(
            definition,
            agent,
            agent_runtime,
            execution_id_factory=AgentExecutionIdFactory(
                prefix="exec_",
                token_factory=lambda: "child",
            ),
        )
    )

    result = await runtime.execute_capability(
        definition.capability_id,
        {"prompt": "investigate"},
        Identity(user_id="u1", auth_type="jwt"),
        execution_id="exec-agent-capability",
        caller_agent_execution_id="exec-agent-capability",
        invocation_id="inv-agent-capability",
        session_id="session-1",
        task_id="task-agent-capability",
    )

    assert result.output["output"] == "done"
    assert agent_runtime.context.execution_id == "exec_child"
    assert agent_runtime.context.execution_id != "exec-agent-capability"
    assert agent_runtime.context.parent_execution_id == "exec-agent-capability"
    assert agent_runtime.context.causation_id == "inv-agent-capability"
    assert agent_runtime.context.metadata["invocation_id"] == "inv-agent-capability"
    assert agent_runtime.context.input == {"prompt": "investigate"}
