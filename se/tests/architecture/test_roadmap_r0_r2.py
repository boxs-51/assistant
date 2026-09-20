from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cl.src.core.gateway_client import normalize_waiting_payload
from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import (
    AgentExecution,
    AgentExecutionLimits,
    AgentExecutionState,
    AgentExecutionWaitReason,
)
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent import AgentExecutionRecord
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    InferenceMessage,
    InferenceResponse,
    PolicyDecision,
)
from se.src.runtimes.agent.persistence import ExecutionConflictError
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.state_machine import AgentExecutionStateMachine


def _execution(**updates) -> AgentExecution:
    values = {
        "execution_id": "exec-1",
        "session_id": "session-1",
        "agent_id": "agent-1",
        "correlation_id": "corr-1",
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    values.update(updates)
    return AgentExecution(**values)


def _context(execution_id: str = "exec-1") -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=Identity(user_id="user-1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(timeout_seconds=5),
        input={"prompt": "hello"},
    )


def test_r1_waiting_contract_and_legacy_normalization() -> None:
    execution = _execution(state="WAITING_AGENT")
    assert execution.state is AgentExecutionState.WAITING
    assert execution.wait_reason is AgentExecutionWaitReason.AGENT
    assert execution.model_dump(mode="json")["state"] == "WAITING"

    with pytest.raises(ValueError, match="requires wait_reason"):
        _execution(state="WAITING")
    with pytest.raises(ValueError, match="only valid"):
        _execution(state="RUNNING", wait_reason="CONNECTION")

    assert normalize_waiting_payload(
        {"status": "WAITING_FOR_CONNECTION", "execution_id": "exec-1"}
    ) == {
        "status": "WAITING",
        "wait_reason": "CONNECTION",
        "execution_id": "exec-1",
    }
    assert normalize_waiting_payload(
        {"status": "WAITING", "wait_reason": "HUMAN_APPROVAL"}
    )["wait_reason"] == "HUMAN_APPROVAL"


def test_r1_terminal_execution_cannot_restart() -> None:
    for state in (
        AgentExecutionState.COMPLETED,
        AgentExecutionState.FAILED,
        AgentExecutionState.CANCELLED,
        AgentExecutionState.TIMEOUT,
    ):
        assert not AgentExecutionStateMachine.can_transition(
            state,
            AgentExecutionState.RUNNING,
        )


@pytest.mark.asyncio
async def test_r1_coordinator_exposes_canonical_waiting_task_and_execution() -> None:
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="agent-1", goal="test", instruction="test"))
    identity = Identity(user_id="user-1", auth_type="api_key", scopes={"*"})
    coordinator = MultiAgentCoordinator(registry)
    session = coordinator.create_session(identity, ["agent-1"])
    task = coordinator.create_task(session.session_id, "agent-1", {}, identity)

    async def executor(
        task,
        *,
        identity,
        execution_id,
        correlation_id,
        parent_execution_id,
    ):
        return {
            "execution_id": execution_id,
            "state": "WAITING",
            "wait_reason": "CONNECTION",
            "error_code": "WAITING_FOR_CONNECTION",
        }

    execution = await coordinator.execute_task(task.task_id, identity, executor)

    assert execution.state is AgentExecutionState.WAITING
    assert execution.wait_reason is AgentExecutionWaitReason.CONNECTION
    assert task.status.value == "WAITING"
    assert task.wait_reasons == ["CONNECTION"]


@pytest.mark.asyncio
async def test_r2_durable_coordinator_rejects_non_runtime_executor() -> None:
    class Store:
        async def update_task(self, task_id, values):
            return values

    registry = AgentRegistry()
    registry.register(AgentDefinition(name="agent-1", goal="test", instruction="test"))
    identity = Identity(user_id="user-1", auth_type="api_key", scopes={"*"})
    coordinator = MultiAgentCoordinator(registry, durable_store=Store())
    session = coordinator.create_session(identity, ["agent-1"])
    task = coordinator.create_task(session.session_id, "agent-1", {}, identity)
    called = False

    async def legacy_executor(task):
        nonlocal called
        called = True
        return {"output": "unsafe"}

    execution = await coordinator.execute_task(task.task_id, identity, legacy_executor)

    assert not called
    assert execution.state is AgentExecutionState.FAILED
    assert "AgentRuntime-owned" in execution.error


