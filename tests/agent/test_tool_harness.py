from __future__ import annotations

import asyncio

import pytest

from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from src.runtimes.capability.runtime import CapabilityRuntime

from .harness import AgentToolLoopHarness, ExecutionTrace, FakeLLM, FakeTool, make_identity


def register_tool(
    runtime: CapabilityRuntime,
    *,
    name: str,
    handler,
    require_auth: bool = False,
    required_scopes: list[str] | None = None,
) -> FakeTool:
    return FakeTool(
        runtime,
        name=name,
        handler=handler,
        require_auth=require_auth,
        required_scopes=required_scopes or [],
    )


@pytest.mark.asyncio
async def test_single_tool_loop_executes_real_capability_runtime():
    runtime = CapabilityRuntime()
    register_tool(runtime, name="calculator.add", handler=lambda a, b: a + b)
    trace = ExecutionTrace()
    llm = FakeLLM([
        {"id": "call-1", "name": "calculator.add", "arguments": {"a": 10, "b": 32}},
        "The answer is 42.",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
        trace=trace,
    ).run("What is 10 + 32?")

    assert result == "The answer is 42."
    assert trace.names == [
        "agent.started",
        "capabilities.available",
        "llm.requested",
        "tool.calls.requested",
        "tool.execution.started",
        "tool.execution.completed",
        "tool.result.appended",
        "llm.requested",
        "agent.completed",
    ]


@pytest.mark.asyncio
async def test_unregistered_tool_is_reported_as_tool_error_and_loop_can_continue():
    runtime = CapabilityRuntime()
    trace = ExecutionTrace()
    llm = FakeLLM([
        {"id": "call-404", "name": "calculator.missing", "arguments": {}},
        "I could not execute that tool.",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
        trace=trace,
    ).run("Use the missing tool.")

    assert result == "I could not execute that tool."
    failed = trace.filter("tool.execution.failed")
    assert failed and failed[0].payload["code"] == "CAPABILITY_NOT_FOUND"


@pytest.mark.asyncio
async def test_permission_denied_is_not_executed():
    calls = 0

    def secret_tool() -> str:
        nonlocal calls
        calls += 1
        return "secret"

    runtime = CapabilityRuntime()
    register_tool(
        runtime,
        name="secret.read",
        handler=secret_tool,
        require_auth=True,
        required_scopes=["secret.read"],
    )
    llm = FakeLLM([
        {"id": "call-deny", "name": "secret.read", "arguments": {}},
        "Access denied.",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(scopes=[]),
    ).run("Read the secret.")

    assert result == "Access denied."
    assert calls == 0


@pytest.mark.asyncio
async def test_retryable_capability_error_retries_then_succeeds():
    state = {"calls": 0}

    async def flaky_tool() -> str:
        state["calls"] += 1
        if state["calls"] < 2:
            from src.runtimes.capability.contracts.error import CapabilityError

            raise CapabilityError(
                code="TRANSIENT",
                message="temporary",
                retryable=True,
            )
        return "ok"

    runtime = CapabilityRuntime()
    register_tool(runtime, name="flaky.read", handler=flaky_tool)
    trace = ExecutionTrace()
    llm = FakeLLM([
        {"id": "call-retry", "name": "flaky.read", "arguments": {}},
        "ok",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
        trace=trace,
    ).run("Read it.")

    assert result == "ok"
    assert state["calls"] == 2
    assert len(trace.filter("tool.execution.retrying")) == 1


@pytest.mark.asyncio
async def test_max_iterations_stops_infinite_tool_loop():
    runtime = CapabilityRuntime()
    register_tool(runtime, name="loop.noop", handler=lambda: "still working")
    llm = FakeLLM([
        {"id": "call-loop-1", "name": "loop.noop", "arguments": {}},
        {"id": "call-loop-2", "name": "loop.noop", "arguments": {}},
        {"id": "call-loop-3", "name": "loop.noop", "arguments": {}},
    ])
    trace = ExecutionTrace()

    with pytest.raises(RuntimeError, match="MAX_ITERATIONS_EXCEEDED"):
        await AgentToolLoopHarness(
            llm=llm,
            capability_runtime=runtime,
            identity=make_identity(),
            trace=trace,
            limits=AgentExecutionLimits(max_iterations=2, max_tool_calls=10),
        ).run("Never finish.")

    assert trace.names[-1] == "agent.failed"
    assert trace.events[-1].payload["code"] == "MAX_ITERATIONS_EXCEEDED"


@pytest.mark.asyncio
async def test_parallel_tool_calls_are_executed_concurrently():
    barrier = asyncio.Barrier(2)

    async def slow_add(value: int) -> int:
        await barrier.wait()
        return value + 1

    runtime = CapabilityRuntime()
    register_tool(runtime, name="parallel.a", handler=slow_add)
    register_tool(runtime, name="parallel.b", handler=slow_add)
    trace = ExecutionTrace()
    llm = FakeLLM([
        [
            {"id": "call-a", "name": "parallel.a", "arguments": {"value": 1}},
            {"id": "call-b", "name": "parallel.b", "arguments": {"value": 2}},
        ],
        "done",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
        trace=trace,
    ).run("Run both tools.")

    assert result == "done"
    assert sorted([e.payload["name"] for e in trace.filter("tool.execution.completed")]) == [
        "parallel.a",
        "parallel.b",
    ]
