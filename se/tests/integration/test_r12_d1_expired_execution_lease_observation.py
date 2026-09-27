from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

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


async def _store(tmp_path, *, name: str):
    database = tmp_path / name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))
    return engine, store


async def _seed(
    store: DurableAgentStore,
    execution_id: str,
    *,
    state: str = "RUNNING",
    owner_instance_id: str | None = None,
    lease_expires_at: datetime | None = None,
    lease_generation: int = 0,
    revision: int = 7,
):
    return await store.save_execution(
        {
            "id": execution_id,
            "session_id": "session-r12-d1",
            "agent_id": "agent-r12-d1",
            "task_id": "task-r12-d1",
            "branch_id": "branch-r12-d1",
            "parent_execution_id": "parent-r12-d1",
            "base_execution_id": "base-r12-d1",
            "base_checkpoint_id": "base-cp-r12-d1",
            "correlation_id": f"corr-{execution_id}",
            "state": state,
            "revision": revision,
            "owner_instance_id": owner_instance_id,
            "lease_expires_at": lease_expires_at,
            "lease_generation": lease_generation,
            "current_checkpoint_id": "checkpoint-r12-d1",
            "remaining_active_budget_seconds": 23.5,
            "request": {"prompt": "preserve"},
        }
    )


@pytest.mark.asyncio
async def test_r12_d1_filters_owned_expired_running_and_includes_cutoff(tmp_path):
    engine, store = await _store(tmp_path, name="r12_d1_filter.sqlite")
    cutoff = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
    try:
        await _seed(
            store,
            "exec-expired-a",
            owner_instance_id="worker-a",
            lease_expires_at=cutoff - timedelta(seconds=5),
            lease_generation=2,
        )
        await _seed(
            store,
            "exec-expired-boundary",
            owner_instance_id="worker-b",
            lease_expires_at=cutoff,
            lease_generation=3,
        )
        await _seed(
            store,
            "exec-active",
            owner_instance_id="worker-c",
            lease_expires_at=cutoff + timedelta(seconds=1),
            lease_generation=4,
        )
        await _seed(store, "exec-unowned")
        await _seed(
            store,
            "exec-waiting",
            state="WAITING",
            owner_instance_id="worker-d",
            lease_expires_at=cutoff - timedelta(seconds=10),
            lease_generation=5,
        )
        await _seed(
            store,
            "exec-terminal",
            state="SUCCEEDED",
            owner_instance_id="worker-e",
            lease_expires_at=cutoff - timedelta(seconds=10),
            lease_generation=6,
        )

        rows = await store.list_expired_execution_leases(
            cutoff_utc=cutoff,
            limit=20,
        )
        assert [row.id for row in rows] == [
            "exec-expired-a",
            "exec-expired-boundary",
        ]
        assert all(row.state == "RUNNING" for row in rows)
        assert all(row.owner_instance_id is not None for row in rows)
        assert all(row.lease_expires_at <= cutoff for row in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_d1_keyset_pagination_is_stable_without_duplicates(tmp_path):
    engine, store = await _store(tmp_path, name="r12_d1_page.sqlite")
    cutoff = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
    expiry = cutoff - timedelta(seconds=1)
    try:
        for execution_id in ("exec-a", "exec-b", "exec-c", "exec-d"):
            await _seed(
                store,
                execution_id,
                owner_instance_id=f"owner-{execution_id}",
                lease_expires_at=expiry,
                lease_generation=1,
            )

        first = await store.list_expired_execution_leases(
            cutoff_utc=cutoff,
            limit=2,
        )
        assert [row.id for row in first] == ["exec-a", "exec-b"]

        second = await store.list_expired_execution_leases(
            cutoff_utc=cutoff,
            limit=2,
            after_expiry=first[-1].lease_expires_at,
            after_execution_id=first[-1].id,
        )
        assert [row.id for row in second] == ["exec-c", "exec-d"]
        assert set(row.id for row in first).isdisjoint(
            row.id for row in second
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_d1_rejects_invalid_utc_cursor_and_limits(tmp_path):
    engine, store = await _store(tmp_path, name="r12_d1_validation.sqlite")
    cutoff = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)
    try:
        with pytest.raises(ValueError, match="cutoff_utc"):
            await store.list_expired_execution_leases(
                cutoff_utc=datetime(2026, 9, 27, 9, 0),
                limit=10,
            )
        with pytest.raises(ValueError, match="cutoff_utc"):
            await store.list_expired_execution_leases(
                cutoff_utc=datetime(
                    2026,
                    9,
                    27,
                    16,
                    0,
                    tzinfo=timezone(timedelta(hours=7)),
                ),
                limit=10,
            )
        with pytest.raises(ValueError, match="provided together"):
            await store.list_expired_execution_leases(
                cutoff_utc=cutoff,
                limit=10,
                after_execution_id="exec-a",
            )
        with pytest.raises(ValueError, match="provided together"):
            await store.list_expired_execution_leases(
                cutoff_utc=cutoff,
                limit=10,
                after_expiry=cutoff - timedelta(seconds=1),
            )
        with pytest.raises(ValueError, match="limit"):
            await store.list_expired_execution_leases(
                cutoff_utc=cutoff,
                limit=0,
            )
        with pytest.raises(ValueError, match="limit"):
            await store.list_expired_execution_leases(
                cutoff_utc=cutoff,
                limit=101,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_d1_observation_does_not_mutate_durable_execution(tmp_path):
    engine, store = await _store(tmp_path, name="r12_d1_immutable.sqlite")
    cutoff = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    expiry = cutoff - timedelta(seconds=30)
    try:
        before = await _seed(
            store,
            "exec-immutable",
            owner_instance_id="worker-immutable",
            lease_expires_at=expiry,
            lease_generation=9,
            revision=13,
        )
        snapshot = {
            "state": before.state,
            "owner_instance_id": before.owner_instance_id,
            "lease_expires_at": before.lease_expires_at,
            "lease_generation": before.lease_generation,
            "revision": before.revision,
            "current_checkpoint_id": before.current_checkpoint_id,
            "remaining_active_budget_seconds": before.remaining_active_budget_seconds,
            "request": before.request,
        }

        rows = await store.list_expired_execution_leases(
            cutoff_utc=cutoff,
            limit=1,
        )
        assert [row.id for row in rows] == ["exec-immutable"]
        assert rows[0].owner_instance_id == "worker-immutable"
        assert rows[0].lease_generation == 9
        assert rows[0].lease_expires_at == expiry

        after = await store.load_execution("exec-immutable")
        assert {
            "state": after.state,
            "owner_instance_id": after.owner_instance_id,
            "lease_expires_at": after.lease_expires_at,
            "lease_generation": after.lease_generation,
            "revision": after.revision,
            "current_checkpoint_id": after.current_checkpoint_id,
            "remaining_active_budget_seconds": after.remaining_active_budget_seconds,
            "request": after.request,
        } == snapshot
    finally:
        await engine.dispose()
