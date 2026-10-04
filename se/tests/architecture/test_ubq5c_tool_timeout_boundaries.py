from __future__ import annotations

import asyncio
import inspect

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.tool_execution.coordinator import AgentToolExecutionCoordinator
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import (
    TOOL_CALL_TIMEOUT,
    TOOL_IDLE_TIMEOUT,
    CapabilityError,
)
from se.src.runtimes.capability.contracts.invocation import CapabilityInvocationState
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.runtime import CapabilityRuntime


class _SlowDriver(BaseCapabilityDriver):
    async def execute(self, context, arguments):
        await asyncio.sleep(0.05)
        return {"ok": True}


def _identity() -> Identity:
    return Identity(user_id="ubq5c-user", auth_type="api_key", scopes={"*"})


def _runtime(*, mode: CapabilityExecutionMode = CapabilityExecutionMode.ONE_SHOT):
    capability_id = f"tool.ubq5c.{mode.value.lower()}"
    definition = CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="UBQ-5C tool-timeout evidence",
        kind=CapabilityKind.TOOL,
        execution_mode=mode,
        input_schema={"type": "object"},
    )
    runtime = CapabilityRuntime()
    runtime.register_capability(_SlowDriver(definition))
    return runtime, capability_id


def test_ubq5c_freezes_hard_and_idle_taxonomy_without_idle_activation() -> None:
    assert TOOL_CALL_TIMEOUT == "TOOL_CALL_TIMEOUT"
    assert TOOL_IDLE_TIMEOUT == "TOOL_IDLE_TIMEOUT"

    runtime_source = inspect.getsource(CapabilityRuntime)
    adapter_source = inspect.getsource(CapabilityToolExecutionAdapter)
    coordinator_source = inspect.getsource(AgentToolExecutionCoordinator)

    # V1 has no trustworthy semantic progress signal, so idle taxonomy is dormant.
    assert "TOOL_IDLE_TIMEOUT" not in runtime_source
    assert "TOOL_IDLE_TIMEOUT" not in adapter_source
    assert "TOOL_IDLE_TIMEOUT" not in coordinator_source

    # Ordinary Agent tool timeout remains subordinate to the existing operation bound.
    assert "remaining_for_operation" in adapter_source
    assert "CapabilityExecutionMode.LONG_RUNNING" in adapter_source
    assert "timeout_seconds is not None" in runtime_source


def test_ubq5c_adapter_preserves_long_running_pre_dispatch_legacy_timeout() -> None:
    adapter_source = inspect.getsource(CapabilityToolExecutionAdapter)

    timeout_branch = adapter_source.split("if timeout <= 0:", 1)[1].split(
        "if not await context.reserve_tool_call()", 1
    )[0]

    assert "if is_long_running:" in timeout_branch
    assert 'code="CAPABILITY_TIMEOUT"' in timeout_branch
    assert 'message="Agent execution deadline exceeded before tool start."' in timeout_branch
    assert "code=TOOL_CALL_TIMEOUT" in timeout_branch
    assert '"timeout_scope": "tool_call"' in timeout_branch


@pytest.mark.asyncio
async def test_ubq5c_agent_owned_one_shot_tool_projects_tool_call_timeout() -> None:
    runtime, capability_id = _runtime()

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            capability_id,
            {},
            _identity(),
            execution_id="exec-ubq5c",
            caller_agent_execution_id="exec-ubq5c",
            invocation_id="inv-ubq5c-agent-tool",
            timeout_seconds=0.001,
        )

    assert caught.value.code == TOOL_CALL_TIMEOUT
    assert caught.value.details["timeout_scope"] == "tool_call"
    assert caught.value.details["timeout_seconds"] == pytest.approx(0.001)

    persisted = await runtime.invocation_lifecycle.store.get(
        "inv-ubq5c-agent-tool"
    )
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.TIMED_OUT
    assert persisted.error["code"] == TOOL_CALL_TIMEOUT


@pytest.mark.asyncio
async def test_ubq5c_direct_capability_timeout_keeps_legacy_projection() -> None:
    runtime, capability_id = _runtime()

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            capability_id,
            {},
            _identity(),
            invocation_id="inv-ubq5c-direct-tool",
            timeout_seconds=0.001,
        )

    assert caught.value.code == "CAPABILITY_TIMEOUT"
    assert caught.value.details == {}

    persisted = await runtime.invocation_lifecycle.store.get(
        "inv-ubq5c-direct-tool"
    )
    assert persisted is not None
    assert persisted.error == {
        "code": "CAPABILITY_TIMEOUT",
        "message": "",
    }


@pytest.mark.asyncio
async def test_ubq5c_long_running_tool_keeps_existing_timeout_projection() -> None:
    runtime, capability_id = _runtime(mode=CapabilityExecutionMode.LONG_RUNNING)

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            capability_id,
            {},
            _identity(),
            execution_id="exec-ubq5c-long",
            caller_agent_execution_id="exec-ubq5c-long",
            invocation_id="inv-ubq5c-long",
            timeout_seconds=0.001,
        )

    assert caught.value.code == "CAPABILITY_TIMEOUT"
    assert caught.value.details == {}
