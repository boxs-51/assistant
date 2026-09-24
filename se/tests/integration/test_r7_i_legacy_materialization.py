from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
    AgentTranscriptRepresentationRecord,
)
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import (
    Session as ChatSessionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.legacy_materialization import (
    LegacyCheckpointMaterializationError,
    parse_legacy_checkpoint_source,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)


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


async def _seed_legacy_waiting(
    sessions,
    *,
    execution_state: str = "WAITING",
    execution_wait_reason: str | None = "CONNECTION",
):
    checkpoint_id = "legacy-cp-1"
    continuation = {
        "current_checkpoint_id": checkpoint_id,
        "checkpoints": {
            checkpoint_id: {
                "checkpoint_id": checkpoint_id,
                "execution_id": "exec-legacy",
                "session_id": "session-legacy",
                "reason": "WAITING_FOR_CONNECTION",
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "parent_checkpoint_id": None,
                "origin_connection_id": "conn-k1",
                "current_connection_id": None,
                "pending_invocation_id": "inv-pending",
                "pending_tool_call_id": "call-pending",
                "pending_capability_id": "tool.remote",
                "iteration": 2,
                "transcript": [
                    {"role": "user", "content": "run tools"},
                    {
                        "role": "tool",
                        "tool_call_id": "call-committed",
                        "content": "old committed projection",
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-pending",
                        "content": {
                            "error_code": "REMOTE_OUTCOME_UNKNOWN"
                        },
                    },
                ],
                "metadata": {
                    "owner_user_id": "user-1",
                    "origin_client_id": "client-1",
                    "server_continuation_available": True,
                },
            }
        },
        "branches": {},
    }

    async with sessions() as session:
        session.add(
            ChatSessionRecord(
                id="session-legacy",
                user_id="user-1",
                organization_id=None,
            )
        )
        session.add(
            AgentExecutionRecord(
                id="exec-legacy",
                session_id="session-legacy",
                agent_id="agent-1",
                correlation_id="corr-1",
                state=execution_state,
                wait_reason=execution_wait_reason,
                revision=4,
                current_checkpoint_id=None,
                bound_client_id=None,
                bound_connection_id=None,
                remaining_active_budget_seconds=30.0,
                request={},
                context_state={"continuation": continuation},
            )
        )
        session.add(
            AgentIterationRecord(
                id="iter-2",
                execution_id="exec-legacy",
                iteration=2,
                state="WAITING_TOOL",
                tool_call_ids=["call-committed", "call-pending"],
            )
        )
        session.add_all(
            [
                AgentToolCallRecord(
                    id="tool-row-1",
                    execution_id="exec-legacy",
                    iteration_id="iter-2",
                    invocation_id="inv-committed",
                    tool_call_id="call-committed",
                    capability_id="tool.local",
                    arguments={},
                    status="COMPLETED",
                ),
                AgentToolCallRecord(
                    id="tool-row-2",
                    execution_id="exec-legacy",
                    iteration_id="iter-2",
                    invocation_id="inv-pending",
                    tool_call_id="call-pending",
                    capability_id="tool.remote",
                    arguments={"x": 1},
                    status="PENDING",
                ),
            ]
        )
        session.add(
            AgentToolResultRecord(
                id="result-committed",
                execution_id="exec-legacy",
                iteration_id="iter-2",
                tool_call_id="call-committed",
                invocation_id="inv-committed",
                capability_id="tool.local",
                success=True,
                output={"ok": True},
                retryable=False,
                commit_state="COMMITTED",
                attempt=1,
            )
        )
        session.add(
            CapabilityInvocationRecord(
                invocation_id="inv-pending",
                capability_id="tool.remote",
                capability_version="1.0",
                kind="TOOL",
                execution_mode="ONE_SHOT",
                idempotency="IDEMPOTENT",
                request_fingerprint="f" * 64,
                owner_user_id="user-1",
                origin_client_id="client-1",
                remote_outcome_state="OUTCOME_UNKNOWN",
                implementation_id="impl-remote",
                driver_kind="REMOTE_CLIENT",
                state="WAITING",
                wait_reason="CONNECTION",
                session_id="session-legacy",
                execution_id="exec-legacy",
                tool_call_id="call-pending",
                connection_id="conn-k1",
                attempt=1,
                max_attempts=2,
                arguments={"x": 1},
                revision=3,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_r7_i_materializes_legacy_waiting_without_revision_change(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-materialization.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)

        checkpoint = await store.materialize_legacy_checkpoint(
            "exec-legacy",
            requested_checkpoint_id="legacy-cp-1",
            target_user_id="user-1",
            target_client_id="client-1",
        )

        assert checkpoint is not None
        assert checkpoint.checkpoint_id == "legacy-cp-1"
        assert checkpoint.execution_revision == 4
        assert checkpoint.origin_client_id == "client-1"
        assert checkpoint.origin_connection_id == "conn-k1"
        assert checkpoint.transcript_snapshot == (
            {"role": "user", "content": "run tools"},
        )

        execution = await store.load_execution("exec-legacy")
        assert execution.state == "WAITING"
        assert execution.revision == 4
        assert execution.current_checkpoint_id == "legacy-cp-1"
        assert execution.bound_client_id == "client-1"
        assert execution.bound_connection_id is None

        pending = await store.load_checkpoint_pending_invocations(
            "legacy-cp-1"
        )
        assert len(pending) == 1
        assert pending[0].ordinal == 1
        assert pending[0].invocation_id == "inv-pending"
        assert pending[0].tool_call_id == "call-pending"
        assert pending[0].capability_id == "tool.remote"
        assert pending[0].capability_version == "1.0"
        assert pending[0].request_fingerprint == "f" * 64
        assert pending[0].idempotency == "IDEMPOTENT"
        assert pending[0].observed_remote_outcome_state == "OUTCOME_UNKNOWN"

        again = await store.materialize_legacy_checkpoint(
            "exec-legacy",
            requested_checkpoint_id="legacy-cp-1",
            target_user_id="user-1",
            target_client_id="client-1",
        )
        assert again == checkpoint

        async with sessions() as session:
            checkpoint_count = len(
                (
                    await session.execute(
                        select(AgentExecutionCheckpointRecord).where(
                            AgentExecutionCheckpointRecord.execution_id
                            == "exec-legacy"
                        )
                    )
                ).scalars().all()
            )
        assert checkpoint_count == 1
        async with sessions() as session:
            row = await session.get(
                AgentExecutionCheckpointRecord,
                "legacy-cp-1",
            )
            assert row.transcript_snapshot is not None
            assert row.transcript_ref is not None
            assert row.transcript_version is not None
            representation_count = len(
                (
                    await session.execute(
                        select(AgentTranscriptRepresentationRecord)
                    )
                ).scalars().all()
            )
            assert representation_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_waiting_ticket_replay_materializes_legacy_first(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-ticket-replay.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)
        tickets = await store.load_pending_resume_tickets(
            owner_user_id="user-1",
            client_id="client-1",
        )
        assert tickets == (
            {
                "execution_id": "exec-legacy",
                "checkpoint_id": "legacy-cp-1",
                "revision": 4,
                "wait_reason": "CONNECTION",
                "wait_expires_at": None,
                "origin_client_id": "client-1",
                "pending_capability_ids": ["tool.remote"],
                "auto_resume_allowed": True,
            },
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_unsafe_legacy_checkpoint_rolls_back_without_pointer(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-unsafe.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)
        async with sessions() as session:
            await session.execute(
                delete(CapabilityInvocationRecord).where(
                    CapabilityInvocationRecord.invocation_id == "inv-pending"
                )
            )
            await session.commit()

        with pytest.raises(LegacyCheckpointMaterializationError) as exc:
            await store.materialize_legacy_checkpoint(
                "exec-legacy",
                requested_checkpoint_id="legacy-cp-1",
                target_user_id="user-1",
                target_client_id="client-1",
            )
        assert exc.value.code == "CHECKPOINT_INCOMPLETE"

        execution = await store.load_execution("exec-legacy")
        assert execution.revision == 4
        assert execution.current_checkpoint_id is None

        async with sessions() as session:
            rows = (
                await session.execute(
                    select(AgentExecutionCheckpointRecord).where(
                        AgentExecutionCheckpointRecord.execution_id
                        == "exec-legacy"
                    )
                )
            ).scalars().all()
        assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_materializes_pre_normalized_waiting_spelling(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-old-spelling.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(
            sessions,
            execution_state="WAITING_FOR_CONNECTION",
            execution_wait_reason=None,
        )
        checkpoint = await store.materialize_legacy_checkpoint(
            "exec-legacy",
            target_user_id="user-1",
            target_client_id="client-1",
        )
        assert checkpoint is not None

        execution = await store.load_execution("exec-legacy")
        assert execution.state == "WAITING"
        assert execution.wait_reason == "CONNECTION"
        assert execution.revision == 4
        assert execution.current_checkpoint_id == "legacy-cp-1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_preserves_checkpoint_time_pending_slot_after_late_commit(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-late-commit.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)
        async with sessions() as session:
            invocation = await session.get(
                CapabilityInvocationRecord,
                "inv-pending",
            )
            invocation.state = "COMPLETED"
            invocation.wait_reason = None
            invocation.remote_outcome_state = "TERMINAL_COMMITTED"
            invocation.output = {"late": True}
            invocation.revision = 4
            session.add(
                AgentToolResultRecord(
                    id="result-pending-late",
                    execution_id="exec-legacy",
                    iteration_id="iter-2",
                    tool_call_id="call-pending",
                    invocation_id="inv-pending",
                    capability_id="tool.remote",
                    success=True,
                    output={"late": True},
                    retryable=False,
                    commit_state="COMMITTED",
                    attempt=1,
                )
            )
            await session.commit()

        checkpoint = await store.materialize_legacy_checkpoint(
            "exec-legacy",
            requested_checkpoint_id="legacy-cp-1",
            target_user_id="user-1",
            target_client_id="client-1",
        )
        assert checkpoint is not None

        pending = await store.load_checkpoint_pending_invocations(
            "legacy-cp-1"
        )
        assert len(pending) == 1
        assert pending[0].ordinal == 1
        assert pending[0].invocation_id == "inv-pending"
        assert (
            pending[0].observed_remote_outcome_state
            == "TERMINAL_COMMITTED"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_legacy_continuation_json_is_read_only(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-read-only.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)
        with pytest.raises(
            ExecutionConflictError,
            match="LEGACY_CONTINUATION_READ_ONLY",
        ):
            await store.update_checkpoint(
                "exec-legacy",
                {
                    "context_state": {
                        "continuation": {
                            "current_checkpoint_id": "forged"
                        }
                    }
                },
            )

        execution = await store.load_execution("exec-legacy")
        assert (
            execution.context_state["continuation"]["current_checkpoint_id"]
            == "legacy-cp-1"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_existing_checkpoint_semantic_collision_fails_closed(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-collision.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)
        execution = await store.load_execution("exec-legacy")
        source = parse_legacy_checkpoint_source(
            execution,
            target_user_id="user-1",
            target_client_id="client-1",
        )

        async with sessions() as session:
            session.add(
                AgentExecutionCheckpointRecord(
                    checkpoint_id="legacy-cp-1",
                    execution_id="exec-legacy",
                    execution_revision=4,
                    session_id="session-legacy",
                    iteration=2,
                    wait_reason="CONNECTION",
                    remaining_active_budget_seconds=30.0,
                    origin_client_id="client-1",
                    origin_connection_id="conn-k1",
                    transcript_snapshot=[
                        {"role": "user", "content": "DIFFERENT"}
                    ],
                    legacy_source_key=source.legacy_source_key,
                    metadata_json={},
                )
            )
            await session.commit()

        with pytest.raises(LegacyCheckpointMaterializationError) as exc:
            await store.materialize_legacy_checkpoint(
                "exec-legacy",
                requested_checkpoint_id="legacy-cp-1",
                target_user_id="user-1",
                target_client_id="client-1",
            )
        assert exc.value.code == "LEGACY_CHECKPOINT_UNSAFE"

        execution = await store.load_execution("exec-legacy")
        assert execution.revision == 4
        assert execution.current_checkpoint_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_i_concurrent_materialization_converges_to_one_checkpoint(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-i-race.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))

    try:
        await _seed_legacy_waiting(sessions)

        outcomes = await asyncio.gather(
            store.materialize_legacy_checkpoint(
                "exec-legacy",
                requested_checkpoint_id="legacy-cp-1",
                target_user_id="user-1",
                target_client_id="client-1",
            ),
            store.materialize_legacy_checkpoint(
                "exec-legacy",
                requested_checkpoint_id="legacy-cp-1",
                target_user_id="user-1",
                target_client_id="client-1",
            ),
        )

        assert [item.checkpoint_id for item in outcomes] == [
            "legacy-cp-1",
            "legacy-cp-1",
        ]
        execution = await store.load_execution("exec-legacy")
        assert execution.revision == 4
        assert execution.current_checkpoint_id == "legacy-cp-1"

        async with sessions() as session:
            rows = (
                await session.execute(
                    select(AgentExecutionCheckpointRecord).where(
                        AgentExecutionCheckpointRecord.execution_id
                        == "exec-legacy"
                    )
                )
            ).scalars().all()
        assert len(rows) == 1
    finally:
        await engine.dispose()
