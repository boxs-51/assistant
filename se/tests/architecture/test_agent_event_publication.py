from __future__ import annotations

import asyncio
import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentEventName,
    AgentExecutionContext,
    AgentLoopState,
    AgentEventEnvelope,
    InferenceMessage,
    InferenceToolCall,
    InferenceResponse,
    InferenceUsage,
    ToolExecutionResult,
    ToolExecutionRequest,
)
from se.src.runtimes.agent.events import EventBusAgentEventPublisher
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.runtime import _public_tool_arguments
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter

class Publisher:
    def __init__(self):
        self.events: list[AgentEventEnvelope] = []

    async def publish(self, event: AgentEventEnvelope) -> None:
        self.events.append(event)


class ContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
        )


class Inference:
    async def complete(self, request):
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(role="assistant", content="done"),
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="test",
            model="test-model",
        )


class Tools:
    async def execute_many(self, context, requests, *, max_parallel):
        return []


class ToolInference:
    def __init__(self, content="Preparing calculator"):
        self.calls = 0
        self.content = content

    async def complete(self, request):
        self.calls += 1
        message = (
            InferenceMessage(
                role="assistant",
                content=self.content,
                tool_calls=(
                    InferenceToolCall(
                        id="call-1",
                        name="calculator.add",
                        arguments={"left": 1},
                    ),
                ),
            )
            if self.calls == 1
            else InferenceMessage(role="assistant", content="done")
        )
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="test",
            model="test-model",
        )


class ToolPort:
    async def execute_many(self, context, requests, *, max_parallel):
        request = requests[0]
        return [
            ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=True,
                output={"value": 3},
            )
        ]


def make_context():
    agent = AgentDefinition(
        name="phase5-10-agent",
        goal="event gate",
        instruction="test",
        tools=[],
    )
    return AgentExecutionContext.create(
        execution_id="exec-phase5-10",
        agent_id=agent.name,
        session_id="session-phase5-10",
        correlation_id="corr-phase5-10",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(max_iterations=1),
        agent=agent,
    )


@pytest.mark.asyncio
async def test_agent_runtime_publishes_lifecycle_events():
    publisher = Publisher()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=Inference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=publisher,
    )

    result = await runtime.execute(make_context())
    names = [event.event_name for event in publisher.events]

    assert result.output == "done"
    assert names == [
        AgentEventName.EXECUTION_STARTED,
        AgentEventName.ITERATION_STARTED,
        AgentEventName.INFERENCE_REQUESTED,
        AgentEventName.INFERENCE_COMPLETED,
        AgentEventName.ITERATION_COMPLETED,
        AgentEventName.EXECUTION_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_agent_events_preserve_correlation_fields():
    publisher = Publisher()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=Inference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=publisher,
    )

    await runtime.execute(make_context())

    for event in publisher.events:
        correlation = event.correlation
        assert correlation.session_id == "session-phase5-10"
        assert correlation.execution_id == "exec-phase5-10"
        assert correlation.correlation_id == "corr-phase5-10"
    iteration_event = next(
        event for event in publisher.events if event.event_name == AgentEventName.ITERATION_STARTED
    )
    assert iteration_event.correlation.iteration_id == "exec-phase5-10:iteration:1"


@pytest.mark.asyncio
async def test_tool_lifecycle_events_are_published():
    publisher = Publisher()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=ToolInference(),
        tool_execution=ToolPort(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=publisher,
    )

    await runtime.execute(make_context())
    names = [event.event_name for event in publisher.events]
    assert AgentEventName.TOOL_REQUESTED in names
    assert AgentEventName.PROGRESS in names
    assert AgentEventName.TOOL_STARTED in names
    assert AgentEventName.TOOL_COMPLETED in names
    assert names.index(AgentEventName.PROGRESS) < names.index(AgentEventName.TOOL_REQUESTED)
    tool_event = next(
        event for event in publisher.events
        if event.event_name == AgentEventName.TOOL_COMPLETED
    )
    assert tool_event.correlation.tool_call_id == "call-1"
    assert tool_event.correlation.invocation_id
    progress = next(event for event in publisher.events if event.event_name == AgentEventName.PROGRESS)
    assert progress.payload["content"] == "Preparing calculator"
    assert progress.payload["tool_calls"] == [{
        "tool_call_id": "call-1",
        "name": "calculator.add",
        "purpose": "Preparing calculator",
        "arguments": {"left": 1},
    }]
    for event in publisher.events:
        if event.event_name.startswith("agent.tool."):
            assert event.payload["capability_id"] == "calculator.add"
            assert event.payload["arguments"] == {"left": 1}
            assert event.payload["purpose"]


@pytest.mark.asyncio
async def test_tool_call_without_assistant_text_still_has_a_described_response():
    publisher = Publisher()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=ToolInference(content=None),
        tool_execution=ToolPort(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=publisher,
    )
    await runtime.execute(make_context())
    response = next(event for event in publisher.events if event.event_name == AgentEventName.PROGRESS)
    assert response.payload["content"] == ""
    assert response.payload["tool_calls"][0]["purpose"] == "Use calculator.add"