class _MemoryExecutionStore:
    def __init__(self, record=None) -> None:
        self.record = record
        self.iterations = []
        self._lock = asyncio.Lock()

    async def load_execution(self, execution_id):
        await asyncio.sleep(0)
        if self.record is None or self.record.id != execution_id:
            return None
        return SimpleNamespace(**vars(self.record))

    async def save_execution(self, values):
        async with self._lock:
            if self.record is not None:
                raise ExecutionConflictError("duplicate")
            self.record = SimpleNamespace(**values)
            return self.record

    async def compare_and_set_execution(self, execution_id, expected_revision, values):
        async with self._lock:
            if (
                self.record is None
                or self.record.id != execution_id
                or self.record.revision != expected_revision
            ):
                raise ExecutionConflictError("stale")
            for key, value in values.items():
                setattr(self.record, key, value)
            self.record.revision += 1
            return self.record

    async def load_iteration(self, execution_id, *, iteration_number):
        return None

    async def save_iteration(self, values):
        assert self.record is not None
        assert self.record.state == "RUNNING"
        self.iterations.append(values)

    async def update_iteration(self, iteration_id, values):
        self.iterations.append(values)

    async def update_checkpoint(self, execution_id, values):
        return None


class _AllowPolicy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


class _Builder:
    def __init__(self, store):
        self.store = store

    async def build(self, context, request):
        assert self.store.record is not None
        assert self.store.record.state == "RUNNING"
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(InferenceMessage(role="user", content="hello"),),
        )


class _Inference:
    async def complete(self, request):
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(role="assistant", content="done"),
            provider="test",
            model="test",
        )


@pytest.mark.asyncio
async def test_r2_runtime_creates_before_iteration_and_atomically_completes() -> None:
    store = _MemoryExecutionStore()
    runtime = AgentRuntime(
        context_builder=_Builder(store),
        inference=_Inference(),
        tool_execution=None,
        execution_policy=_AllowPolicy(),
        durable_store=store,
    )

    result = await runtime.execute(_context())

    assert result.state.value == "COMPLETED"
    assert store.iterations
    assert store.record.state == "COMPLETED"
    assert store.record.revision == 2
    assert store.record.result["execution_id"] == "exec-1"
    assert store.record.completed_at is not None


@pytest.mark.asyncio
async def test_r2_two_resume_claims_have_exactly_one_winner() -> None:
    store = _MemoryExecutionStore(
        SimpleNamespace(
            id="exec-resume",
            state="WAITING",
            wait_reason="CONNECTION",
            revision=7,
            remaining_active_budget_seconds=5.0,
            wait_expires_at=None,
        )
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )
    first = _context("exec-resume")
    second = _context("exec-resume")
    first.resume_revision = second.resume_revision = 7

    outcomes = await asyncio.gather(
        runtime._begin_durable_execution(first),
        runtime._begin_durable_execution(second),
        return_exceptions=True,
    )

    assert sum(isinstance(item, int) for item in outcomes) == 1
    assert sum(isinstance(item, ExecutionConflictError) for item in outcomes) == 1
    assert store.record.state == "RUNNING"
    assert store.record.revision == 8


@pytest.mark.asyncio
async def test_r2_stale_resume_revision_is_rejected() -> None:
    store = _MemoryExecutionStore(
        SimpleNamespace(
            id="exec-resume",
            state="WAITING",
            wait_reason="CONNECTION",
            revision=3,
        )
    )
    context = _context("exec-resume")
    context.resume_revision = 2
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )

    with pytest.raises(ExecutionConflictError, match="Stale"):
        await runtime._begin_durable_execution(context)
    assert store.record.state == "WAITING"
    assert store.record.revision == 3


@pytest.mark.asyncio
async def test_r2_duplicate_execution_start_is_rejected() -> None:
    store = _MemoryExecutionStore(
        SimpleNamespace(
            id="exec-running",
            state="RUNNING",
            wait_reason=None,
            revision=1,
        )
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )

    with pytest.raises(ExecutionConflictError, match="not resumable WAITING"):
        await runtime._begin_durable_execution(_context("exec-running"))
    assert store.record.revision == 1


@pytest.mark.asyncio
async def test_r2_sql_repository_cas_rejects_stale_revision() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            session.add(
                AgentExecutionRecord(
                    id="exec-sql-cas",
                    session_id="session-1",
                    agent_id="agent-1",
                    correlation_id="corr-1",
                    state="WAITING",
                    wait_reason="CONNECTION",
                    revision=4,
                    request={},
                )
            )
            await session.commit()

        async with sessions() as winner_session:
            winner = await AgentRepository(winner_session).compare_and_set_execution(
                "exec-sql-cas",
                4,
                {"state": "RUNNING", "wait_reason": None},
            )
            await winner_session.commit()
            assert winner is not None
            assert winner.revision == 5

        async with sessions() as stale_session:
            stale = await AgentRepository(stale_session).compare_and_set_execution(
                "exec-sql-cas",
                4,
                {"state": "CANCELLED", "wait_reason": None},
            )
            assert stale is None
    finally:
        await engine.dispose()
