from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import (
    AgentExecutionLimits,
    AgentExecutionWaitReason,
)
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts import (
    AgentExecutionContext,
    AgentExecutionResult,
    AgentLoopState,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.wait_policy import ConfiguredExecutionWaitPolicy


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


class _FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = datetime(
            2026, 9, 20, 7, 0, tzinfo=timezone.utc
        )

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_utc(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_r4_b1_real_sqlite_waiting_cas_persists_budget_and_expiry():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _SqliteUow(sessions))

    clock = _FakeClock()
    context = AgentExecutionContext.create(
        execution_id="exec-r4-b1-sql",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=60),
        clock=clock,
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
        wait_policy=ConfiguredExecutionWaitPolicy(
            {AgentExecutionWaitReason.CONNECTION: 3600}
        ),
    )

    try:
        revision = await runtime._begin_durable_execution(context)
        assert revision == 1

        clock.advance(20)
        expected_expiry = clock.now_utc() + timedelta(seconds=3600)
        await runtime._finish_durable_execution(
            context,
            AgentExecutionResult(
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                state=AgentLoopState.WAITING,
                wait_reason=AgentExecutionWaitReason.CONNECTION,
                error_code="WAITING_FOR_CONNECTION",
            ),
            revision,
        )

        record = await store.load_execution(context.execution_id)
        assert record is not None
        assert record.state == "WAITING"
        assert record.wait_reason == "CONNECTION"
        assert record.revision == 2
        assert record.remaining_active_budget_seconds == 40.0
        assert record.wait_expires_at is not None
        assert (
            record.wait_expires_at.replace(tzinfo=timezone.utc)
            == expected_expiry
        )
        assert record.completed_at is None
    finally:
        await engine.dispose()
