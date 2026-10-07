from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import (
    Session as ChatSessionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
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


async def _seed_legacy_waiting(sessions):
    continuation = {
        "current_checkpoint_id": "legacy-cp-1",
        "checkpoints": {
            "legacy-cp-1": {
                "checkpoint_id": "legacy-cp-1",
                "execution_id": "exec-legacy",
                "session_id": "session-legacy",
                "reason": "WAITING_FOR_CONNECTION",
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "origin_connection_id": "conn-k1",
                "pending_invocation_id": "inv-pending",
                "pending_tool_call_id": "call-pending",
                "pending_capability_id": "tool.remote",
                "iteration": 2,
                "transcript": [{"role": "user", "content": "legacy"}],
                "metadata": {
                    "owner_user_id": "user-1",
                    "origin_client_id": "client-1",
                },
            }
        },
        "branches": {},
    }
    frozen = deepcopy(continuation)

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
                state="WAITING",
                wait_reason="CONNECTION",
                revision=4,
                current_checkpoint_id=None,
                bound_client_id=None,
                bound_connection_id=None,
                remaining_active_budget_seconds=30.0,
                request={},
                context_state={"continuation": continuation},
            )
        )
        await session.commit()

    return frozen


async def _seed_canonical_waiting(sessions):
    async with sessions() as session:
        session.add(
            ChatSessionRecord(
                id="session-canonical",
                user_id="user-1",
                organization_id=None,
            )
        )
        session.add(
            AgentExecutionRecord(
                id="exec-canonical",
                session_id="session-canonical",
                agent_id="agent-r7",
                correlation_id="corr-canonical",
                state="WAITING",
                wait_reason="CONNECTION",
                revision=4,
                current_checkpoint_id="cp-canonical",
                bound_client_id="client-1",
                bound_connection_id=None,
                remaining_active_budget_seconds=30.0,
                request={},
                context_state={},
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id="cp-canonical",
                execution_id="exec-canonical",
                execution_revision=4,
                session_id="session-canonical",
                iteration=1,
                wait_reason="CONNECTION",
                remaining_active_budget_seconds=30.0,
                origin_client_id="client-1",
                origin_connection_id="conn-old",
                transcript_snapshot=[{"role": "assistant", "content": ""}],
                metadata_json={},
            )
        )
        await session.flush()
        repo = AgentRepository(session)
        await repo.save_checkpoint_pending_invocation(
            {
                "checkpoint_id": "cp-canonical",
                "ordinal": 0,
                "invocation_id": "inv-canonical",
                "invocation_revision": 1,
                "tool_call_id": "call-canonical",
                "capability_id": "tool.remote",
                "capability_version": "1.0",
                "request_fingerprint": "f" * 64,
                "idempotency": "IDEMPOTENT",
                "observed_remote_outcome_state": "OUTCOME_UNKNOWN",
                "origin_client_id": "client-1",
                "origin_connection_id": "conn-old",
            }
        )
        await session.commit()


@pytest.mark.asyncio
async def test_r13_c1_pending_ticket_path_does_not_materialize_legacy_json(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r13-c1-retirement.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))
    frozen_continuation = await _seed_legacy_waiting(sessions)

    try:
        assert not hasattr(DurableAgentStore, "materialize_legacy_checkpoint")
        assert not hasattr(
            DurableAgentStore,
            "materialize_legacy_waiting_for_client",
        )

        tickets = await store.load_pending_resume_tickets(
            owner_user_id="user-1",
            client_id="client-1",
        )
        assert tickets == ()

        async with sessions() as session:
            execution = (
                await session.execute(
                    select(AgentExecutionRecord).where(
                        AgentExecutionRecord.id == "exec-legacy"
                    )
                )
            ).scalar_one()
            checkpoints = (
                await session.execute(
                    select(AgentExecutionCheckpointRecord).where(
                        AgentExecutionCheckpointRecord.execution_id
                        == "exec-legacy"
                    )
                )
            ).scalars().all()

        assert execution.revision == 4
        assert execution.current_checkpoint_id is None
        assert execution.bound_client_id is None
        assert execution.context_state["continuation"] == frozen_continuation
        assert checkpoints == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r13_c1_pending_ticket_path_preserves_canonical_normalized_waiting(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r13-c1-canonical-ticket.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))
    await _seed_canonical_waiting(sessions)

    try:
        tickets = await store.load_pending_resume_tickets(
            owner_user_id="user-1",
            client_id="client-1",
        )

        assert tickets == (
            {
                "execution_id": "exec-canonical",
                "checkpoint_id": "cp-canonical",
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
