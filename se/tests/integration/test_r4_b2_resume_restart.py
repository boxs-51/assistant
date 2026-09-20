from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import (
    AgentRuntime,
    ExecutionResumeBudgetError,
    ExecutionWaitExpiredError,
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


class _FakeClock:
    def __init__(
        self,
        *,
        monotonic: float = 500.0,
        wall: datetime | None = None,
    ) -> None:
        self.monotonic_value = monotonic
        self.wall_value = wall or datetime(
            2026, 9, 20, 8, 0, tzinfo=timezone.utc
        )

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_utc(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


async def _store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, DurableAgentStore(lambda: _SqliteUow(sessions))


async def _save_waiting(
    store,
    *,
    execution_id: str,
    remaining,
    expiry,
):
    await store.save_execution(
        {
            "id": execution_id,
            "session_id": "session-r4",
            "agent_id": "agent-r4",
            "correlation_id": "corr-r4",
            "state": "WAITING",
            "wait_reason": "CONNECTION",
            "revision": 7,
            "remaining_active_budget_seconds": remaining,
            "wait_expires_at": expiry,
            "request": {"prompt": "resume"},
            "context_state": {
                "limits": {"timeout_seconds": 60.0},
                "metadata": {"client_id": "client-r4"},
            },
        }
    )


def _runtime(store):
    return AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )


@pytest.mark.asyncio
async def test_r4_b2_restart_rehydrates_frozen_40s_then_claim_restores_exact_budget():
    engine, first_store = await _store()
    try:
        wall = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
        expiry = wall + timedelta(hours=1)
        await _save_waiting(
            first_store,
            execution_id="exec-restart",
            remaining=40.0,
            expiry=expiry,
        )

        restarted_store = DurableAgentStore(first_store.uow_factory)
        clock = _FakeClock(monotonic=9000.0, wall=wall)
        context = await restarted_store.resume_execution(
            "exec-restart",
            identity=Identity(user_id="user-r4", auth_type="jwt"),
            clock=clock,
        )

        assert context is not None
        assert context.active_budget_running is False
        assert context.remaining_active_seconds == 40.0

        clock.advance(600)
        assert context.remaining_active_seconds == 40.0

        revision = await _runtime(
            restarted_store
        )._begin_durable_execution(context)

        assert revision == 8
        assert context.active_budget_running is True
        assert context.remaining_seconds == 40.0
        assert context.active_deadline_monotonic == clock.monotonic() + 40.0
        record = await restarted_store.load_execution("exec-restart")
        assert record.state == "RUNNING"
        assert record.wait_expires_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r4_b2_restart_expiry_at_boundary_cas_waiting_to_timeout():
    engine, store = await _store()
    try:
        expiry = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
        await _save_waiting(
            store,
            execution_id="exec-expired",
            remaining=40.0,
            expiry=expiry,
        )
        restarted = DurableAgentStore(store.uow_factory)
        clock = _FakeClock(wall=expiry)
        context = await restarted.resume_execution(
            "exec-expired",
            clock=clock,
        )

        with pytest.raises(
            ExecutionWaitExpiredError,
            match="WAIT_TTL_EXPIRED",
        ):
            await _runtime(restarted)._begin_durable_execution(context)

        record = await restarted.load_execution("exec-expired")
        assert record.state == "TIMEOUT"
        assert record.revision == 8
        assert record.wait_reason is None
        assert record.wait_expires_at is None
        assert record.error == "WAIT_TTL_EXPIRED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r4_b2_restart_legacy_null_budget_remains_waiting_fail_closed():
    engine, store = await _store()
    try:
        await _save_waiting(
            store,
            execution_id="exec-legacy-null",
            remaining=None,
            expiry=None,
        )
        restarted = DurableAgentStore(store.uow_factory)
        clock = _FakeClock()
        context = await restarted.resume_execution(
            "exec-legacy-null",
            clock=clock,
        )

        assert context is not None
        assert context.remaining_active_budget_seconds is None
        assert context.active_budget_running is False

        with pytest.raises(
            ExecutionResumeBudgetError,
            match="UNKNOWN_ACTIVE_BUDGET",
        ):
            await _runtime(restarted)._begin_durable_execution(context)

        record = await restarted.load_execution("exec-legacy-null")
        assert record.state == "WAITING"
        assert record.revision == 7
        assert record.wait_reason == "CONNECTION"
    finally:
        await engine.dispose()
