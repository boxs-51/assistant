from __future__ import annotations

import json
from types import MappingProxyType

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts import (
    AgentContextSnapshot,
    AgentExecutionContext,
    InferenceMessage,
    InferenceResponse,
    PolicyDecision,
)
from se.src.runtimes.agent.contracts.events import AgentEventName
from se.src.runtimes.agent.contracts.loop import AgentLoopState
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.serialization import (
    AgentPersistenceSerializationError,
    to_json_safe,
)


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


class _AllowPolicy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW


class _ContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(InferenceMessage(role="user", content="hello"),),
            metadata={"routing": {"values": ("a", "b")}},
        )


class _CountingInference:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(role="assistant", content="done"),
            provider="test-provider",
            model="test-model",
        )


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class _CheckpointFailingStore(DurableAgentStore):
    def __init__(self, uow_factory) -> None:
        super().__init__(uow_factory)
        self.checkpoint_attempted = False

    async def update_checkpoint(self, execution_id: str, values: dict):
        self.checkpoint_attempted = True
        raise AgentPersistenceSerializationError(
            "agent_executions.inference_request.metadata.callback",
            object(),
            "forced persistence-boundary failure",
        )


def _context(execution_id: str) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=Identity(user_id="user-1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(timeout_seconds=5),
        input={"prompt": "hello"},
    )


async def _sqlite_store(store_type=DurableAgentStore):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions, store_type(lambda: _SqliteUow(sessions))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_json_serializer_rejects_non_finite_float(value):
    with pytest.raises(AgentPersistenceSerializationError) as caught:
        to_json_safe(
            {"value": value},
            path="agent_executions.context_state",
        )
    assert caught.value.code == "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
    assert caught.value.path.endswith(".value")


@pytest.mark.asyncio
async def test_successful_durable_execution_keeps_r2_revision_contract():
    engine, sessions, store = await _sqlite_store()
    inference = _CountingInference()
    runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=None,
        execution_policy=_AllowPolicy(),
        durable_store=store,
    )
    try:
        result = await runtime.execute(_context("exec-r2-1-revision"))
        assert result.state is AgentLoopState.COMPLETED
        assert inference.calls == 1
        record = await store.load_execution("exec-r2-1-revision")
        assert record.state == "COMPLETED"
        assert record.revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successful_inference_then_persistence_failure_is_classified_and_durable():
    engine, sessions, store = await _sqlite_store(_CheckpointFailingStore)
    inference = _CountingInference()
    publisher = _RecordingPublisher()
    runtime = AgentRuntime(
        context_builder=_ContextBuilder(),
        inference=inference,
        tool_execution=None,
        execution_policy=_AllowPolicy(),
        durable_store=store,
        event_publisher=publisher,
    )
    try:
        result = await runtime.execute(_context("exec-r2-1-persistence-failure"))

        assert inference.calls == 1
        assert store.checkpoint_attempted is True
        assert result.state is AgentLoopState.FAILED
        assert result.error_code == "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
        assert result.failure_domain == "AGENT_PERSISTENCE"
        assert result.retryable is False

        record = await store.load_execution("exec-r2-1-persistence-failure")
        assert record.state == "FAILED"
        assert record.revision == 2
        assert "AGENT_PERSISTENCE_SERIALIZATION_FAILED" in record.error

        failure_event = next(
            event
            for event in publisher.events
            if event.event_name == AgentEventName.EXECUTION_FAILED
        )
        assert failure_event.payload["error_code"] == "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
        assert failure_event.payload["failure_domain"] == "AGENT_PERSISTENCE"
        assert failure_event.payload["retryable"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_representative_agent_json_surfaces_normalize_nested_immutable_values():
    engine, sessions, store = await _sqlite_store()
    frozen = MappingProxyType(
        {"nested": MappingProxyType({"values": ("a", "b")})}
    )
    try:
        task = await store.save_task(
            {
                "id": "task-json",
                "session_id": "session-1",
                "created_by": "user-1",
                "assigned_agent_id": "agent-1",
                "wait_reasons": ["CONNECTION"],
                "input": frozen,
                "output": frozen,
            }
        )
        message = await store.save_message(
            {
                "id": "message-json",
                "session_id": "session-1",
                "sender_id": "user-1",
                "message_type": "USER",
                "payload": frozen,
            }
        )
        await store.save_execution(
            {
                "id": "exec-json-surfaces",
                "session_id": "session-1",
                "agent_id": "agent-1",
                "correlation_id": "corr-json",
                "state": "RUNNING",
                "revision": 1,
                "request": {},
                "context_state": {},
            }
        )
        await store.save_iteration(
            {
                "id": "exec-json-surfaces:iteration:1",
                "execution_id": "exec-json-surfaces",
                "iteration": 1,
                "state": "WAITING_TOOL",
            }
        )
        tool_call = await store.save_tool_call(
            {
                "id": "call-json",
                "execution_id": "exec-json-surfaces",
                "iteration_id": "exec-json-surfaces:iteration:1",
                "invocation_id": "inv-json",
                "tool_call_id": "call-json",
                "capability_id": "tool-json",
                "arguments": frozen,
                "status": "PENDING",
                "extra_metadata": frozen,
            }
        )
        tool_result = await store.save_tool_result(
            {
                "id": "result-json",
                "execution_id": "exec-json-surfaces",
                "iteration_id": "exec-json-surfaces:iteration:1",
                "tool_call_id": "call-json",
                "invocation_id": "inv-json",
                "capability_id": "tool-json",
                "success": True,
                "output": frozen,
                "extra_metadata": frozen,
                "attempt": 1,
            }
        )

        expected = {"nested": {"values": ["a", "b"]}}
        assert task.input == expected
        assert task.output == expected
        assert message.payload == expected
        assert tool_call.arguments == expected
        assert tool_call.extra_metadata == expected
        assert tool_result.output == expected
        assert tool_result.extra_metadata == expected
        json.dumps(expected)
    finally:
        await engine.dispose()