def test_public_tool_arguments_redact_nested_secrets():
    assert _public_tool_arguments({
        "skill_id": "web-research",
        "options": {"api_key": "private", "query": "public"},
    }) == {
        "skill_id": "web-research",
        "options": {"api_key": "[redacted]", "query": "public"},
    }


@pytest.mark.asyncio
async def test_tool_dispatch_failure_publishes_tool_failed():
    publisher = Publisher()

    class BrokenTools:
        async def execute_many(self, context, requests, *, max_parallel):
            raise RuntimeError("tool unavailable")

    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=ToolInference(),
        tool_execution=BrokenTools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=publisher,
    )

    result = await runtime.execute(make_context())
    assert result.error_code == "RuntimeError"
    failed = [
        event for event in publisher.events
        if event.event_name == AgentEventName.TOOL_FAILED
    ]
    assert len(failed) == 1
    assert failed[0].correlation.tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_publisher_failure_does_not_change_execution_result():
    class BrokenPublisher:
        async def publish(self, event):
            raise RuntimeError("telemetry unavailable")

    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=Inference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
        event_publisher=BrokenPublisher(),
    )

    result = await runtime.execute(make_context())
    assert result.output == "done"


@pytest.mark.asyncio
async def test_model_timeout_is_reported_as_inference_budget():
    class TimeoutInference:
        async def complete(self, request):
            raise asyncio.TimeoutError("model call expired")

    context = make_context()
    context.limits.timeout_seconds = 2
    context.limits.iteration_timeout_seconds = 1
    context.limits.inference_timeout_seconds = 0.5
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=TimeoutInference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.error_code == "AGENT_INFERENCE_TIMEOUT"


@pytest.mark.asyncio
async def test_iteration_deadline_has_distinct_timeout_code():
    class SlowContextBuilder:
        async def build(self, context, request):
            await asyncio.sleep(0.1)

    context = make_context()
    context.limits.timeout_seconds = 1
    context.limits.iteration_timeout_seconds = 0.01
    runtime = AgentRuntime(
        context_builder=SlowContextBuilder(),
        inference=Inference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.error_code == "AGENT_ITERATION_TIMEOUT"


@pytest.mark.asyncio
async def test_tool_timeout_has_distinct_timeout_code():
    class TimeoutTools:
        async def execute_many(self, context, requests, *, max_parallel):
            raise TimeoutError("tool expired")

    context = make_context()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=ToolInference(),
        tool_execution=TimeoutTools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.error_code == "AGENT_TOOL_TIMEOUT"


@pytest.mark.asyncio
async def test_agent_can_propose_task_deadline_and_reallocate_step_time():
    context = make_context()
    context.metadata.update({
        "agent_time_budget_enabled": True,
        "task_started_at": context.clock.now_utc().timestamp(),
        "task_max_timeout_seconds": 900.0,
        "task_deadline_at": None,
    })
    adapter = CapabilityToolExecutionAdapter(None, None, None)

    def request(invocation_id, arguments):
        return ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id=invocation_id,
            tool_call_id=invocation_id,
            capability_id="agent.budget.configure",
            arguments=arguments,
        )

    first = await adapter.execute(context, request("budget-1", {
        "task_seconds": 600,
        "iteration_seconds": 420,
        "inference_seconds": 90,
        "tool_seconds": 330,
    }))
    assert first.success is True
    assert context.remaining_seconds > 590
    assert context.limits.tool_timeout_seconds == 330
    deadline = context.metadata["task_deadline_at"]

    second = await adapter.execute(context, request("budget-2", {
        "iteration_seconds": 180,
        "tool_seconds": 150,
    }))
    assert second.success is True
    assert context.metadata["task_deadline_at"] == deadline

    rejected = await adapter.execute(context, request("budget-3", {
        "task_seconds": 900,
    }))
    assert rejected.error_code == "AGENT_BUDGET_INVALID"
    assert context.metadata["task_deadline_at"] == deadline


