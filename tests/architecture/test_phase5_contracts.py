import asyncio

import pytest

from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentEventEnvelope,
    AgentEventName,
    AgentExecutionContext,
    AgentIteration,
    AgentLoopState,
    CorrelationContext,
    InferenceMessage,
    InferencePort,
    InferenceRequest,
    InferenceResponse,
    InferenceToolCall,
    InferenceToolDefinition,
    ToolExecutionPort,
    ToolExecutionRequest,
    ToolExecutionResult,
    transition,
)
from src.runtimes.agent.contracts.policy import (
    AgentExecutionPolicy,
    AgentToolPolicy,
    PolicyDecision,
)


def make_identity() -> Identity:
    return Identity(
        user_id="user-1",
        organization_id="org-1",
        auth_type="api_key",
        scopes={"tools.read"},
    )


def make_context() -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id="exec-1",
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=make_identity(),
        limits=AgentExecutionLimits(
            max_iterations=3,
            max_tool_calls=4,
            timeout_seconds=10,
        ),
        request_id="req-1",
        agent=AgentDefinition(
            name="agent-1",
            goal="test",
            instruction="test",
            tools=["calculator.add"],
        ),
        now_monotonic=100.0,
    )


def test_execution_context_has_budget_and_correlation_boundary():
    context = make_context()

    assert context.execution_id == "exec-1"
    assert context.correlation_id == "corr-1"
    assert context.limits.max_iterations == 3
    assert context.limits.max_tool_calls == 4
    assert context.iteration == 0
    assert context.tool_calls_used == 0

    context.next_iteration()
    context.record_tool_calls(2)

    assert context.iteration == 1
    assert context.tool_calls_used == 2


def test_execution_context_cancellation_is_explicit():
    context = make_context()

    assert context.cancelled is False
    context.cancel()
    assert context.cancelled is True


def test_loop_transition_contract_accepts_tool_cycle_and_rejects_terminal_restart():
    state = AgentLoopState.PREPARING
    state = transition(state, AgentLoopState.THINKING)
    state = transition(state, AgentLoopState.TOOL_CALLING)
    state = transition(state, AgentLoopState.WAITING_TOOL)
    state = transition(state, AgentLoopState.THINKING)
    state = transition(state, AgentLoopState.FINALIZING)
    state = transition(state, AgentLoopState.COMPLETED)

    assert state is AgentLoopState.COMPLETED

    with pytest.raises(ValueError, match="Invalid agent loop transition"):
        transition(AgentLoopState.COMPLETED, AgentLoopState.THINKING)


def test_iteration_preserves_tool_calls_and_rejects_invalid_terminal_transition():
    iteration = AgentIteration(
        execution_id="exec-1",
        iteration=2,
        state=AgentLoopState.TOOL_CALLING,
        inference_request_id="inf-1",
        tool_call_ids=["call-1", "call-2"],
    )

    iteration.close(AgentLoopState.WAITING_TOOL)
    assert iteration.state is AgentLoopState.WAITING_TOOL
    assert iteration.tool_call_ids == ["call-1", "call-2"]

    iteration.close(AgentLoopState.THINKING)
    with pytest.raises(ValueError):
        iteration.close(AgentLoopState.COMPLETED)


@pytest.mark.asyncio
async def test_inference_port_contract_is_provider_neutral():
    class FakeInference:
        async def complete(self, request: InferenceRequest) -> InferenceResponse:
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=InferenceMessage(
                    role="assistant",
                    tool_calls=[
                        InferenceToolCall(
                            id="call-1",
                            name="calculator.add",
                            arguments={"a": 10, "b": 32},
                        )
                    ],
                ),
                provider="mock",
                model=request.model or "mock-model",
            )

    port: InferencePort = FakeInference()
    response = await port.complete(
        InferenceRequest(
            request_id="inf-1",
            execution_id="exec-1",
            iteration=1,
            messages=[{"role": "user", "content": "10 + 32"}],
            tools=[
                InferenceToolDefinition(
                    name="calculator.add",
                    description="Add numbers",
                    parameters={"type": "object"},
                )
            ],
        )
    )

    assert response.execution_id == "exec-1"
    assert response.message.tool_calls[0].name == "calculator.add"


