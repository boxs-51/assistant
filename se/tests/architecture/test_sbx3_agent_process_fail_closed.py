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


# T1 negative-only alias oracles.  These use process-affiliated synthetic
# capability IDs, not a claim that external JSON/MCP can register Python
# handlers or that a kernel-enforced sandbox is present.
_PROCESS_ALIASES = (
    "plugin.terminal.run",
    "plugin.terminal.launch",
    "plugin.python.run",
)


@pytest.mark.parametrize("capability_id", _PROCESS_ALIASES)
def test_process_alias_execution_fails_before_runtime_dispatch(capability_id):
    from se.src.runtimes.capability.contracts.definition import (
        CapabilityDefinition,
        CapabilityEffect,
    )

    definition = CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="Synthetic process-bound alias of a trusted executable",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        effects={CapabilityEffect.EXECUTE},
        metadata={"physical_export": "terminal.run"},
    )
    dispatched = AsyncMock(
        return_value=SimpleNamespace(
            invocation_id="alias-execute", output={"dispatched": True}, metadata={}
        )
    )
    runtime = SimpleNamespace(
        registry=SimpleNamespace(
            get=lambda name: SimpleNamespace(definition=definition, executable=True)
        ),
        execute_capability=dispatched,
        catalog=None,
    )
    context = SimpleNamespace(
        execution_id="sbx3-alias-execution",
        connection_id=None,
        agent_id="sbx3-alias-agent",
        identity=SimpleNamespace(user_id="sbx3-user"),
        ensure_active=lambda: None,
        remaining_seconds=20,
        remaining_iteration_seconds=20,
        remaining_for_operation=lambda *_: 10,
        reserve_tool_call=AsyncMock(return_value=True),
        record_usage=lambda **_: None,
        limits=SimpleNamespace(max_tool_calls=3, tool_timeout_seconds=10),
        metadata={},
        request_id=None,
        session_id="sbx3-session",
        task_id=None,
        branch_id=None,
        correlation_id="sbx3-test",
        trace_id=None,
        workflow_id=None,
        cancellation_event=None,
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy=SimpleNamespace(
            is_visible=lambda **_: True,
            authorize=lambda **_: PolicyDecision.ALLOW,
        ),
        execution_policy=SimpleNamespace(
            check_tool_call=lambda *_: PolicyDecision.ALLOW,
        ),
    )
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=0,
        invocation_id="alias-execute",
        tool_call_id="alias-tool-call",
        capability_id=capability_id,
        connection_id=None,
        arguments={"command": "MUST_NOT_RUN"},
    )
    result = asyncio.run(adapter.execute(context, request))
    assert result.error_code == "AGENT_TOOL_POLICY_DENIED", (
        "Executable alias was allowed past the Agent pre-dispatch policy",
        result,
    )
    dispatched.assert_not_awaited()


def _r7_alias_action(capability_id):
    from se.src.runtimes.agent.contracts.resume import (
        ResumeInvocationActionKind,
    )

    return SimpleNamespace(
        invocation_id="sbx3-alias-continuation",
        tool_call_id="sbx3-alias-toolcall",
        capability_id=capability_id,
        action=ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
        expected_invocation_revision=1,
        request_fingerprint="sbx3-synthetic-fingerprint",
    )


def _r7_alias_context():
    return SimpleNamespace(
        execution_id="sbx3-alias-execution",
        iteration=0,
        connection_id="sbx3-claimed-client",
        ensure_active=lambda: None,
        cancellation_event=None,
        limits=SimpleNamespace(max_parallel_tools=1),
    )


@pytest.mark.parametrize("capability_id", _PROCESS_ALIASES)
def test_process_alias_r7_continuation_rejected_before_runtime(capability_id):
    runtime = SimpleNamespace(
        continue_invocation=AsyncMock(
            return_value=SimpleNamespace(output={"dispatched": True}, metadata={})
        ),
        catalog=None,
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime, tool_policy=object(), execution_policy=object()
    )
    with pytest.raises(CapabilityContinuationDispatchGuardError) as exc:
        asyncio.run(
            adapter.continue_invocation(
                _r7_alias_context(), _r7_alias_action(capability_id)
            )
        )
    assert exc.value.code == "AGENT_TOOL_POLICY_DENIED"
    runtime.continue_invocation.assert_not_awaited()


@pytest.mark.parametrize("capability_id", _PROCESS_ALIASES)
def test_process_alias_never_eligible_for_server_continuation(capability_id):
    from se.src.runtimes.capability.contracts.implementation import (
        CapabilityExecutionLocation,
    )

    runtime = SimpleNamespace(
        catalog=SimpleNamespace(
            list_implementations=lambda *_args, **_kwargs: [
                SimpleNamespace(location=CapabilityExecutionLocation.SERVER)
            ]
        )
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime, tool_policy=object(), execution_policy=object()
    )
    assert adapter.can_continue_server_side(capability_id) is False


@pytest.mark.parametrize("capability_id", _PROCESS_ALIASES)
def test_process_alias_r12_coordinator_prepared_continuation_never_dispatches(
    capability_id,
):
    from se.src.runtimes.agent.tool_execution.coordinator import (
        AgentToolExecutionCoordinator,
    )

    runtime = SimpleNamespace(
        continue_invocation=AsyncMock(
            return_value=SimpleNamespace(output={"dispatched": True}, metadata={})
        ),
        catalog=None,
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime, tool_policy=object(), execution_policy=object()
    )
    coordinator = AgentToolExecutionCoordinator(adapter)
    action = _r7_alias_action(capability_id)
    prepared = []
    guard_calls = []

    async def prepare(raw):
        prepared.append(raw.invocation_id)
        return raw

    async def canonical_guard(*args):
        guard_calls.append(args)

    async def run():
        return await coordinator.continue_invocations(
            _r7_alias_context(),
            [action],
            max_parallel=1,
            pre_dispatch_prepare=prepare,
            canonical_dispatch_guard=canonical_guard,
            preserve_started_on_failure=True,
        )

    with pytest.raises(CapabilityContinuationDispatchGuardError) as exc:
        asyncio.run(run())
    assert exc.value.code == "AGENT_TOOL_POLICY_DENIED"
    assert prepared == [action.invocation_id]
    assert not guard_calls, "Alias reached R12 final-send guard after missed early deny"
    runtime.continue_invocation.assert_not_awaited()
