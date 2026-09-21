from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import CapabilityInvocationRecord
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionContext,
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)
from se.src.runtimes.agent.runtime import AgentRuntime


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _schema(tmp_path, name):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _invocation(*, state="RUNNING", remote_state="OUTCOME_UNKNOWN", output=None, revision=1):
    return CapabilityInvocationRecord(
        invocation_id="inv-r7-c",
        capability_id="tool.remote",
        capability_version="7.2",
        kind="TOOL",
        execution_mode="ONE_SHOT",
        idempotency="DEDUPLICATED",
        request_fingerprint="b" * 64,
        owner_user_id="user-r7-c",
        origin_client_id="client-r7-c",
        remote_outcome_state=remote_state,
        state=state,
        session_id="session-r7-c",
        execution_id="exec-r7-c",
        tool_call_id="call-r7-c",
        connection_id="conn-r7-c",
        arguments={"value": 1},
        output=output,
        revision=revision,
    )


@pytest.mark.asyncio
async def test_r7_c_provisional_never_loads_and_terminal_r6_promotes_exact_result_once(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-commit.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="RUNNING",
                    revision=1,
                    request={},
                )
            )
            uow.session.add(
                AgentIterationRecord(
                    id="exec-r7-c:iteration:1",
                    execution_id="exec-r7-c",
                    iteration=1,
                    state="WAITING_TOOL",
                    tool_call_ids=["call-r7-c"],
                )
            )
            uow.session.add(
                AgentToolCallRecord(
                    id="call-r7-c",
                    execution_id="exec-r7-c",
                    iteration_id="exec-r7-c:iteration:1",
                    invocation_id="inv-r7-c",
                    tool_call_id="call-r7-c",
                    capability_id="tool.remote",
                    arguments={"value": 1},
                    status="PENDING",
                )
            )
            uow.session.add(_invocation())
            await uow.commit()

        row = await store.save_tool_result(
            {
                "id": "exec-r7-c:call-r7-c",
                "execution_id": "exec-r7-c",
                "iteration_id": "exec-r7-c:iteration:1",
                "tool_call_id": "call-r7-c",
                "invocation_id": "inv-r7-c",
                "capability_id": "tool.remote",
                "success": False,
                "output": {"transport": "unknown"},
                "error_code": "REMOTE_OUTCOME_UNKNOWN",
                "error_message": "transport lost",
                "retryable": True,
                "extra_metadata": {"transport_projection": True},
                "attempt": 1,
            }
        )
        assert row.commit_state == "PROVISIONAL"
        assert await store.load_committed_tool_result(
            "exec-r7-c", "call-r7-c"
        ) is None

        async with _Uow(sessions) as uow:
            invocation = await uow.capability_invocations.get_record("inv-r7-c")
            invocation.state = "COMPLETED"
            invocation.remote_outcome_state = "TERMINAL_COMMITTED"
            invocation.output = {"authoritative": 42}
            invocation.error = None
            invocation.revision = 2
            await uow.commit()

        promoted = await store.load_committed_tool_result(
            "exec-r7-c", "call-r7-c"
        )
        assert promoted.commit_state == "COMMITTED"
        assert promoted.success is True
        assert promoted.output == {"authoritative": 42}
        assert promoted.error_code is None
        assert promoted.extra_metadata["r7_commit_authority"] == "CAPABILITY_INVOCATION"
        assert promoted.extra_metadata["r7_invocation_revision"] == 2

        again = await store.load_committed_tool_result(
            "exec-r7-c", "call-r7-c"
        )
        assert again.commit_state == "COMMITTED"
        assert again.output == {"authoritative": 42}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_checkpoint_reconstruction_sanitizes_provisional_and_preserves_parallel_order(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-reconstruct.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        checkpoint_id = "exec-r7-c:checkpoint:2"
        async with _Uow(sessions) as uow:
            execution = AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="WAITING",
                    wait_reason="CONNECTION",
                    revision=2,
                    current_checkpoint_id=None,
                    request={},
                    transcript=[
                        {"role": "user", "content": "legacy"},
                        {
                            "role": "tool",
                            "tool_call_id": "call-1",
                            "content": "PROVISIONAL-POISON",
                        },
                    ],
                )
            uow.session.add(execution)
            await uow.session.flush()
            uow.session.add(
                AgentIterationRecord(
                    id="iter-r7-c",
                    execution_id="exec-r7-c",
                    iteration=3,
                    state="FAILED",
                    tool_call_ids=["call-2", "call-1", "call-3"],
                )
            )
            uow.session.add(
                AgentExecutionCheckpointRecord(
                    checkpoint_id=checkpoint_id,
                    execution_id="exec-r7-c",
                    execution_revision=2,
                    session_id="session-r7-c",
                    iteration=3,
                    wait_reason="CONNECTION",
                    transcript_snapshot=[
                        {"role": "user", "content": "checkpoint-prefix"},
                        {
                            "role": "tool",
                            "tool_call_id": "call-1",
                            "content": "PROVISIONAL-POISON",
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call-2",
                            "content": {"ok": 2},
                        },
                    ],
                    metadata_json={},
                )
            )

            await uow.session.flush()
            execution.current_checkpoint_id = checkpoint_id

            for call_id in ("call-1", "call-3", "call-2"):
                uow.session.add(
                    AgentToolCallRecord(
                        id=call_id,
                        execution_id="exec-r7-c",
                        iteration_id="iter-r7-c",
                        invocation_id=f"inv-{call_id}",
                        tool_call_id=call_id,
                        capability_id="tool.remote",
                        arguments={"call": call_id},
                        status="PENDING",
                    )
                )

            uow.session.add_all(
                [
                    AgentToolResultRecord(
                        id="result-call-1",
                        execution_id="exec-r7-c",
                        iteration_id="iter-r7-c",
                        tool_call_id="call-1",
                        invocation_id="inv-call-1",
                        capability_id="tool.remote",
                        success=False,
                        error_code="REMOTE_OUTCOME_UNKNOWN",
                        error_message="unknown",
                        commit_state="PROVISIONAL",
                    ),
                    AgentToolResultRecord(
                        id="result-call-2",
                        execution_id="exec-r7-c",
                        iteration_id="iter-r7-c",
                        tool_call_id="call-2",
                        invocation_id="inv-call-2",
                        capability_id="tool.remote",
                        success=True,
                        output={"ok": 2},
                        commit_state="COMMITTED",
                    ),
                    AgentToolResultRecord(
                        id="result-call-3",
                        execution_id="exec-r7-c",
                        iteration_id="iter-r7-c",
                        tool_call_id="call-3",
                        invocation_id="inv-call-3",
                        capability_id="tool.remote",
                        success=True,
                        output={"ok": 3},
                        commit_state="COMMITTED",
                    ),
                ]
            )
            await uow.commit()

        context = await store.resume_execution("exec-r7-c")
        assert context is not None
        assert context.iteration == 3
        # The active batch is re-materialized after resume, so even already
        # committed current-batch messages are stripped from the safe prefix.
        assert context.resume_transcript == [
            {"role": "user", "content": "checkpoint-prefix"},
        ]
        assert "PROVISIONAL-POISON" not in repr(context.resume_transcript)
        assert [
            item["tool_call_id"]
            for item in context.resume_pending_tool_calls
        ] == ["call-2", "call-1", "call-3"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_legacy_resume_transcript_is_fail_closed_for_uncommitted_tool_messages(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-legacy.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="WAITING_TOOL",
                    revision=0,
                    request={},
                    transcript=[
                        {"role": "user", "content": "hello"},
                        {
                            "role": "tool",
                            "tool_call_id": "call-p",
                            "content": "must-disappear",
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call-c",
                            "content": "keep",
                        },
                    ],
                )
            )
            uow.session.add(
                AgentIterationRecord(
                    id="iter-r7-c",
                    execution_id="exec-r7-c",
                    iteration=1,
                    state="WAITING_TOOL",
                    tool_call_ids=[],
                )
            )
            for call_id, state in (
                ("call-p", "PROVISIONAL"),
                ("call-c", "COMMITTED"),
            ):
                uow.session.add(
                    AgentToolResultRecord(
                        id=f"result-{call_id}",
                        execution_id="exec-r7-c",
                        iteration_id="iter-r7-c",
                        tool_call_id=call_id,
                        invocation_id=f"inv-{call_id}",
                        capability_id="tool.remote",
                        success=state == "COMMITTED",
                        output="keep" if state == "COMMITTED" else None,
                        commit_state=state,
                    )
                )
            await uow.commit()

        context = await store.resume_execution("exec-r7-c")
        assert context is not None
        assert [item["content"] for item in context.resume_transcript] == [
            "hello",
            "keep",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_resumed_batch_materializes_results_in_original_parallel_call_order():
    context = AgentExecutionContext.create(
        execution_id="exec-order",
        agent_id="agent-order",
        session_id="session-order",
        correlation_id="corr-order",
        identity=Identity(
            user_id="user-order",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_parallel_tools=3,
            timeout_seconds=5,
        ),
    )
    context.iteration = 3
    context.resume_pending_tool_calls = [
        {
            "execution_id": "exec-order",
            "iteration": 3,
            "invocation_id": "inv-2",
            "tool_call_id": "call-2",
            "capability_id": "tool.remote",
            "arguments": {"value": 2},
        },
        {
            "execution_id": "exec-order",
            "iteration": 3,
            "invocation_id": "inv-1",
            "tool_call_id": "call-1",
            "capability_id": "tool.remote",
            "arguments": {"value": 1},
        },
        {
            "execution_id": "exec-order",
            "iteration": 3,
            "invocation_id": "inv-3",
            "tool_call_id": "call-3",
            "capability_id": "tool.remote",
            "arguments": {"value": 3},
        },
    ]

    def result(call_id, value):
        return ToolExecutionResult(
            execution_id="exec-order",
            iteration=3,
            invocation_id=f"inv-{call_id[-1]}",
            tool_call_id=call_id,
            capability_id="tool.remote",
            success=True,
            output={"value": value},
        )

    committed = {
        "call-2": result("call-2", 2),
        "call-3": result("call-3", 3),
    }

    class Store:
        async def load_committed_tool_result(self, execution_id, tool_call_id):
            value = committed.get(tool_call_id)
            if value is None:
                return None
            return type(
                "Record",
                (),
                {
                    "execution_id": value.execution_id,
                    "invocation_id": value.invocation_id,
                    "tool_call_id": value.tool_call_id,
                    "capability_id": value.capability_id,
                    "success": value.success,
                    "output": value.output,
                    "error_code": value.error_code,
                    "error_message": value.error_message,
                    "retryable": value.retryable,
                    "extra_metadata": value.metadata,
                    "commit_state": "COMMITTED",
                },
            )()

        async def save_tool_result(self, values):
            committed_result = result(
                values["tool_call_id"],
                values["output"]["value"],
            )
            committed[values["tool_call_id"]] = committed_result

    class Executor:
        async def execute_many(self, context, requests, *, max_parallel):
            assert [item.tool_call_id for item in requests] == ["call-1"]
            assert max_parallel == 3
            return [result("call-1", 1)]

    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=Executor(),
        execution_policy=None,
        durable_store=Store(),
    )

    reconstructed = await runtime._execute_resumed_tool_calls(context)

    assert [item.tool_call_id for item in reconstructed] == [
        "call-2",
        "call-1",
        "call-3",
    ]
    assert [item.output["value"] for item in reconstructed] == [2, 1, 3]



