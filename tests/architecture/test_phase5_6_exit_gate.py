from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.agent.contracts import (
    AgentExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from src.runtimes.agent.tool_execution import AgentToolExecutionCoordinator


ROOT = Path(__file__).resolve().parents[2]
EXIT_GATE_DOC = ROOT / "docs" / "phase5" / "phase5_6" / "PHASE5_6_EXIT_GATE.md"
LEGACY_STATUS_DOC = ROOT / "docs" / "phase5" / "Agent_Execution_System.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "phase5-6-exit-gate.yml"


def make_context(
    *,
    timeout_seconds: float = 5,
    max_retry_attempts: int = 0,
) -> AgentExecutionContext:
    agent = AgentDefinition(
        name="phase5-6-exit-gate-agent",
        goal="phase 5.6 gate",
        instruction="test",
        tools=["tool.a", "tool.b"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-phase5-6-exit-gate",
        agent_id=agent.name,
        session_id="session-phase5-6-exit-gate",
        correlation_id="corr-phase5-6-exit-gate",
        identity=Identity(
            user_id="u1",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=16,
            max_parallel_tools=4,
            max_retry_attempts=max_retry_attempts,
            timeout_seconds=timeout_seconds,
            tool_timeout_seconds=1,
        ),
        agent=agent,
    )


def make_request(
    context: AgentExecutionContext,
    *,
    call_id: str,
    invocation_id: str | None = None,
    capability_id: str = "tool.a",
) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id=invocation_id or f"inv-{call_id}",
        tool_call_id=call_id,
        capability_id=capability_id,
        arguments={},
    )


def make_result(request: ToolExecutionRequest) -> ToolExecutionResult:
    return ToolExecutionResult(
        execution_id=request.execution_id,
        iteration=request.iteration,
        invocation_id=request.invocation_id,
        tool_call_id=request.tool_call_id,
        capability_id=request.capability_id,
        success=True,
        output=f"result:{request.tool_call_id}",
    )


@pytest.mark.asyncio
async def test_E1_caller_cancellation_detaches_waiter_but_shared_execution_continues():
    """
    E1: cancelling one caller must not cancel the shared invocation while
    another caller still waits for the same execution.
    """
    context = make_context()
    request = make_request(context, call_id="call-e1")

    started = asyncio.Event()
    allow_finish = asyncio.Event()
    downstream_cancelled = asyncio.Event()
    dispatches = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches
            dispatches += 1
            started.set()
            try:
                await allow_finish.wait()
            except asyncio.CancelledError:
                downstream_cancelled.set()
                raise
            return make_result(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = asyncio.create_task(coordinator.execute(context, request))
    await started.wait()
    second = asyncio.create_task(coordinator.execute(context, request))

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert not downstream_cancelled.is_set()

    allow_finish.set()
    result = await asyncio.wait_for(second, timeout=1)

    assert result.output == "result:call-e1"
    assert dispatches == 1
    assert not downstream_cancelled.is_set()


@pytest.mark.asyncio
async def test_E2_execution_cancellation_cancels_shared_task_once_for_all_waiters():
    """
    E2: execution-level cancellation is stronger than caller detachment:
    every waiter observes cancellation, while downstream sees one execution
    cancellation rather than one cancellation per waiter.
    """
    context = make_context()
    request = make_request(context, call_id="call-e2")

    started = asyncio.Event()
    downstream_cancelled = asyncio.Event()
    dispatches = 0
    downstream_cancel_count = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches, downstream_cancel_count
            dispatches += 1
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                downstream_cancel_count += 1
                downstream_cancelled.set()
                raise

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = asyncio.create_task(coordinator.execute(context, request))
    second = asyncio.create_task(coordinator.execute(context, request))
    await started.wait()

    context.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(asyncio.CancelledError):
        await second

    await asyncio.wait_for(downstream_cancelled.wait(), timeout=1)

    assert dispatches == 1
    assert downstream_cancel_count == 1
    assert not coordinator._inflight


@pytest.mark.asyncio
async def test_E3_completed_shared_execution_is_committed_even_when_last_waiter_cancels():
    """
    E3: if the downstream task has already completed, a late caller
    cancellation must not lose the completed idempotency record.
    """
    context = make_context()
    request = make_request(context, call_id="call-e3")

    dispatches = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches
            dispatches += 1
            await asyncio.sleep(0)
            return make_result(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    original_wait = coordinator._await_with_cancellation

    async def cancel_after_shared_completion(
        execution_context,
        task,
    ):
        result = await original_wait(
            execution_context,
            task,
        )
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        return result

    coordinator._await_with_cancellation = cancel_after_shared_completion

    caller = asyncio.create_task(
        coordinator.execute(context, request)
    )

    with pytest.raises(asyncio.CancelledError):
        await caller

    assert dispatches == 1

    # Restore normal waiting for the post-race verification.
    coordinator._await_with_cancellation = original_wait

    result = await coordinator.execute(context, request)

    assert result.output == "result:call-e3"
    assert dispatches == 1
    assert (context.execution_id, request.invocation_id) in coordinator._completed


@pytest.mark.asyncio
async def test_E4_completion_or_cancellation_race_never_redispatches_same_invocation():
    """
    E4: a new caller arriving after the race must reuse the completed result,
    not execute the side effect again.
    """
    context = make_context()
    request = make_request(
        context,
        call_id="call-e4",
        invocation_id="stable-invocation-e4",
    )

    dispatches = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches
            dispatches += 1
            await asyncio.sleep(0)
            return make_result(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    original_wait = coordinator._await_with_cancellation

    async def cancel_after_shared_completion(
        execution_context,
        task,
    ):
        result = await original_wait(
            execution_context,
            task,
        )
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        return result

    coordinator._await_with_cancellation = cancel_after_shared_completion

    cancelled_caller = asyncio.create_task(
        coordinator.execute(context, request)
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller

    coordinator._await_with_cancellation = original_wait

    reused = await coordinator.execute(context, request)

    assert reused.output == "result:call-e4"
    assert dispatches == 1


def test_E5_ci_declares_full_suite_and_exit_gate_as_blocking_checks():
    """
    E5: CI must run the entire test suite plus this exit-gate suite.

    The green GitHub Actions run itself is the external evidence for the
    final E5 release decision; this test verifies the CI contract is present.
    """
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pytest -q" in workflow
    assert (
        "python -m pytest -q tests/architecture/test_phase5_6_exit_gate.py"
        in workflow
    )
    assert "exit 1" not in workflow
    assert "on:" in workflow
    assert "pull_request:" in workflow


@pytest.mark.asyncio
async def test_E6_completed_ledger_has_bounded_retention():
    """
    E6: the in-memory completed ledger must have an explicit bounded
    retention policy so a long-lived gateway cannot grow it without limit.
    """
    context = make_context()

    class Executor:
        async def execute(self, context, request):
            return make_result(request)

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        max_completed_entries=2,
    )

    for index in range(3):
        request = make_request(
            context,
            call_id=f"call-e6-{index}",
            invocation_id=f"inv-e6-{index}",
        )
        await coordinator.execute(context, request)

    assert len(coordinator._completed) <= 2
    assert (
        context.execution_id,
        "inv-e6-0",
    ) not in coordinator._completed


def test_E7_phase_5_6_status_document_is_canonical_and_legacy_status_declares_it():
    """
    E7: there is one canonical Phase 5.6 exit-gate document and the older
    status report explicitly points readers to it instead of publishing a
    contradictory stale matrix.
    """
    gate_doc = EXIT_GATE_DOC.read_text(encoding="utf-8")
    legacy_doc = LEGACY_STATUS_DOC.read_text(encoding="utf-8")

    for criterion in ("E1", "E2", "E3", "E4", "E5", "E6", "E7"):
        assert f"## {criterion}" in gate_doc

    assert "Phase 5.6 Exit Gate" in gate_doc
    assert "Canonical current status" in legacy_doc
    assert "PHASE5_6_EXIT_GATE.md" in legacy_doc