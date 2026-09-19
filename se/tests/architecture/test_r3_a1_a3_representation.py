from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import (
    AgentExecution,
    AgentExecutionLimits,
    AgentExecutionState,
    AgentExecutionWaitReason,
)
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.events import AgentEventName
from se.src.runtimes.agent.events import EventBusAgentEventPublisher
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime


class _SqliteUow:
    def __init__(self, sessions) -> None:
        self._sessions = sessions
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self.session = self._sessions()
        self.agents = AgentRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


class _RecordingBus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event):
        self.events.append(event)
        return None


def _identity() -> Identity:
    return Identity(
        user_id="user-r3",
        auth_type="api_key",
        scopes={"*"},
    )


def _context(execution_id: str = "exec-r3-a") -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r3",
        session_id="session-r3",
        correlation_id="corr-r3",
        identity=_identity(),
        limits=AgentExecutionLimits(timeout_seconds=5),
        request_id="request-r3",
        task_id="task-r3",
        branch_id="branch-r3",
        parent_execution_id="parent-r3",
        retry_of_execution_id="retry-r3",
        base_execution_id="base-r3",
        base_checkpoint_id="checkpoint-r3",
        workflow_id="workflow-r3",
        causation_id="invocation-r3",
        trace_id="trace-r3",
        input={"prompt": "hello"},
    )


def test_a1_domain_and_context_keep_independent_lineage_dimensions():
    execution = AgentExecution(
        execution_id="exec-r3-a",
        session_id="session-r3",
        agent_id="agent-r3",
        task_id="task-r3",
        branch_id="branch-r3",
        parent_execution_id="parent-r3",
        retry_of_execution_id="retry-r3",
        base_execution_id="base-r3",
        base_checkpoint_id="checkpoint-r3",
        correlation_id="corr-r3",
        state=AgentExecutionState.WAITING,
        wait_reason=AgentExecutionWaitReason.DEPENDENCY,
        created_at=1.0,
        updated_at=2.0,
    )
    context = _context()

    assert execution.branch_id == "branch-r3"
    assert execution.parent_execution_id == "parent-r3"
    assert execution.retry_of_execution_id == "retry-r3"
    assert execution.base_execution_id == "base-r3"
    assert execution.base_checkpoint_id == "checkpoint-r3"

    assert context.branch_id == "branch-r3"
    assert context.parent_execution_id == "parent-r3"
    assert context.retry_of_execution_id == "retry-r3"
    assert context.base_execution_id == "base-r3"
    assert context.base_checkpoint_id == "checkpoint-r3"
    assert context.causation_id == "invocation-r3"
    assert context.trace_id == "trace-r3"

@pytest.mark.asyncio
async def _sqlite_store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, DurableAgentStore(lambda: _SqliteUow(sessions))

@pytest.mark.asyncio
async def test_a2_durable_store_round_trips_execution_lineage():
    engine, store = await _sqlite_store()
    try:
        await store.save_execution(
            {
                "id": "exec-r3-durable",
                "session_id": "session-r3",
                "agent_id": "agent-r3",
                "task_id": "task-r3",
                "branch_id": "branch-r3",
                "parent_execution_id": "parent-r3",
                "retry_of_execution_id": "retry-r3",
                "base_execution_id": "base-r3",
                "base_checkpoint_id": "checkpoint-r3",
                "correlation_id": "corr-r3",
                "state": AgentExecutionState.WAITING.value,
                "wait_reason": AgentExecutionWaitReason.DEPENDENCY.value,
                "revision": 7,
                "request": {"prompt": "hello"},
                "context_state": {
                    "request_id": "request-r3",
                    "workflow_id": "workflow-r3",
                    "causation_id": "invocation-r3",
                    "trace_id": "trace-r3",
                    "metadata": {"source": "r3-a2"},
                    "limits": AgentExecutionLimits(
                        timeout_seconds=5
                    ).model_dump(mode="json"),
                },
            }
        )

        record = await store.load_execution("exec-r3-durable")
        assert record.branch_id == "branch-r3"
        assert record.retry_of_execution_id == "retry-r3"
        assert record.base_execution_id == "base-r3"
        assert record.base_checkpoint_id == "checkpoint-r3"

        resumed = await store.resume_execution(
            "exec-r3-durable",
            identity=_identity(),
        )
        assert resumed is not None
        assert resumed.task_id == "task-r3"
        assert resumed.branch_id == "branch-r3"
        assert resumed.parent_execution_id == "parent-r3"
        assert resumed.retry_of_execution_id == "retry-r3"
        assert resumed.base_execution_id == "base-r3"
        assert resumed.base_checkpoint_id == "checkpoint-r3"
        assert resumed.request_id == "request-r3"
        assert resumed.workflow_id == "workflow-r3"
        assert resumed.causation_id == "invocation-r3"
        assert resumed.trace_id == "trace-r3"
        assert resumed.resume_revision == 7
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_a3_agent_events_expose_task_and_branch_correlation():
    bus = _RecordingBus()
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        event_publisher=EventBusAgentEventPublisher(bus),
    )

    await runtime._publish(
        AgentEventName.EXECUTION_STARTED,
        _context("exec-r3-event"),
    )

    assert len(bus.events) == 1
    correlation = bus.events[0].payload["correlation"]
    assert correlation["execution_id"] == "exec-r3-event"
    assert correlation["task_id"] == "task-r3"
    assert correlation["branch_id"] == "branch-r3"
    assert correlation["parent_execution_id"] == "parent-r3"
    assert correlation["correlation_id"] == "corr-r3"
    assert correlation["trace_id"] == "trace-r3"
    assert correlation["causation_id"] == "invocation-r3"
