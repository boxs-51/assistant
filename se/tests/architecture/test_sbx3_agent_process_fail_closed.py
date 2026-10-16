"""SBX-3 interim Agent-only fail-closed guard; NOT OS confinement certification.

These tests never spawn processes, run commands, or access a network.
The test does not certify native Linux/Windows process/FS/egress NONE,
descendant VERIFIED_EMPTY, crash recovery, or durable receipt fencing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.tool import ToolExecutionRequest
from se.src.runtimes.capability.contracts.error import (
    CapabilityContinuationDispatchGuardError,
)


@pytest.mark.parametrize("capability_id", ["terminal.run", "terminal.launch", "python.run"])
@pytest.mark.parametrize("connection_id", [None, "remote-client-connection"])
def test_uncertified_agent_command_never_dispatches(capability_id, connection_id):
    runtime = SimpleNamespace(execute_capability=AsyncMock())
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy=object(),
        execution_policy=object(),
    )
    context = SimpleNamespace(
        execution_id="sbx3-test-execution",
        connection_id=connection_id,
    )
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=0,
        invocation_id="sbx3-test-invocation",
        tool_call_id="sbx3-test-tool-call",
        capability_id=capability_id,
        connection_id=connection_id,
        arguments={"command": "SHOULD_NEVER_EXECUTE"},
    )

    result = asyncio.run(adapter.execute(context, request))

    assert result.success is False
    assert result.error_code == "AGENT_TOOL_POLICY_DENIED"
    assert result.retryable is False
    assert result.metadata["sbx3_native_isolation"] == "NOT_CERTIFIED"
    assert result.metadata["r7_commit_authority"] == "AGENT_PRE_DISPATCH"
    runtime.execute_capability.assert_not_awaited()


@pytest.mark.parametrize("capability_id", ["terminal.run", "terminal.launch", "python.run"])
def test_uncertified_agent_continuation_rejects_before_runtime(capability_id):
    runtime = SimpleNamespace(continue_invocation=AsyncMock(), catalog=None)
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy=object(),
        execution_policy=object(),
    )
    context = SimpleNamespace(connection_id="claimed-client")
    # The guard occurs before continuation interpretation or runtime dispatch.
    action = SimpleNamespace(action=object(), capability_id=capability_id)

    with pytest.raises(CapabilityContinuationDispatchGuardError) as exc_info:
        asyncio.run(adapter.continue_invocation(context, action))

    assert exc_info.value.code == "AGENT_TOOL_POLICY_DENIED"
    assert exc_info.value.retryable is False
    assert adapter.can_continue_server_side(capability_id) is False
    runtime.continue_invocation.assert_not_awaited()


def test_nonprocess_capability_still_uses_normal_agent_policy():
    runtime = SimpleNamespace(execute_capability=AsyncMock())
    policy = SimpleNamespace(
        check_tool_call=lambda context, request: PolicyDecision.DENY
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy=object(),
        execution_policy=policy,
    )
    context = SimpleNamespace(
        execution_id="sbx3-test-execution",
        connection_id=None,
        tool_calls_used=0,
        limits=SimpleNamespace(max_tool_calls=2),
    )
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=0,
        invocation_id="sbx3-test-other-invocation",
        tool_call_id="sbx3-test-other-call",
        capability_id="file.read",
        arguments={"path": "not-opened.txt"},
    )
    result = asyncio.run(adapter.execute(context, request))
    assert result.success is False
    assert result.error_code == "AGENT_TOOL_POLICY_DENIED"
    assert "sbx3_native_isolation" not in result.metadata
    runtime.execute_capability.assert_not_awaited()
