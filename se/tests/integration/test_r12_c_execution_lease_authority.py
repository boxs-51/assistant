from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    LeaseAuthorityConflictError,
)


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _store(tmp_path, *, name: str = "r12_c.sqlite"):
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


async def _seed(store: DurableAgentStore, execution_id: str, *, state="RUNNING"):
    return await store.save_execution(
        {
            "id": execution_id,
            "session_id": "session-r12-c",
            "agent_id": "agent-r12-c",
            "task_id": "task-r12-c",
            "branch_id": "branch-r12-c",
            "parent_execution_id": "parent-r12-c",
            "base_execution_id": "base-r12-c",
            "base_checkpoint_id": "base-cp-r12-c",
            "correlation_id": "corr-r12-c",
            "state": state,
            "revision": 9,
            "current_checkpoint_id": "checkpoint-r12-c",
            "remaining_active_budget_seconds": 37.5,
            "request": {"prompt": "keep"},
        }
    )


@pytest.mark.asyncio
async def test_r12_c_competing_acquire_has_exactly_one_winner(tmp_path):
    engine, store = await _store(tmp_path, name="r12_c_race.sqlite")
    now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(seconds=30)
    try:
        await _seed(store, "exec-r12-c-race")

        async def attempt(owner: str):
            try:
                return await store.acquire_execution_lease(
                    "exec-r12-c-race",
                    owner_instance_id=owner,
                    now_utc=now,
                    lease_expires_at=expiry,
                )
            except LeaseAuthorityConflictError as exc:
                return exc

        results = await asyncio.gather(
            attempt("worker-r12-c-a"),
            attempt("worker-r12-c-b"),
        )

        winners = [
            item for item in results
            if not isinstance(item, LeaseAuthorityConflictError)
        ]
        losers = [
            item for item in results
            if isinstance(item, LeaseAuthorityConflictError)
        ]

        assert len(winners) == 1
        assert len(losers) == 1
        assert losers[0].code == "LEASE_ACQUIRE_REJECTED"

        winner = winners[0]
        assert winner.owner_instance_id in {
            "worker-r12-c-a",
            "worker-r12-c-b",
        }
        assert winner.lease_generation == 1
        assert winner.revision == 9

        loaded = await store.load_execution("exec-r12-c-race")
        assert loaded.owner_instance_id == winner.owner_instance_id
        assert loaded.lease_generation == 1
        assert loaded.revision == 9
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_c_acquire_renew_release_reacquire_preserves_semantics(tmp_path):
    engine, store = await _store(tmp_path, name="r12_c_lifecycle.sqlite")
    now = datetime(2026, 9, 27, 4, 0, tzinfo=timezone.utc)
    first_expiry = now + timedelta(seconds=30)
    renewed_expiry = now + timedelta(seconds=90)
    second_expiry = now + timedelta(seconds=120)
    try:
        before = await _seed(store, "exec-r12-c-life")

        acquired = await store.acquire_execution_lease(
            "exec-r12-c-life",
            owner_instance_id="worker-r12-c-1",
            now_utc=now,
            lease_expires_at=first_expiry,
        )
        assert acquired.lease_generation == 1
        assert acquired.revision == before.revision == 9
        assert _as_utc(acquired.lease_expires_at) == first_expiry

        renewed = await store.renew_execution_lease(
            "exec-r12-c-life",
            owner_instance_id="worker-r12-c-1",
            lease_generation=1,
            now_utc=now + timedelta(seconds=1),
            new_lease_expires_at=renewed_expiry,
        )
        assert renewed.lease_generation == 1
        assert renewed.revision == 9
        assert _as_utc(renewed.lease_expires_at) == renewed_expiry

        for field in (
            "state",
            "task_id",
            "branch_id",
            "parent_execution_id",
            "base_execution_id",
            "base_checkpoint_id",
            "current_checkpoint_id",
            "remaining_active_budget_seconds",
        ):
            assert getattr(renewed, field) == getattr(before, field)

        released = await store.release_execution_lease(
            "exec-r12-c-life",
            owner_instance_id="worker-r12-c-1",
            lease_generation=1,
        )
        assert released.owner_instance_id is None
        assert released.lease_expires_at is None
        assert released.lease_generation == 1
        assert released.revision == 9

        reacquired = await store.acquire_execution_lease(
            "exec-r12-c-life",
            owner_instance_id="worker-r12-c-2",
            now_utc=now + timedelta(seconds=2),
            lease_expires_at=second_expiry,
        )
        assert reacquired.owner_instance_id == "worker-r12-c-2"
        assert reacquired.lease_generation == 2
        assert reacquired.revision == 9

        with pytest.raises(LeaseAuthorityConflictError) as stale_release:
            await store.release_execution_lease(
                "exec-r12-c-life",
                owner_instance_id="worker-r12-c-1",
                lease_generation=1,
            )
        assert stale_release.value.code == "LEASE_RELEASE_REJECTED"

        loaded = await store.load_execution("exec-r12-c-life")
        assert loaded.owner_instance_id == "worker-r12-c-2"
        assert loaded.lease_generation == 2
        assert loaded.revision == 9
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_c_renew_and_fence_fail_closed_for_stale_or_expired_authority(
    tmp_path,
):
    engine, store = await _store(tmp_path, name="r12_c_fence.sqlite")
    now = datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(seconds=30)
    try:
        await _seed(store, "exec-r12-c-fence")
        acquired = await store.acquire_execution_lease(
            "exec-r12-c-fence",
            owner_instance_id="worker-r12-c",
            now_utc=now,
            lease_expires_at=expiry,
        )
        assert acquired.lease_generation == 1

        assert await store.has_active_execution_lease_fence(
            "exec-r12-c-fence",
            owner_instance_id="worker-r12-c",
            lease_generation=1,
            now_utc=now + timedelta(seconds=1),
        )
        assert not await store.has_active_execution_lease_fence(
            "exec-r12-c-fence",
            owner_instance_id="other-worker",
            lease_generation=1,
            now_utc=now + timedelta(seconds=1),
        )
        assert not await store.has_active_execution_lease_fence(
            "exec-r12-c-fence",
            owner_instance_id="worker-r12-c",
            lease_generation=2,
            now_utc=now + timedelta(seconds=1),
        )
        assert not await store.has_active_execution_lease_fence(
            "exec-r12-c-fence",
            owner_instance_id="worker-r12-c",
            lease_generation=1,
            now_utc=expiry,
        )

        with pytest.raises(LeaseAuthorityConflictError) as stale_generation:
            await store.renew_execution_lease(
                "exec-r12-c-fence",
                owner_instance_id="worker-r12-c",
                lease_generation=2,
                now_utc=now + timedelta(seconds=1),
                new_lease_expires_at=expiry + timedelta(seconds=30),
            )
        assert stale_generation.value.code == "LEASE_RENEW_REJECTED"

        with pytest.raises(LeaseAuthorityConflictError) as expired:
            await store.renew_execution_lease(
                "exec-r12-c-fence",
                owner_instance_id="worker-r12-c",
                lease_generation=1,
                now_utc=expiry,
                new_lease_expires_at=expiry + timedelta(seconds=30),
            )
        assert expired.value.code == "LEASE_RENEW_REJECTED"

        with pytest.raises(LeaseAuthorityConflictError) as takeover:
            await store.acquire_execution_lease(
                "exec-r12-c-fence",
                owner_instance_id="new-worker-r12-c",
                now_utc=expiry + timedelta(seconds=1),
                lease_expires_at=expiry + timedelta(seconds=60),
            )
        assert takeover.value.code == "LEASE_ACQUIRE_REJECTED"

        await store.save_execution(
            {
                "id": "exec-r12-c-non-running-fence",
                "session_id": "session-r12-c",
                "agent_id": "agent-r12-c",
                "correlation_id": "corr-r12-c-non-running",
                "state": "WAITING",
                "revision": 4,
                "owner_instance_id": "worker-r12-c",
                "lease_expires_at": expiry + timedelta(seconds=60),
                "lease_generation": 1,
                "request": {},
            }
        )
        assert not await store.has_active_execution_lease_fence(
            "exec-r12-c-non-running-fence",
            owner_instance_id="worker-r12-c",
            lease_generation=1,
            now_utc=now + timedelta(seconds=1),
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_c_rejects_invalid_time_owner_and_non_running_acquire(tmp_path):
    engine, store = await _store(tmp_path, name="r12_c_inputs.sqlite")
    now = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)
    try:
        await _seed(store, "exec-r12-c-inputs")
        await _seed(store, "exec-r12-c-waiting", state="WAITING")

        with pytest.raises(ValueError, match="non-empty string"):
            await store.acquire_execution_lease(
                "exec-r12-c-inputs",
                owner_instance_id=" ",
                now_utc=now,
                lease_expires_at=now + timedelta(seconds=30),
            )

        with pytest.raises(ValueError, match="timezone-aware UTC"):
            await store.acquire_execution_lease(
                "exec-r12-c-inputs",
                owner_instance_id="worker-r12-c",
                now_utc=now.replace(tzinfo=None),
                lease_expires_at=now + timedelta(seconds=30),
            )

        with pytest.raises(ValueError, match="timezone-aware UTC"):
            await store.acquire_execution_lease(
                "exec-r12-c-inputs",
                owner_instance_id="worker-r12-c",
                now_utc=now.astimezone(timezone(timedelta(hours=7))),
                lease_expires_at=now + timedelta(seconds=30),
            )

        with pytest.raises(ValueError, match="later than now_utc"):
            await store.acquire_execution_lease(
                "exec-r12-c-inputs",
                owner_instance_id="worker-r12-c",
                now_utc=now,
                lease_expires_at=now,
            )

        with pytest.raises(LeaseAuthorityConflictError) as waiting:
            await store.acquire_execution_lease(
                "exec-r12-c-waiting",
                owner_instance_id="worker-r12-c",
                now_utc=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
        assert waiting.value.code == "LEASE_ACQUIRE_REJECTED"
    finally:
        await engine.dispose()
