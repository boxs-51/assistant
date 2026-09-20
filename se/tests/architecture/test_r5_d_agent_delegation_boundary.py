from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.task_budget import (
    AgentDelegationCycleError,
    TaskBudgetRequiredError,
)
from se.src.runtimes.agent.tool_execution.errors import (
    normalize_tool_exception,
)
from se.src.runtimes.capability.contracts.context import (
    CapabilityExecutionContext,
)
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import (
    AgentCapabilityDriver,
)


def _identity():
    return Identity(
        user_id="user-r5-d",
        auth_type="api_key",
        scopes={"*"},
    )


def _driver(captured):
    agent = AgentDefinition(
        name="agent-child",
        goal="child",
        instruction="child",
    )
    definition = CapabilityDefinition(
        id=agent.name,
        name=agent.name,
        description=agent.goal,
        kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
    )

    class Runtime:
        async def execute(self, context):
            captured["context"] = context
            return SimpleNamespace(
                error_code=None,
                error_message=None,
                model_dump=lambda mode=None: {
                    "execution_id": context.execution_id,
                    "output": "ok",
                },
            )

    return AgentCapabilityDriver(definition, agent, Runtime())


@pytest.mark.asyncio
async def test_r5_d_nested_agent_requires_durable_task_scope():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-parent",
        invocation_id="inv-child",
        caller_agent_execution_id="exec-parent",
        session_id="session-r5-d",
    )

    with pytest.raises(CapabilityError) as raised:
        await driver.execute(context, {"prompt": "delegate"})

    assert raised.value.code == "TASK_BUDGET_REQUIRED"
    assert raised.value.category == "POLICY"
    assert raised.value.retryable is False
    assert "context" not in captured


@pytest.mark.asyncio
async def test_r5_d_taskless_direct_agent_root_remains_valid():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="capability-root",
        invocation_id="inv-root",
        caller_agent_execution_id=None,
        session_id="session-r5-d",
    )

    result = await driver.execute(context, {"prompt": "root"})

    assert result["output"] == "ok"
    assert captured["context"].parent_execution_id is None
    assert captured["context"].task_id is None


@pytest.mark.asyncio
async def test_r5_d_capability_runtime_preserves_task_budget_error_code():
    from se.src.runtimes.capability.contracts.error import CapabilityError
    from se.src.runtimes.capability.runtime import CapabilityRuntime

    captured = {}
    driver = _driver(captured)
    runtime = CapabilityRuntime()
    runtime.register_capability(driver)

    with pytest.raises(CapabilityError) as raised:
        await runtime.execute_capability(
            driver.definition.capability_id,
            {"prompt": "delegate"},
            _identity(),
            execution_id="exec-parent",
            caller_agent_execution_id="exec-parent",
            invocation_id="inv-child-runtime",
            session_id="session-r5-d",
        )

    assert raised.value.code == "TASK_BUDGET_REQUIRED"
    assert raised.value.retryable is False
    assert "context" not in captured


def test_r5_d_task_budget_error_code_survives_tool_normalization():
    normalized = normalize_tool_exception(
        TaskBudgetRequiredError("task scope required"),
        capability_id="agent-child",
        invocation_id="inv-r5-d",
    )
    assert normalized.code == "TASK_BUDGET_REQUIRED"
    assert normalized.retryable is False

    cycle = normalize_tool_exception(
        AgentDelegationCycleError("cycle"),
        capability_id="agent-child",
        invocation_id="inv-r5-d-cycle",
    )
    assert cycle.code == "AGENT_DELEGATION_CYCLE"
    assert cycle.retryable is False
