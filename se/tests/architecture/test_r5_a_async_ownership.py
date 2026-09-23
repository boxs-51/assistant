from __future__ import annotations

import asyncio
import gc

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import InferenceRequest
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.capability.contracts.context import (
    CapabilityExecutionContext,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


def _identity() -> Identity:
    return Identity(
        user_id="r5-a-user",
        auth_type="api_key",
        scopes={"*"},
    )


def _agent_context(
    execution_id: str = "exec-r5-a",
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r5-a",
        session_id="session-r5-a",
        correlation_id="corr-r5-a",
        identity=_identity(),
        limits=AgentExecutionLimits(
            timeout_seconds=5,
            inference_timeout_seconds=5,
            tool_timeout_seconds=5,
        ),
        input={"prompt": "test"},
    )


@pytest.mark.asyncio
async def test_provider_outer_cancellation_drains_provider_task_and_retrieves_late_error():
    started = asyncio.Event()
    child_tasks: list[asyncio.Task] = []

    class SlowHandler:
        async def execute_with_fallback(self, http_client, body, *, timeout=None):
            child = asyncio.current_task()
            assert child is not None
            child_tasks.append(child)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Simulate a provider cleanup path that fails after receiving
                # cancellation.  The adapter must still retrieve this error.
                raise RuntimeError("late provider cleanup failure")

    class Runtime:
        chat_handler = SlowHandler()

    adapter = ProviderInferenceAdapter(Runtime(), object())
    request = InferenceRequest(
        request_id="req-r5-a-provider",
        execution_id="exec-r5-a-provider",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=30,
    )

    loop = asyncio.get_running_loop()
    observed: list[dict] = []
    previous_handler = loop.get_exception_handler()

    def capture_loop_error(_loop, context):
        observed.append(context)

    loop.set_exception_handler(capture_loop_error)
    outer = asyncio.create_task(adapter.complete(request))

    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        outer.cancel()

        with pytest.raises(asyncio.CancelledError):
            await outer

        assert len(child_tasks) == 1
        child = child_tasks.pop()
        assert child.done()

        # Do not call child.exception(): that would retrieve the exception in
        # the test and hide the exact regression this test is protecting.
        del child
        gc.collect()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not any(
            str(item.get("exception")) == "late provider cleanup failure"
            or item.get("message") == "Task exception was never retrieved"
            for item in observed
        )
    finally:
        loop.set_exception_handler(previous_handler)
        if not outer.done():
            outer.cancel()
            await asyncio.gather(outer, return_exceptions=True)
        for child in child_tasks:
            if not child.done():
                child.cancel()
            await asyncio.gather(child, return_exceptions=True)


@pytest.mark.asyncio
async def test_capability_driver_outer_cancellation_drains_driver_task():
    started = asyncio.Event()
    cleaned = asyncio.Event()

    class Driver:
        async def execute(self, context, arguments):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-r5-a-capability",
        invocation_id="inv-r5-a-capability",
        timeout_seconds=30,
    )

    outer = asyncio.create_task(
        CapabilityRuntime._execute_driver_once(
            Driver(),
            context,
            capability_id="cap.r5-a",
            arguments={},
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    await asyncio.wait_for(cleaned.wait(), timeout=1)


@pytest.mark.asyncio
async def test_agent_runtime_outer_cancellation_drains_contextual_child():
    context = _agent_context("exec-r5-a-contextual")
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def child():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    outer = asyncio.create_task(
        AgentRuntime._await_contextual(
            child(),
            context=context,
            timeout_seconds=30,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    await asyncio.wait_for(cleaned.wait(), timeout=1)


@pytest.mark.asyncio
async def test_tool_batch_gather_outer_cancellation_drains_gather_and_children():
    class NeverUsedExecutor:
        async def execute(self, context, request):
            raise AssertionError("not used")

    coordinator = AgentToolExecutionCoordinator(NeverUsedExecutor())
    context = _agent_context("exec-r5-a-tool-batch")
    started = [asyncio.Event(), asyncio.Event()]
    cleaned = [asyncio.Event(), asyncio.Event()]

    async def child(index: int):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned[index].set()

    tasks = [
        asyncio.create_task(child(0), name="r5-a-child-0"),
        asyncio.create_task(child(1), name="r5-a-child-1"),
    ]
    await asyncio.gather(*(event.wait() for event in started))

    outer = asyncio.create_task(
        coordinator._gather_with_cancellation(context, tasks)
    )
    await asyncio.sleep(0)

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    for event in cleaned:
        await asyncio.wait_for(event.wait(), timeout=1)
    assert all(task.done() for task in tasks)
