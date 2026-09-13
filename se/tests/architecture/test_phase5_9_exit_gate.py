from types import SimpleNamespace
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)
from src.infrastructure.storage.models.sql.base import Base
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.agent.contracts import (
    AgentIteration,
    AgentLoopState,
    AgentExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from src.runtimes.agent.persistence import DurableAgentStore
from src.runtimes.agent.runtime import AgentRuntime

ROOT = Path(__file__).resolve().parents[2]
EXIT_GATE_DOC = ROOT / "docs" / "phase5" / "phase5_9" / "PHASE5_9_EXIT_GATE.md"
LEGACY_STATUS_DOC = ROOT / "docs" / "phase5" / "Agent_Execution_System.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "phase5-9-exit-gate.yml"

def test_E1_execution_plane_models_persist_expected_links():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    execution = AgentExecutionRecord(
        id="exec-1",
        session_id="session-1",
        agent_id="agent-1",
        correlation_id="corr-1",
        state="WAITING_TOOL",
        request={"prompt": "test"},
    )
    session.add(execution)
    session.flush()
    iteration = AgentIterationRecord(
        id="iter-1",
        execution_id=execution.id,
        iteration=1,
        state="WAITING_TOOL",
        tool_call_ids=["call-1"],
    )
    session.add(iteration)
    session.flush()
    call = AgentToolCallRecord(
        id="call-1",
        execution_id=execution.id,
        iteration_id=iteration.id,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        arguments={"left": 1},
        status="COMPLETED",
    )
    session.add(call)
    session.add(
        AgentToolResultRecord(
            id="result-1",
            execution_id=execution.id,
            iteration_id=iteration.id,
            tool_call_id=call.tool_call_id,
            invocation_id=call.invocation_id,
            capability_id=call.capability_id,
            success=True,
            output={"value": 3},
            extra_metadata={"attempt": 1},
        )
    )
    session.commit()

    assert session.get(AgentIterationRecord, "iter-1").execution_id == "exec-1"
    assert session.get(AgentToolCallRecord, "call-1").iteration_id == "iter-1"
    assert session.get(AgentToolResultRecord, "result-1").tool_call_id == "call-1"


class FakeUoW:
    def __init__(self, agents):
        self.agents = agents

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_E2_resume_rehydrates_latest_iteration_and_pending_calls():
    execution = SimpleNamespace(
        id="exec-resume",
        state="WAITING_TOOL",
        session_id="session-1",
        agent_id="agent-1",
        task_id=None,
        correlation_id="corr-1",
        request={"prompt": "resume"},
        result=None,
        error=None,
        context_state={"metadata": {"resume": True}},
        transcript=[{"role": "user", "content": "resume"}],
    )
    latest = SimpleNamespace(id="iter-2", iteration=2)
    pending = SimpleNamespace(
        execution_id="exec-resume",
        iteration_id="iter-2",
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        arguments={"left": 1},
        status="PENDING",
    )

    class Agents:
        async def get_execution(self, execution_id):
            return execution if execution_id == execution.id else None

        async def list_iterations(self, execution_id):
            return [latest]

        async def list_tool_calls(self, execution_id, iteration_id=None):
            return [pending]

    store = DurableAgentStore(lambda: FakeUoW(Agents()))
    state = await store.resume_execution("exec-resume")

    assert state.execution_id == "exec-resume"
    assert state.iteration == 2
    assert state.metadata["resume"] is True
    assert state.resume_transcript[0]["content"] == "resume"
    assert state.resume_pending_tool_calls[0]["invocation_id"] == "inv-1"


@pytest.mark.asyncio
async def test_E3_committed_tool_results_are_not_inserted_twice():
    committed = SimpleNamespace(tool_call_id="call-1")
    saved = []

    class Agents:
        async def get_tool_result(self, execution_id, tool_call_id):
            return committed if saved else None

        async def save_tool_result(self, values):
            saved.append(values)
            return values

    store = DurableAgentStore(lambda: FakeUoW(Agents()))
    values = {
        "id": "result-1",
        "tool_call_id": "call-1",
        "execution_id": "exec-1",
        "iteration_id": "iter-1",
    }
    await store.save_tool_result(values)
    await store.save_tool_result(values)

    assert len(saved) == 1


@pytest.mark.asyncio
async def test_E4_runtime_persists_iteration_calls_and_results():
    persisted = {"iterations": [], "calls": [], "results": []}

    class Store:
        async def load_iteration(self, execution_id, *, iteration_number):
            return None

        async def save_iteration(self, values):
            persisted["iterations"].append(values)

        async def update_iteration(self, iteration_id, values):
            persisted["iterations"].append(values)

        async def save_tool_call(self, values):
            persisted["calls"].append(values)

        async def save_tool_result(self, values):
            persisted["results"].append(values)

    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=Store(),
    )
    iteration = AgentIteration(
        execution_id="exec-1",
        iteration=1,
        state=AgentLoopState.PREPARING,
    )
    request = ToolExecutionRequest(
        execution_id="exec-1",
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        arguments={"left": 1},
    )
    result = ToolExecutionResult(
        execution_id="exec-1",
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        success=True,
        output={"value": 3},
    )

    await runtime._persist_iteration(iteration)
    await runtime._persist_tool_call(request, "exec-1:iteration:1")
    await runtime._persist_tool_result(result, "exec-1:iteration:1")

    assert persisted["iterations"]
    assert persisted["calls"][0]["tool_call_id"] == "call-1"
    assert persisted["results"][0]["invocation_id"] == "inv-1"


@pytest.mark.asyncio
async def test_E4_resumed_committed_tool_result_is_not_dispatched_again():
    context = AgentExecutionContext.create(
        execution_id="exec-restart",
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(max_iterations=4),
    )
    context.iteration = 2
    context.resume_pending_tool_calls = [{
        "execution_id": "exec-restart",
        "iteration": 2,
        "invocation_id": "inv-1",
        "tool_call_id": "call-1",
        "capability_id": "calculator.add",
        "arguments": {"left": 1},
    }]

    class Store:
        async def load_tool_result(self, execution_id, tool_call_id):
            return SimpleNamespace(
                execution_id=execution_id,
                invocation_id="inv-1",
                tool_call_id=tool_call_id,
                capability_id="calculator.add",
                success=True,
                output={"value": 3},
                error_code=None,
                error_message=None,
                retryable=False,
                extra_metadata={"attempt": 1},
            )

        async def save_tool_result(self, values):
            raise AssertionError("committed result must not be written again")

        async def update_checkpoint(self, execution_id, values):
            return None

    class Executor:
        async def execute_many(self, *args, **kwargs):
            raise AssertionError("committed tool call must not be dispatched")

    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=Executor(),
        execution_policy=None,
        durable_store=Store(),
    )
    results = await runtime._execute_resumed_tool_calls(context)

    assert len(results) == 1
    assert results[0].output == {"value": 3}


def test_E5_ci_declares_full_suite_and_phase_5_9_gate():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow
    assert "python -m pytest -q tests/architecture/test_phase5_9_exit_gate.py" in workflow
    assert "pull_request:" in workflow


def test_E6_canonical_gate_is_referenced_by_legacy_status_doc():
    gate_doc = EXIT_GATE_DOC.read_text(encoding="utf-8")
    legacy_doc = LEGACY_STATUS_DOC.read_text(encoding="utf-8")
    assert "Phase 5.9 Exit Gate" in gate_doc
    assert "phase5_9/PHASE5_9_EXIT_GATE.md" in legacy_doc