@pytest.mark.asyncio
async def test_r7_c_reused_tool_call_id_with_different_invocation_is_rejected(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-call-collision.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="RUNNING",
                    revision=1,
                    request={},
                )
            )
            uow.session.add_all(
                [
                    AgentIterationRecord(
                        id="iter-1",
                        execution_id="exec-r7-c",
                        iteration=1,
                        state="WAITING_TOOL",
                        tool_call_ids=["call-reused"],
                    ),
                    AgentIterationRecord(
                        id="iter-2",
                        execution_id="exec-r7-c",
                        iteration=2,
                        state="WAITING_TOOL",
                        tool_call_ids=["call-reused"],
                    ),
                ]
            )
            await uow.commit()

        await store.save_tool_call(
            {
                "id": "call-reused",
                "execution_id": "exec-r7-c",
                "iteration_id": "iter-1",
                "invocation_id": "inv-old",
                "tool_call_id": "call-reused",
                "capability_id": "tool.echo",
                "arguments": {"value": "old"},
                "status": "PENDING",
                "extra_metadata": {},
            }
        )

        with pytest.raises(ExecutionConflictError, match="iteration_id"):
            await store.save_tool_call(
                {
                    "id": "call-reused",
                    "execution_id": "exec-r7-c",
                    "iteration_id": "iter-2",
                    "invocation_id": "inv-new",
                    "tool_call_id": "call-reused",
                    "capability_id": "tool.echo",
                    "arguments": {"value": "new"},
                    "status": "PENDING",
                    "extra_metadata": {},
                }
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_conflicting_committed_result_is_rejected(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-result-conflict.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="RUNNING",
                    revision=1,
                    request={},
                )
            )
            uow.session.add(
                AgentIterationRecord(
                    id="iter-1",
                    execution_id="exec-r7-c",
                    iteration=1,
                    state="WAITING_TOOL",
                    tool_call_ids=["call-1"],
                )
            )
            uow.session.add(
                AgentToolResultRecord(
                    id="result-1",
                    execution_id="exec-r7-c",
                    iteration_id="iter-1",
                    tool_call_id="call-1",
                    invocation_id="inv-1",
                    capability_id="tool.echo",
                    success=True,
                    output={"value": "durable"},
                    commit_state="COMMITTED",
                )
            )
            await uow.commit()

        with pytest.raises(ExecutionConflictError, match="COMMITTED tool-result output"):
            await store.save_tool_result(
                {
                    "id": "result-1",
                    "execution_id": "exec-r7-c",
                    "iteration_id": "iter-1",
                    "tool_call_id": "call-1",
                    "invocation_id": "inv-1",
                    "capability_id": "tool.echo",
                    "success": True,
                    "output": {"value": "conflict"},
                    "error_code": None,
                    "error_message": None,
                    "retryable": False,
                    "extra_metadata": {
                        "r7_commit_authority": "AGENT_PRE_DISPATCH",
                    },
                    "attempt": 1,
                }
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_committed_loader_rejects_request_identity_mismatch():
    class Store:
        async def load_committed_tool_result(self, execution_id, tool_call_id):
            return type(
                "Record",
                (),
                {
                    "execution_id": execution_id,
                    "invocation_id": "inv-old",
                    "tool_call_id": tool_call_id,
                    "capability_id": "tool.echo",
                    "success": True,
                    "output": {"old": True},
                    "error_code": None,
                    "error_message": None,
                    "retryable": False,
                    "extra_metadata": {},
                    "commit_state": "COMMITTED",
                },
            )()

    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=Store(),
    )
    request = ToolExecutionRequest(
        execution_id="exec-1",
        iteration=2,
        invocation_id="inv-new",
        tool_call_id="call-1",
        capability_id="tool.echo",
        arguments={},
    )

    with pytest.raises(ExecutionConflictError, match="invocation_id"):
        await runtime._load_committed_tool_result(request)


