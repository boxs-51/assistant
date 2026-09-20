from __future__ import annotations

from pathlib import Path

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentEventName,
    AgentExecutionContext,
    AgentEventEnvelope,
    InferenceMessage,
    InferenceToolCall,
    InferenceResponse,
    InferenceUsage,
    ToolExecutionResult,
)
from se.src.runtimes.agent.events import EventBusAgentEventPublisher
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy

ROOT = Path(__file__).resolve().parents[3]
EXIT_GATE_DOC = ROOT / "se" / "docs" / "exit-gate" / "PHASE5_10_EXIT_GATE.md"
LEGACY_STATUS_DOC = ROOT / "se" / "docs" / "legacy" / "phase5" / "Agent_Execution_System.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "phase5-10-exit-gate.yml"
MAIN_MODULE = ROOT / "se" / "src" / "main.py"


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
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        message = (
            InferenceMessage(
                role="assistant",
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
async def test_E1_E2_runtime_publishes_lifecycle_events():
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
async def test_E3_events_preserve_correlation_fields():
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
async def test_E2_tool_lifecycle_events_are_published():
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
    assert AgentEventName.TOOL_STARTED in names
    assert AgentEventName.TOOL_COMPLETED in names
    tool_event = next(
        event for event in publisher.events
        if event.event_name == AgentEventName.TOOL_COMPLETED
    )
    assert tool_event.correlation.tool_call_id == "call-1"
    assert tool_event.correlation.invocation_id


@pytest.mark.asyncio
async def test_E2_tool_dispatch_failure_publishes_tool_failed():
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
async def test_E4_publisher_failure_does_not_change_execution_result():
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
async def test_E5_event_bus_adapter_maps_agent_envelope_to_base_event():
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
    assert bus.events[0].payload["correlation"]["execution_id"] == "exec-1"


def test_E6_E7_E8_gate_docs_ci_and_legacy_reference_exist():
    gate_doc = EXIT_GATE_DOC.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    legacy = LEGACY_STATUS_DOC.read_text(encoding="utf-8")

    for criterion in ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"):
        assert f"**{criterion}:**" in gate_doc
    assert "python -m pytest -q" in workflow
    assert "python -m pytest -q se/tests/architecture/test_phase5_10_exit_gate.py" in workflow
    assert "phase5_10/PHASE5_10_EXIT_GATE.md" in legacy
    main_source = MAIN_MODULE.read_text(encoding="utf-8")
    assert "container.agent_runtime = AgentRuntime(" in main_source
    assert "EventBusAgentEventPublisher(container.event_bus)" in main_source