@pytest.mark.asyncio
async def test_tool_execution_port_contract_normalizes_success_and_failure():
    class FakeToolExecutor:
        async def execute(self, context, request):
            return ToolExecutionResult(
                execution_id=context.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=True,
                output={"result": 42},
            )

        async def execute_many(self, context, requests, *, max_parallel):
            assert max_parallel == 2
            return [await self.execute(context, request) for request in requests]

    port: ToolExecutionPort = FakeToolExecutor()
    request = ToolExecutionRequest(
        execution_id="exec-1",
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        arguments={"a": 10, "b": 32},
    )

    result = await port.execute(make_context(), request)

    assert result.success is True
    assert result.output == {"result": 42}


@pytest.mark.asyncio
async def test_tool_failure_contract_carries_retryability():
    class FakeToolExecutor:
        async def execute(self, context, request):
            return ToolExecutionResult(
                execution_id=context.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                error_message="timed out",
                retryable=True,
            )

        async def execute_many(self, context, requests, *, max_parallel):
            return [await self.execute(context, request) for request in requests]

    port: ToolExecutionPort = FakeToolExecutor()
    result = await port.execute(
        make_context(),
        ToolExecutionRequest(
            execution_id="exec-1",
            iteration=1,
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="slow.tool",
        ),
    )

    assert result.success is False
    assert result.tool_call_id == "call-1"
    assert result.error_code == "CAPABILITY_TIMEOUT"
    assert result.retryable is True


def test_context_snapshot_is_frozen():
    snapshot = AgentContextSnapshot(
        execution_id="exec-1",
        iteration=1,
        messages=[InferenceMessage(role="user", content="hello")],
        tools=[],
    )

    with pytest.raises((TypeError, ValueError)):
        snapshot.iteration = 2


def test_correlation_contract_covers_execution_iteration_tool_and_causation():
    correlation = CorrelationContext(
        correlation_id="corr-1",
        execution_id="exec-1",
        request_id="req-1",
        parent_execution_id="exec-parent",
        iteration_id="iter-1",
        tool_call_id="call-1",
        invocation_id="inv-1",
        causation_id="evt-previous",
        trace_id="trace-1",
    )

    event = AgentEventEnvelope(
        event_id="evt-1",
        event_name=AgentEventName.TOOL_COMPLETED,
        correlation=correlation,
        payload={"success": True},
    )

    assert event.correlation.execution_id == "exec-1"
    assert event.correlation.tool_call_id == "call-1"
    assert event.correlation.causation_id == "evt-previous"


def test_policy_protocols_are_implementable_without_runtime_coupling():
    class AllowToolPolicy:
        def is_visible(self, *, agent_id, capability_id):
            return capability_id == "calculator.add"

        def authorize(self, *, identity, agent_id, capability_id):
            return (
                PolicyDecision.ALLOW
                if capability_id == "calculator.add"
                else PolicyDecision.DENY
            )

    class BasicExecutionPolicy:
        def check_start(self, context):
            return PolicyDecision.ALLOW

        def check_iteration(self, context, iteration):
            return (
                PolicyDecision.ALLOW
                if iteration <= context.limits.max_iterations
                else PolicyDecision.DENY
            )

        def check_tool_call(self, context, request):
            return (
                PolicyDecision.ALLOW
                if context.tool_calls_used < context.limits.max_tool_calls
                else PolicyDecision.DENY
            )

        def limits(self, context):
            return context.limits

    tool_policy: AgentToolPolicy = AllowToolPolicy()
    execution_policy: AgentExecutionPolicy = BasicExecutionPolicy()
    context = make_context()

    request = ToolExecutionRequest(
        execution_id="exec-1",
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        arguments={"a": 1, "b": 2},
    )

    assert tool_policy.is_visible(
        agent_id="agent-1",
        capability_id="calculator.add",
    )
    assert tool_policy.authorize(
        identity=make_identity(),
        agent_id="agent-1",
        capability_id="calculator.add",
    ) is PolicyDecision.ALLOW
    assert execution_policy.check_start(context) is PolicyDecision.ALLOW
    assert execution_policy.check_iteration(context, 1) is PolicyDecision.ALLOW
    assert execution_policy.check_tool_call(context, request) is PolicyDecision.ALLOW
    assert execution_policy.limits(context).max_tool_calls == 4


def test_context_iteration_and_tool_counters_are_incremental():
    context = make_context()

    assert context.next_iteration() == 1
    assert context.next_iteration() == 2
    assert context.next_iteration() == 3

    assert context.record_tool_calls(2) == 2
    assert context.record_tool_calls(3) == 5

    with pytest.raises(ValueError, match="non-negative"):
        context.record_tool_calls(-1)