@pytest.mark.asyncio
async def test_r7_c_legacy_transcript_rematerializes_exact_committed_payload(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-canonical-transcript.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="WAITING_TOOL",
                    revision=0,
                    request={},
                    transcript=[
                        {"role": "user", "content": "hello"},
                        {
                            "role": "tool",
                            "name": "tool.remote",
                            "tool_call_id": "call-c",
                            "content": {
                                "error_code": "REMOTE_OUTCOME_UNKNOWN",
                                "error_message": "stale poison",
                            },
                            "metadata": {"success": False},
                        },
                    ],
                )
            )
            uow.session.add(
                AgentIterationRecord(
                    id="iter-r7-c",
                    execution_id="exec-r7-c",
                    iteration=1,
                    state="WAITING_TOOL",
                    tool_call_ids=[],
                )
            )
            uow.session.add(
                AgentToolResultRecord(
                    id="result-call-c",
                    execution_id="exec-r7-c",
                    iteration_id="iter-r7-c",
                    tool_call_id="call-c",
                    invocation_id="inv-call-c",
                    capability_id="tool.remote",
                    success=True,
                    output={"authoritative": 42},
                    commit_state="COMMITTED",
                )
            )
            await uow.commit()

        context = await store.resume_execution("exec-r7-c")
        assert context is not None
        assert context.resume_transcript[1]["content"] == {"authoritative": 42}
        assert context.resume_transcript[1]["metadata"] == {
            "success": True,
            "retryable": False,
        }
        assert "REMOTE_OUTCOME_UNKNOWN" not in repr(context.resume_transcript)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_missing_expected_invocation_authority_fails_closed(tmp_path):
    engine, sessions = await _schema(tmp_path, "r7c-missing-authority.sqlite")
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r7-c",
                    session_id="session-r7-c",
                    agent_id="agent-r7-c",
                    correlation_id="corr-r7-c",
                    state="RUNNING",
                    revision=1,
                    request={},
                )
            )
            uow.session.add(
                AgentIterationRecord(
                    id="iter-1",
                    execution_id="exec-r7-c",
                    iteration=1,
                    state="WAITING_TOOL",
                    tool_call_ids=["call-missing", "call-pre"],
                )
            )
            await uow.commit()

        missing = await store.save_tool_result(
            {
                "id": "result-missing",
                "execution_id": "exec-r7-c",
                "iteration_id": "iter-1",
                "tool_call_id": "call-missing",
                "invocation_id": "inv-missing",
                "capability_id": "tool.remote",
                "success": True,
                "output": {"unsafe": True},
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "extra_metadata": {},
                "attempt": 1,
            }
        )
        assert missing.commit_state == "PROVISIONAL"
        assert await store.load_committed_tool_result(
            "exec-r7-c", "call-missing"
        ) is None

        predispatch = await store.save_tool_result(
            {
                "id": "result-pre",
                "execution_id": "exec-r7-c",
                "iteration_id": "iter-1",
                "tool_call_id": "call-pre",
                "invocation_id": "inv-pre",
                "capability_id": "tool.missing",
                "success": False,
                "output": None,
                "error_code": "CAPABILITY_NOT_FOUND",
                "error_message": "CAPABILITY_NOT_FOUND",
                "retryable": False,
                "extra_metadata": {
                    "r7_commit_authority": "AGENT_PRE_DISPATCH",
                },
                "attempt": 1,
            }
        )
        assert predispatch.commit_state == "COMMITTED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_c_terminal_committed_reserved_error_code_does_not_enter_waiting():
    context = AgentExecutionContext.create(
        execution_id="exec-terminal-error",
        agent_id="agent-1",
        session_id="session-1",
        correlation_id="corr-1",
        identity=Identity(user_id="user-1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(max_iterations=3, timeout_seconds=5),
    )

    class ContextBuilder:
        async def build(self, context, request):
            return type("Snapshot", (), {"messages": [], "tools": [], "metadata": {}})()

    class Inference:
        def __init__(self):
            self.calls = 0

        async def complete(self, request):
            self.calls += 1
            calls = (
                (
                    InferenceToolCall(
                        id="call-terminal-error",
                        name="tool.remote",
                        arguments={},
                    ),
                )
                if self.calls == 1
                else ()
            )
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=InferenceMessage(
                    role="assistant",
                    content="done" if self.calls > 1 else "",
                    tool_calls=calls,
                ),
                usage=InferenceUsage(),
                provider="test",
                model="test",
            )

    class Policy:
        def check_start(self, context):
            return PolicyDecision.ALLOW

        def check_iteration(self, context, iteration):
            return PolicyDecision.ALLOW

    terminal = ToolExecutionResult(
        execution_id="exec-terminal-error",
        iteration=1,
        invocation_id="inv-terminal-error",
        tool_call_id="call-terminal-error",
        capability_id="tool.remote",
        success=False,
        error_code="REMOTE_CONNECTION_LOST",
        error_message="authoritative remote terminal error",
        retryable=False,
    )

    class Executor:
        async def execute_many(self, context, requests, *, max_parallel):
            request = requests[0]
            return [
                terminal.model_copy(
                    update={
                        "iteration": request.iteration,
                        "invocation_id": request.invocation_id,
                    }
                )
            ]

    class Store:
        def __init__(self):
            self.iterations = {}
            self.saved_result = None

        async def load_iteration(self, execution_id, *, iteration_number=None, iteration_id=None):
            return self.iterations.get(iteration_number)

        async def save_iteration(self, values):
            item = type("Iteration", (), values)()
            self.iterations[values["iteration"]] = item
            return item

        async def update_iteration(self, iteration_id, values):
            item = next(
                item for item in self.iterations.values()
                if item.id == iteration_id
            )
            for key, value in values.items():
                setattr(item, key, value)
            return item

        async def save_tool_call(self, values):
            return type("Call", (), values)()

        async def save_tool_result(self, values):
            self.saved_result = dict(values)
            return type("Result", (), values)()

        async def load_committed_tool_result(self, execution_id, tool_call_id):
            if self.saved_result is None:
                return None
            values = dict(self.saved_result)
            values.update(
                {
                    "success": False,
                    "error_code": "REMOTE_CONNECTION_LOST",
                    "error_message": "authoritative remote terminal error",
                    "retryable": False,
                    "output": None,
                    "extra_metadata": {},
                    "commit_state": "COMMITTED",
                }
            )
            return type("Result", (), values)()

        async def update_checkpoint(self, execution_id, values):
            return None

    inference = Inference()
    runtime = AgentRuntime(
        context_builder=ContextBuilder(),
        inference=inference,
        tool_execution=Executor(),
        execution_policy=Policy(),
        durable_store=Store(),
        continuation_service=None,
    )

    result = await runtime._execute_loop(context)

    assert result.state.value == "COMPLETED"
    assert result.error_code is None
    assert inference.calls == 2
