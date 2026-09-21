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
from se.src.runtimes.agent.persistence import DurableAgentStore


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
