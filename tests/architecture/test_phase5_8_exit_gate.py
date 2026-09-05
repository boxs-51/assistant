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
EXIT_GATE_DOC = ROOT / "docs" / "phase5" / "phase5_8" / "PHASE5_8_EXIT_GATE.md"
LEGACY_STATUS_DOC = ROOT / "docs" / "phase5" / "PHASE_5_AGENT_RUNTIME_SPEC.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "phase5-8-exit-gate.yml"


def make_context() -> AgentExecutionContext:
    agent = AgentDefinition(
        name="phase5-8-exit-gate-agent",
        goal="phase 5.8 gate",
        instruction="test",
        tools=["tool.a", "tool.b", "tool.c", "tool.d"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-phase5-8-exit-gate",
        agent_id=agent.name,
        session_id="session-phase5-8-exit-gate",
        correlation_id="corr-phase5-8-exit-gate",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=16,
            max_parallel_tools=2,
            max_retry_attempts=1,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        agent=agent,
    )


def request(context: AgentExecutionContext, call_id: str, capability: str) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id=f"inv-{call_id}",
        tool_call_id=call_id,
        capability_id=capability,
        arguments={},
    )


def result_for(req: ToolExecutionRequest, *, execution_id: str | None = None, iteration: int | None = None, capability_id: str | None = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        execution_id=execution_id or req.execution_id,
        iteration=req.iteration if iteration is None else iteration,
        invocation_id=req.invocation_id,
        tool_call_id=req.tool_call_id,
        capability_id=capability_id or req.capability_id,
        success=True,
        output=req.tool_call_id,
    )


@pytest.mark.asyncio
async def test_E1_batch_execution_is_bounded_and_ordered():
    """E1: bounded concurrency and ordered results are preserved."""
    context = make_context()
    active = 0
    peak = 0
    lock = asyncio.Lock()
    requests = [
        request(context, "call-a", "tool.a"),
        request(context, "call-b", "tool.b"),
        request(context, "call-c", "tool.c"),
        request(context, "call-d", "tool.d"),
    ]

    class Executor:
        async def execute(self, context, request):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            try:
                await asyncio.sleep({"call-a": 0.03, "call-b": 0.01, "call-c": 0.02, "call-d": 0.005}[request.tool_call_id])
                return result_for(request)
            finally:
                async with lock:
                    active -= 1

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())
    results = await coordinator.execute_many(context, requests, max_parallel=2)

    assert peak <= 2
    assert [item.tool_call_id for item in results] == ["call-a", "call-b", "call-c", "call-d"]


@pytest.mark.asyncio
async def test_E2_batch_results_preserve_input_order():
    """E2: output order must match input order even when completion order differs."""
    context = make_context()
    requests = [
        request(context, "call-1", "tool.a"),
        request(context, "call-2", "tool.b"),
        request(context, "call-3", "tool.c"),
    ]

    class Executor:
        async def execute(self, context, request):
            await asyncio.sleep({"call-1": 0.05, "call-2": 0.01, "call-3": 0.0}[request.tool_call_id])
            return result_for(request)

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())
    results = await coordinator.execute_many(context, requests, max_parallel=2)

    assert [item.tool_call_id for item in results] == ["call-1", "call-2", "call-3"]


@pytest.mark.asyncio
async def test_E3_duplicate_and_mismatched_batch_results_are_rejected():
    """E3: duplicate and mismatched batch responses fail closed."""
    context = make_context()
    req1 = request(context, "call-1", "tool.a")
    req2 = request(context, "call-2", "tool.b")

    class Executor:
        async def execute(self, context, request):
            if request.tool_call_id == "call-1":
                return result_for(request)
            return result_for(request, capability_id="tool.c")

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())

    with pytest.raises(ValueError, match="capability_id"):
        await coordinator.execute_many(context, [req1, req2], max_parallel=2)

    duplicate = request(context, "call-1", "tool.a")
    duplicate2 = request(context, "call-1", "tool.c")
    with pytest.raises(ValueError, match="Duplicate tool_call_id"):
        await coordinator.execute_many(context, [duplicate, duplicate2], max_parallel=2)


@pytest.mark.asyncio
async def test_E4_retry_policy_respects_retryable_signal_and_budget():
    """E4: retry stops when the retryable signal or budget is exhausted."""
    context = make_context()
    context.limits.max_retry_attempts = 1
    attempts = 0
    req = request(context, "call-r1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("not used")

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
        max_attempts=2,
    )

    result = await coordinator.execute(context, req)

    assert attempts == 2
    assert result.retryable is True
    assert result.metadata["attempt"] == 2


@pytest.mark.asyncio
async def test_E5_cancelled_waiters_do_not_break_shared_batch_execution():
    """E5: cancellation is isolated to the caller, while shared execution remains safe."""
    context = make_context()
    req = request(context, "call-cancel", "tool.a")
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
            return result_for(request)

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("not used")

    coordinator = AgentToolExecutionCoordinator(Executor())
    first = asyncio.create_task(coordinator.execute(context, req))
    await started.wait()
    second = asyncio.create_task(coordinator.execute(context, req))

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert not downstream_cancelled.is_set()

    allow_finish.set()
    result = await asyncio.wait_for(second, timeout=1)

    assert result.output == "call-cancel"
    assert dispatches == 1


def test_E6_ci_declares_full_suite_and_phase_5_8_gate_as_blocking_checks():
    """E6: CI must run the full suite + this gate."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pytest -q" in workflow
    assert "python -m pytest -q tests/architecture/test_phase5_8_exit_gate.py" in workflow
    assert "exit 1" not in workflow
    assert "on:" in workflow
    assert "pull_request:" in workflow


def test_E7_phase_5_8_status_document_is_canonical_and_legacy_doc_points_to_it():
    """E7: the canonical gate doc is the single source of truth for Phase 5.8."""
    gate_doc = EXIT_GATE_DOC.read_text(encoding="utf-8")
    legacy_doc = LEGACY_STATUS_DOC.read_text(encoding="utf-8")

    for criterion in ("E1", "E2", "E3", "E4", "E5", "E6", "E7"):
        assert f"## {criterion}" in gate_doc

    assert "Phase 5.8 Exit Gate" in gate_doc
    assert "PHASE5_8_EXIT_GATE.md" in legacy_doc or "Phase 5.8 Exit Gate" in legacy_doc
