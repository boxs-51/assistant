from __future__ import annotations

import json
from types import MappingProxyType, SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.event import BaseEvent
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
from se.src.runtimes.agent.contracts.loop import AgentLoopState
from se.src.runtimes.agent.contracts.result import AgentExecutionResult
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.serialization import (
    AgentPersistenceSerializationError,
    to_json_safe,
)
from se.src.runtimes.workflow.runtime import WorkflowRuntime


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


class _FrozenContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(InferenceMessage(role="user", content="hello"),),
            metadata={
                "routing": {
                    "client_id": "client-1",
                    "connection_id": "conn-1",
                    "nested": {"values": ("a", "b")},
                }
            },
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


def _context(execution_id="exec-r2-1"):
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=Identity(user_id="user-1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(timeout_seconds=5),
        input={"prompt": "hello"},
    )


def test_json_serializer_deeply_thaws_mappingproxy_and_is_deterministic():
    value = MappingProxyType({
        "routing": MappingProxyType({
            "client_id": "client-1",
            "tags": frozenset({"b", "a"}),
        })
    })

    normalized = to_json_safe(value, path="agent_executions.inference_request")

    assert normalized == {
        "routing": {"client_id": "client-1", "tags": ["a", "b"]}
    }
    json.dumps(normalized)


def test_json_serializer_rejects_non_string_mapping_keys_with_path():
    with pytest.raises(AgentPersistenceSerializationError) as caught:
        to_json_safe({1: "bad"}, path="agent_executions.request")

    assert caught.value.code == "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
    assert caught.value.path == "agent_executions.request"


def test_json_serializer_rejects_unknown_runtime_objects():
    with pytest.raises(AgentPersistenceSerializationError) as caught:
        to_json_safe(
            {"metadata": {"callback": object()}},
            path="agent_executions.context_state",
        )

    assert caught.value.path.endswith("metadata.callback")
    assert caught.value.failure_domain == "AGENT_PERSISTENCE"


@pytest.mark.asyncio
async def test_real_sqlite_agent_checkpoint_accepts_nested_frozen_metadata():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _SqliteUow(sessions))

    runtime = AgentRuntime(
        context_builder=_FrozenContextBuilder(),
        inference=_Inference(),
        tool_execution=None,
        execution_policy=_AllowPolicy(),
        durable_store=store,
    )

    try:
        result = await runtime.execute(_context())
        assert result.state is AgentLoopState.COMPLETED

        async with sessions() as session:
            record = await AgentRepository(session).get_execution("exec-r2-1")
            assert record is not None
            assert record.state == "COMPLETED"
            assert record.inference_request["metadata"]["routing"]["client_id"] == "client-1"
            assert record.inference_request["metadata"]["routing"]["nested"]["values"] == ["a", "b"]
            assert record.transcript
            assert record.inference_response["message"]["content"] == "done"
            json.dumps(record.context_state)
            json.dumps(record.transcript)
            json.dumps(record.inference_request)
            json.dumps(record.inference_response)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_update_preserves_existing_legacy_continuation_member():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _SqliteUow(sessions))

    try:
        await store.save_execution({
            "id": "exec-continuation",
            "session_id": "session-1",
            "agent_id": "agent-1",
            "correlation_id": "corr-1",
            "state": "RUNNING",
            "revision": 1,
            "request": {},
            "context_state": {
                "continuation": {"current_checkpoint_id": "legacy-c1"},
                "metadata": {"old": True},
            },
        })

        await store.update_checkpoint(
            "exec-continuation",
            {"context_state": {"metadata": {"new": True}}},
        )

        record = await store.load_execution("exec-continuation")
        assert record.context_state["continuation"] == {
            "current_checkpoint_id": "legacy-c1"
        }
        assert record.context_state["metadata"] == {"new": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_coordinator_rejects_canonical_waiting_without_reason():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="agent-1", goal="test", instruction="test"))
    identity = Identity(user_id="user-1", auth_type="api_key", scopes={"*"})
    coordinator = MultiAgentCoordinator(registry)
    session = coordinator.create_session(identity, ["agent-1"])
    task = coordinator.create_task(session.session_id, "agent-1", {}, identity)

    async def executor(task):
        return {"state": "WAITING"}

    execution = await coordinator.execute_task(task.task_id, identity, executor)

    assert execution.state.value == "FAILED"
    assert "requires explicit wait_reason" in execution.error


class _RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class _FailedAgentRuntime:
    async def execute(self, context):
        return AgentExecutionResult(
            execution_id=context.execution_id,
            agent_id=context.agent_id,
            state=AgentLoopState.FAILED,
            error_code="AGENT_PERSISTENCE_SERIALIZATION_FAILED",
            error_message="checkpoint serialization failed",
            failure_domain="AGENT_PERSISTENCE",
            retryable=False,
        )


@pytest.mark.asyncio
async def test_workflow_preserves_agent_persistence_failure_domain():
    bus = _RecordingBus()
    agent = AgentDefinition(name="agent-1", goal="test", instruction="test")
    workflow = WorkflowRuntime()
    workflow.event_bus = bus
    workflow.container = SimpleNamespace(
        capability_runtime=SimpleNamespace(catalog=None),
        agent_registry=SimpleNamespace(get=lambda agent_id: agent),
        agent_runtime=_FailedAgentRuntime(),
    )
    identity = Identity(user_id="user-1", auth_type="api_key", scopes={"*"})
    event = BaseEvent(
        event_name="context.event.built",
        session_id="session-1",
        turn_id="turn-1",
        payload={"identity": identity.model_dump(mode="json")},
    )

    await workflow._execute_agent(
        event,
        {
            "agent_id": "agent-1",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {},
            "config": {"stream": False},
        },
    )

    failure = next(item for item in bus.events if item.event_name == "provider.failed")
    assert failure.payload["error_code"] == "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
    assert failure.payload["failure_domain"] == "AGENT_PERSISTENCE"
    assert failure.payload["retryable"] is False