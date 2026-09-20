from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import (
    AgentExecution,
    AgentExecutionState,
    AgentExecutionWaitReason,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore


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


def _execution(**updates) -> AgentExecution:
    values = {
        "execution_id": "exec-r4-a1",
        "session_id": "session-r4",
        "agent_id": "agent-r4",
        "correlation_id": "corr-r4",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    values.update(updates)
    return AgentExecution(**values)


def test_r4_a1_domain_represents_budget_and_wait_expiry_without_runtime_policy():
    wait_expires_at = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    execution = _execution(
        state=AgentExecutionState.WAITING,
        wait_reason=AgentExecutionWaitReason.CONNECTION,
        remaining_active_budget_seconds=37.5,
        wait_expires_at=wait_expires_at,
    )

    assert execution.remaining_active_budget_seconds == 37.5
    assert execution.wait_expires_at == wait_expires_at

    legacy = _execution()
    assert legacy.remaining_active_budget_seconds is None
    assert legacy.wait_expires_at is None


@pytest.mark.asyncio
async def test_r4_a1_sql_round_trips_budget_and_wait_expiry_and_keeps_legacy_nulls():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _SqliteUow(sessions))

    wait_expires_at = datetime(2026, 9, 21, 0, 0)

    try:
        await store.save_execution(
            {
                "id": "exec-r4-durable",
                "session_id": "session-r4",
                "agent_id": "agent-r4",
                "correlation_id": "corr-r4",
                "state": AgentExecutionState.WAITING.value,
                "wait_reason": AgentExecutionWaitReason.CONNECTION.value,
                "revision": 3,
                "remaining_active_budget_seconds": 21.25,
                "wait_expires_at": wait_expires_at,
                "request": {},
                "context_state": {},
            }
        )

        record = await store.load_execution("exec-r4-durable")
        assert record is not None
        assert record.remaining_active_budget_seconds == 21.25
        assert record.wait_expires_at == wait_expires_at

        await store.save_execution(
            {
                "id": "exec-r4-legacy",
                "session_id": "session-r4",
                "agent_id": "agent-r4",
                "correlation_id": "corr-r4-legacy",
                "state": AgentExecutionState.WAITING.value,
                "wait_reason": AgentExecutionWaitReason.CONNECTION.value,
                "revision": 1,
                "request": {},
                "context_state": {},
            }
        )
        legacy = await store.load_execution("exec-r4-legacy")
        assert legacy is not None
        assert legacy.remaining_active_budget_seconds is None
        assert legacy.wait_expires_at is None
    finally:
        await engine.dispose()
