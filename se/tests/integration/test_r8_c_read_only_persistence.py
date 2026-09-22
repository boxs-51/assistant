from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(
            self.session
        )
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


async def _database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r8_c_read_only.sqlite').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _execution():
    return AgentExecutionRecord(
        id="exec-r8-c",
        session_id="session-r8-c",
        agent_id="agent-r8-c",
        task_id=None,
        branch_id=None,
        correlation_id="corr-r8-c",
        state="WAITING",
        revision=7,
        current_checkpoint_id="cp-r8-c",
        request={},
    )


def _iteration():
    return AgentIterationRecord(
        id="iter-r8-c",
        execution_id="exec-r8-c",
        iteration=4,
        state="WAITING",
        tool_call_ids=["call-b", "call-a"],
    )


def _checkpoint():
    return AgentExecutionCheckpointRecord(
        checkpoint_id="cp-r8-c",
        execution_id="exec-r8-c",
        execution_revision=7,
        session_id="session-r8-c",
        task_id=None,
        branch_id=None,
        iteration=4,
        wait_reason="RESOURCE",
        transcript_snapshot=[
            {
                "role": "user",
                "content": "base",
                "tool_calls": [],
                "name": None,
                "tool_call_id": None,
                "metadata": {},
            }
        ],
        metadata_json={},
    )


def _result(tool_call_id: str, output: str, *, committed=True):
    suffix = tool_call_id.removeprefix("call-")
    return AgentToolResultRecord(
        id=f"result-{tool_call_id}",
        execution_id="exec-r8-c",
        iteration_id="iter-r8-c",
        tool_call_id=tool_call_id,
        invocation_id=suffix,
        capability_id=f"tool.{suffix}",
        success=True,
        output={"value": output},
        retryable=False,
        commit_state="COMMITTED" if committed else "PROVISIONAL",
        attempt=1,
    )


def _invocation(invocation_id: str, *, execution_id="exec-r8-c"):
    return CapabilityInvocationRecord(
        invocation_id=invocation_id,
        capability_id=f"tool.{invocation_id}",
        capability_version="1",
        kind="TOOL",
        execution_mode="SYNC",
        idempotency="IDEMPOTENT",
        request_fingerprint=f"fp-{invocation_id}",
        remote_outcome_state="TERMINAL_COMMITTED",
        implementation_id=f"impl-{invocation_id}",
        driver_kind="REMOTE_CLIENT",
        state="COMPLETED",
        execution_id=execution_id,
        tool_call_id=f"call-{invocation_id}",
        attempt=1,
        max_attempts=1,
        arguments={},
        output={"ok": True},
        revision=3,
    )


async def _seed_source(sessions, *, provisional=False, pending=False):
    async with sessions() as session:
        session.add(_execution())
        session.add(_iteration())
        session.add(_checkpoint())
        session.add(_result("call-a", "A", committed=not provisional))
        session.add(_result("call-b", "B"))
        session.add(_invocation("a"))
        session.add(_invocation("b"))
        if pending:
            session.add(
                AgentCheckpointPendingInvocationRecord(
                    checkpoint_id="cp-r8-c",
                    ordinal=0,
                    invocation_id="inv-pending",
                    invocation_revision=1,
                    tool_call_id="call-pending",
                    capability_id="tool.pending",
                    capability_version="1",
                    request_fingerprint="fp-pending",
                    idempotency="IDEMPOTENT",
                    observed_remote_outcome_state="TERMINAL_COMMITTED",
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_is_read_only_and_preserves_parallel_order(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions)
        store = DurableAgentStore(lambda: _Uow(sessions))

        transcript = await store.load_fork_safe_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
        )

        assert [item.role for item in transcript] == ["user", "tool", "tool"]
        assert [item.tool_call_id for item in transcript[1:]] == [
            "call-b",
            "call-a",
        ]
        assert [item.content for item in transcript[1:]] == [
            {"value": "B"},
            {"value": "A"},
        ]

        async with sessions() as session:
            a = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-a",
            )
            b = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-b",
            )
            execution = await AgentRepository(session).get_execution(
                "exec-r8-c"
            )
            assert a.commit_state == "COMMITTED"
            assert b.commit_state == "COMMITTED"
            assert execution.revision == 7
            assert execution.current_checkpoint_id == "cp-r8-c"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_never_promotes_provisional_result(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions, provisional=True)
        store = DurableAgentStore(lambda: _Uow(sessions))

        with pytest.raises(
            ExecutionConflictError,
            match="not durably COMMITTED",
        ):
            await store.load_fork_safe_checkpoint_transcript(
                "exec-r8-c",
                "cp-r8-c",
            )

        async with sessions() as session:
            result = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-a",
            )
            assert result.commit_state == "PROVISIONAL"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_rejects_any_pending_checkpoint_row(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions, pending=True)
        store = DurableAgentStore(lambda: _Uow(sessions))

        with pytest.raises(
            ExecutionConflictError,
            match="FORK_PENDING_INVOCATIONS",
        ):
            await store.load_fork_safe_checkpoint_transcript(
                "exec-r8-c",
                "cp-r8-c",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_invocation_scan_is_deterministic_and_read_only(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_invocation("b"))
            session.add(_invocation("a"))
            session.add(_invocation("other", execution_id="exec-other"))
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        rows = await store.load_fork_capability_invocations("exec-r8-c")
        assert [item.invocation_id for item in rows] == ["a", "b"]

        async with sessions() as session:
            repo = CapabilityInvocationRepository(session)
            direct = await repo.list_records_for_execution("exec-r8-c")
            assert [item.invocation_id for item in direct] == ["a", "b"]
            assert [item.revision for item in direct] == [3, 3]
            assert [item.state for item in direct] == [
                "COMPLETED",
                "COMPLETED",
            ]
    finally:
        await engine.dispose()