@pytest.mark.asyncio
async def test_inference_timeout_feedback_reaches_next_model_call():
    class TranscriptBuilder:
        async def build(self, context, request):
            return AgentContextSnapshot(
                execution_id=context.execution_id,
                iteration=request.iteration,
                messages=tuple(InferenceMessage.model_validate(item) for item in request.prior_messages),
            )

    class RetryInference(Inference):
        def __init__(self):
            self.messages = []

        async def complete(self, request):
            self.messages.append(request.messages)
            if len(self.messages) == 1:
                raise asyncio.TimeoutError("model exceeded its operation budget")
            return await super().complete(request)

    context = make_context()
    context.limits.max_iterations = 2
    context.metadata.update({
        "agent_time_budget_enabled": True,
        "task_started_at": context.clock.now_utc().timestamp(),
        "task_max_timeout_seconds": 600.0,
        "task_deadline_at": None,
    })
    inference = RetryInference()
    runtime = AgentRuntime(
        context_builder=TranscriptBuilder(),
        inference=inference,
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.state == AgentLoopState.COMPLETED
    assert len(inference.messages) == 2
    assert "previous model call timed out" in inference.messages[1][-1].content


@pytest.mark.asyncio
async def test_expired_task_deadline_is_distinct_from_agent_execution_timeout():
    context = make_context()
    context.metadata["task_deadline_at"] = context.clock.now_utc().timestamp() - 1
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=Inference(),
        tool_execution=Tools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.error_code == "AGENT_TASK_TIMEOUT"


@pytest.mark.asyncio
async def test_tool_timeout_feedback_reaches_agent_without_automatic_replay():
    class TranscriptBuilder:
        async def build(self, context, request):
            return AgentContextSnapshot(
                execution_id=context.execution_id,
                iteration=request.iteration,
                messages=tuple(InferenceMessage.model_validate(item) for item in request.prior_messages),
            )

    class RecordingInference(ToolInference):
        def __init__(self):
            super().__init__()
            self.messages = []

        async def complete(self, request):
            self.messages.append(request.messages)
            return await super().complete(request)

    class TimeoutResultTools:
        def __init__(self):
            self.calls = 0

        async def execute_many(self, context, requests, *, max_parallel):
            self.calls += 1
            request = requests[0]
            return [ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                error_message="tool timed out",
            )]

    context = make_context()
    context.limits.max_iterations = 2
    context.metadata.update({
        "agent_time_budget_enabled": True,
        "task_started_at": context.clock.now_utc().timestamp(),
        "task_max_timeout_seconds": 600.0,
        "task_deadline_at": None,
    })
    inference = RecordingInference()
    tools = TimeoutResultTools()
    runtime = AgentRuntime(
        context_builder=TranscriptBuilder(),
        inference=inference,
        tool_execution=tools,
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)
    assert result.state == AgentLoopState.COMPLETED
    assert tools.calls == 1
    assert any("A tool call timed out" in str(item.content) for item in inference.messages[1])


@pytest.mark.asyncio
async def test_internal_budget_tool_does_not_require_capability_invocation_commit():
    class Store:
        def __init__(self):
            self.checkpoints = []

        async def load_iteration(self, execution_id, *, iteration_number):
            return None

        async def save_iteration(self, values):
            return values

        async def update_checkpoint(self, execution_id, values):
            self.checkpoints.append(values)

        async def save_tool_call(self, values):
            return values

        async def save_tool_result(self, values):
            return values

        async def load_committed_tool_result(self, execution_id, tool_call_id):
            raise AssertionError("Internal budget tool has no CapabilityInvocation")

    class BudgetInference:
        def __init__(self):
            self.calls = 0

        async def complete(self, request):
            self.calls += 1
            message = (
                InferenceMessage(
                    role="assistant",
                    content="Set the task budget",
                    tool_calls=(InferenceToolCall(
                        id="budget-call",
                        name="agent.budget.configure",
                        arguments={
                            "task_seconds": 600,
                            "iteration_seconds": 420,
                            "inference_seconds": 90,
                            "tool_seconds": 330,
                        },
                    ),),
                )
                if self.calls == 1
                else InferenceMessage(role="assistant", content="done")
            )
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=message,
                finish_reason="stop",
                usage=InferenceUsage(),
                provider="test",
                model="test-model",
            )

    context = make_context()
    context.limits.max_iterations = 2
    context.metadata.update({
        "agent_time_budget_enabled": True,
        "task_started_at": context.clock.now_utc().timestamp(),
        "task_max_timeout_seconds": 900.0,
        "task_deadline_at": None,
    })
    store = Store()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=BudgetInference(),
        tool_execution=CapabilityToolExecutionAdapter(None, None, None),
        execution_policy=DefaultAgentExecutionPolicy(),
        durable_store=store,
    )
    result = await runtime.execute(context)
    assert result.state == AgentLoopState.COMPLETED
    assert store.checkpoints[-1]["context_state"]["metadata"]["task_deadline_at"]


@pytest.mark.asyncio
async def test_event_bus_adapter_maps_agent_envelope_to_base_event():
    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, event):
            self.events.append(event)
            return None

    bus = Bus()
    publisher = EventBusAgentEventPublisher(bus)
    event = AgentEventEnvelope(
        event_id="event-1",
        event_name=AgentEventName.EXECUTION_STARTED,
        correlation={
            "correlation_id": "corr-1",
            "session_id": "session-1",
            "execution_id": "exec-1",
        },
        payload={"value": 1},
    )

    await publisher.publish(event)
    assert bus.events[0].event_name == AgentEventName.EXECUTION_STARTED
    assert bus.events[0].session_id == "session-1"
    assert bus.events[0].turn_id == "corr-1"
    assert bus.events[0].payload["correlation"]["execution_id"] == "exec-1"


