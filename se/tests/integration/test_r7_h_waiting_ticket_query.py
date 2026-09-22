from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentSessionRecord,
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


async def _seed_waiting(
    sessions,
    *,
    user_id: str,
    client_id: str,
    session_id: str,
    execution_id: str,
    checkpoint_id: str,
    capability_id: str,
):
    async with sessions() as session:
        session.add(
            AgentSessionRecord(
                id=session_id,
                owner_user_id=user_id,
            )
        )
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=session_id,
                agent_id="agent-r7h",
                correlation_id=f"corr-{execution_id}",
                state="WAITING",
                wait_reason="CONNECTION",
                revision=4,
                current_checkpoint_id=checkpoint_id,
                bound_client_id=client_id,
                bound_connection_id=None,
                remaining_active_budget_seconds=30.0,
                request={},
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=checkpoint_id,
                execution_id=execution_id,
                execution_revision=4,
                session_id=session_id,
                iteration=1,
                wait_reason="CONNECTION",
                remaining_active_budget_seconds=30.0,
                origin_client_id=client_id,
                origin_connection_id=f"{client_id}-k1",
                transcript_snapshot=[{"role": "assistant", "content": ""}],
                metadata_json={},
            )
        )
        await session.flush()
        repo = AgentRepository(session)
        await repo.save_checkpoint_pending_invocation(
            {
                "checkpoint_id": checkpoint_id,
                "ordinal": 0,
                "invocation_id": f"inv-{execution_id}",
                "invocation_revision": 1,
                "tool_call_id": f"call-{execution_id}",
                "capability_id": capability_id,
                "capability_version": "1.0",
                "request_fingerprint": "f" * 64,
                "idempotency": "IDEMPOTENT",
                "observed_remote_outcome_state": "OUTCOME_UNKNOWN",
                "origin_client_id": client_id,
                "origin_connection_id": f"{client_id}-k1",
            }
        )
        await session.commit()


@pytest.mark.asyncio
async def test_r7_h_waiting_ticket_query_is_principal_and_client_scoped(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r7-h-ticket-query.db').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    store = DurableAgentStore(factory)

    try:
        await _seed_waiting(
            sessions,
            user_id="user-a",
            client_id="client-a",
            session_id="session-a",
            execution_id="exec-a",
            checkpoint_id="cp-a",
            capability_id="tool.a",
        )
        await _seed_waiting(
            sessions,
            user_id="user-a",
            client_id="client-b",
            session_id="session-b",
            execution_id="exec-b",
            checkpoint_id="cp-b",
            capability_id="tool.b",
        )
        await _seed_waiting(
            sessions,
            user_id="user-b",
            client_id="client-a",
            session_id="session-c",
            execution_id="exec-c",
            checkpoint_id="cp-c",
            capability_id="tool.c",
        )

        tickets = await store.load_pending_resume_tickets(
            owner_user_id="user-a",
            client_id="client-a",
        )

        assert len(tickets) == 1
        assert tickets[0] == {
            "execution_id": "exec-a",
            "checkpoint_id": "cp-a",
            "revision": 4,
            "wait_reason": "CONNECTION",
            "wait_expires_at": None,
            "origin_client_id": "client-a",
            "pending_capability_ids": ["tool.a"],
            "auto_resume_allowed": True,
        }

        assert await store.load_pending_resume_tickets(
            owner_user_id="user-b",
            client_id="client-b",
        ) == ()
    finally:
        await engine.dispose()
