from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent import stale_lease_scanner as scanner_module
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.stale_lease_scanner import (
    StaleLeaseScanCoordinator,
    StaleLeaseScanPolicy,
    StaleLeaseSweepStopReason,
)


class _Clock:
    def __init__(
        self,
        now_utc: datetime,
        *,
        monotonic_values: tuple[float, ...] = (0.0,),
    ) -> None:
        self._now_utc = now_utc
        self._monotonic_values = list(monotonic_values)
        self._last_monotonic = self._monotonic_values[-1]
        self.now_calls = 0
        self.monotonic_calls = 0

    def now_utc(self) -> datetime:
        self.now_calls += 1
        return self._now_utc

    def monotonic(self) -> float:
        self.monotonic_calls += 1
        if self._monotonic_values:
            self._last_monotonic = self._monotonic_values.pop(0)
        return self._last_monotonic


def _row(
    execution_id: str,
    expiry: datetime,
    *,
    owner: str | None = None,
    generation: int = 1,
    state: str = "RUNNING",
    revision: int = 7,
):
    return SimpleNamespace(
        id=execution_id,
        owner_instance_id=owner or f"owner-{execution_id}",
        lease_generation=generation,
        lease_expires_at=expiry,
        state=state,
        revision=revision,
    )


class _ScriptedStore:
    def __init__(self, pages) -> None:
        self.pages = list(pages)
        self.calls: list[dict] = []

    async def list_expired_execution_leases(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self.pages:
            return []
        return self.pages.pop(0)


class _RepeatingStore:
    def __init__(self, page) -> None:
        self.page = list(page)
        self.calls: list[dict] = []

    async def list_expired_execution_leases(self, **kwargs):
        self.calls.append(dict(kwargs))
        return list(self.page)


class _GateStore:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def list_expired_execution_leases(self, **kwargs):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        await self.release.wait()
        self.active -= 1
        return []


class _HangingStore:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0

    async def list_expired_execution_leases(self, **kwargs):
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


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


async def _real_store(tmp_path, *, name: str):
    database = tmp_path / name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, DurableAgentStore(lambda: _Uow(sessions))


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
            "session_id": "session-r12-d2b",
            "agent_id": "agent-r12-d2b",
            "task_id": "task-r12-d2b",
            "branch_id": "branch-r12-d2b",
            "parent_execution_id": "parent-r12-d2b",
            "base_execution_id": "base-r12-d2b",
            "base_checkpoint_id": "base-r12-d2b",
            "correlation_id": f"corr-{execution_id}",
            "state": state,
            "revision": revision,
            "owner_instance_id": owner_instance_id,
            "lease_expires_at": lease_expires_at,
            "lease_generation": lease_generation,
            "current_checkpoint_id": "checkpoint-r12-d2b",
            "remaining_active_budget_seconds": 17.5,
            "request": {"preserve": execution_id},
        }
    )


def _durable_snapshot(record) -> dict:
    return {
        "state": record.state,
        "revision": record.revision,
        "owner_instance_id": record.owner_instance_id,
        "lease_expires_at": record.lease_expires_at,
        "lease_generation": record.lease_generation,
        "current_checkpoint_id": record.current_checkpoint_id,
        "remaining_active_budget_seconds": record.remaining_active_budget_seconds,
        "request": record.request,
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"page_size": 0}, "page_size"),
        ({"page_size": 101}, "page_size"),
        ({"page_size": True}, "page_size"),
        ({"max_pages": 0}, "max_pages"),
        ({"max_rows": 0}, "max_rows"),
        ({"max_duration_seconds": 0}, "max_duration_seconds"),
        ({"max_duration_seconds": True}, "max_duration_seconds"),
        ({"max_duration_seconds": "5"}, "max_duration_seconds"),
        ({"max_duration_seconds": float("inf")}, "max_duration_seconds"),
        ({"max_duration_seconds": float("nan")}, "max_duration_seconds"),
    ],
)
def test_r12_d2b_policy_is_finite_and_bounded(kwargs, match):
    with pytest.raises(ValueError, match=match):
        StaleLeaseScanPolicy(**kwargs)


@pytest.mark.asyncio
async def test_r12_d2b_uses_one_cutoff_and_exact_keyset_across_pages():
    cutoff = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    expiry_a = cutoff - timedelta(seconds=2)
    expiry_b = cutoff - timedelta(seconds=1)
    store = _ScriptedStore(
        [
            [_row("exec-a", expiry_a), _row("exec-b", expiry_b)],
            [_row("exec-c", expiry_b)],
        ]
    )
    clock = _Clock(
        cutoff,
        monotonic_values=(10.0, 10.0, 10.2, 10.4),
    )
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=2,
            max_pages=5,
            max_rows=10,
            max_duration_seconds=5.0,
        ),
        clock=clock,
    )

    result = await coordinator.scan_once()

    assert clock.now_calls == 1
    assert result.scan_cutoff_utc == cutoff
    assert result.stop_reason is StaleLeaseSweepStopReason.EXHAUSTED
    assert [item.execution_id for item in result.observations] == [
        "exec-a",
        "exec-b",
        "exec-c",
    ]
    assert [call["cutoff_utc"] for call in store.calls] == [cutoff, cutoff]
    assert store.calls[0]["after_expiry"] is None
    assert store.calls[0]["after_execution_id"] is None
    assert store.calls[1]["after_expiry"] == expiry_b
    assert store.calls[1]["after_execution_id"] == "exec-b"


@pytest.mark.asyncio
async def test_r12_d2b_max_pages_stops_exactly_after_full_page():
    cutoff = datetime(2026, 9, 27, 12, 30, tzinfo=timezone.utc)
    store = _ScriptedStore(
        [[
            _row("exec-a", cutoff - timedelta(seconds=2)),
            _row("exec-b", cutoff - timedelta(seconds=1)),
        ]]
    )
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=2,
            max_pages=1,
            max_rows=50,
            max_duration_seconds=5,
        ),
        clock=_Clock(cutoff),
    )

    result = await coordinator.scan_once()

    assert result.stop_reason is StaleLeaseSweepStopReason.MAX_PAGES
    assert result.pages_fetched == 1
    assert result.rows_observed == 2
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_r12_d2b_max_rows_uses_smaller_final_page_request():
    cutoff = datetime(2026, 9, 27, 13, 0, tzinfo=timezone.utc)
    expiry = cutoff - timedelta(seconds=1)
    store = _ScriptedStore(
        [
            [_row("exec-a", expiry), _row("exec-b", expiry), _row("exec-c", expiry)],
            [_row("exec-d", expiry)],
        ]
    )
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=3,
            max_pages=10,
            max_rows=4,
            max_duration_seconds=5,
        ),
        clock=_Clock(cutoff),
    )

    result = await coordinator.scan_once()

    assert result.stop_reason is StaleLeaseSweepStopReason.MAX_ROWS
    assert result.rows_observed == 4
    assert [call["limit"] for call in store.calls] == [3, 1]


@pytest.mark.asyncio
async def test_r12_d2b_monotonic_budget_stops_further_fetches_without_new_cutoff():
    cutoff = datetime(2026, 9, 27, 13, 30, tzinfo=timezone.utc)
    expiry = cutoff - timedelta(seconds=1)
    store = _ScriptedStore(
        [[_row("exec-a", expiry), _row("exec-b", expiry)]]
    )
    clock = _Clock(
        cutoff,
        monotonic_values=(0.0, 0.0, 2.0, 2.0),
    )
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=2,
            max_pages=10,
            max_rows=100,
            max_duration_seconds=1.0,
        ),
        clock=clock,
    )

    result = await coordinator.scan_once()

    assert result.stop_reason is StaleLeaseSweepStopReason.MAX_DURATION
    assert result.pages_fetched == 1
    assert clock.now_calls == 1
    assert len(store.calls) == 1
    assert store.calls[0]["cutoff_utc"] == cutoff


@pytest.mark.asyncio
async def test_r12_d2b_page_fetch_is_bounded_by_remaining_duration():
    cutoff = datetime(2026, 9, 27, 13, 45, tzinfo=timezone.utc)
    store = _HangingStore()
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=10,
            max_pages=10,
            max_rows=100,
            max_duration_seconds=0.05,
        ),
    )

    result = await asyncio.wait_for(coordinator.scan_once(), timeout=0.5)

    assert store.calls == 1
    assert store.entered.is_set()
    assert store.cancelled.is_set()
    assert result.stop_reason is StaleLeaseSweepStopReason.MAX_DURATION
    assert result.pages_fetched == 0
    assert result.observations == ()


@pytest.mark.asyncio
async def test_r12_d2b_concurrent_scans_are_serialized_per_coordinator():
    cutoff = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)
    store = _GateStore()
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=10,
            max_pages=1,
            max_rows=10,
            max_duration_seconds=5,
        ),
        clock=_Clock(cutoff),
    )

    first = asyncio.create_task(coordinator.scan_once())
    await store.entered.wait()
    second = asyncio.create_task(coordinator.scan_once())
    await asyncio.sleep(0)

    assert store.calls == 1
    assert store.max_active == 1

    store.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.stop_reason is StaleLeaseSweepStopReason.EXHAUSTED
    assert second_result.stop_reason is StaleLeaseSweepStopReason.EXHAUSTED
    assert store.calls == 2
    assert store.max_active == 1


@pytest.mark.asyncio
async def test_r12_d2b_duplicate_observations_across_sweeps_are_tolerated():
    cutoff = datetime(2026, 9, 27, 14, 30, tzinfo=timezone.utc)
    observation = _row("exec-repeat", cutoff - timedelta(seconds=1))
    store = _RepeatingStore([observation])
    coordinator = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=2,
            max_pages=2,
            max_rows=10,
            max_duration_seconds=5,
        ),
        clock=_Clock(cutoff),
    )

    first = await coordinator.scan_once()
    second = await coordinator.scan_once()

    assert [item.execution_id for item in first.observations] == ["exec-repeat"]
    assert [item.execution_id for item in second.observations] == ["exec-repeat"]


@pytest.mark.asyncio
async def test_r12_d2b_rejects_non_utc_scan_cutoff_before_store_access():
    store = _ScriptedStore([])
    coordinator = StaleLeaseScanCoordinator(
        store,
        clock=_Clock(
            datetime(
                2026,
                9,
                27,
                22,
                0,
                tzinfo=timezone(timedelta(hours=7)),
            )
        ),
    )

    with pytest.raises(ValueError, match="UTC offset zero"):
        await coordinator.scan_once()

    assert store.calls == []


@pytest.mark.asyncio
async def test_r12_d2b_real_d1_traversal_is_ordered_exclusive_and_zero_mutation(
    tmp_path,
):
    engine, store = await _real_store(tmp_path, name="r12_d2b_real.sqlite")
    cutoff = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    tied_expiry = cutoff - timedelta(seconds=5)
    try:
        await _seed(
            store,
            "exec-expired-a",
            owner_instance_id="worker-a",
            lease_expires_at=tied_expiry,
            lease_generation=2,
            revision=11,
        )
        await _seed(
            store,
            "exec-expired-b",
            owner_instance_id="worker-b",
            lease_expires_at=tied_expiry,
            lease_generation=3,
            revision=12,
        )
        await _seed(
            store,
            "exec-future",
            owner_instance_id="worker-future",
            lease_expires_at=cutoff + timedelta(seconds=1),
            lease_generation=4,
        )
        await _seed(store, "exec-unowned")
        await _seed(
            store,
            "exec-waiting",
            state="WAITING",
            owner_instance_id="worker-waiting",
            lease_expires_at=cutoff - timedelta(seconds=20),
            lease_generation=5,
        )
        await _seed(
            store,
            "exec-terminal",
            state="SUCCEEDED",
            owner_instance_id="worker-terminal",
            lease_expires_at=cutoff - timedelta(seconds=20),
            lease_generation=6,
        )

        ids = (
            "exec-expired-a",
            "exec-expired-b",
            "exec-future",
            "exec-unowned",
            "exec-waiting",
            "exec-terminal",
        )
        before = {
            execution_id: _durable_snapshot(
                await store.load_execution(execution_id)
            )
            for execution_id in ids
        }

        coordinator = StaleLeaseScanCoordinator(
            store,
            policy=StaleLeaseScanPolicy(
                page_size=1,
                max_pages=10,
                max_rows=10,
                max_duration_seconds=5,
            ),
            clock=_Clock(cutoff),
        )
        result = await coordinator.scan_once()

        assert result.stop_reason is StaleLeaseSweepStopReason.EXHAUSTED
        assert [item.execution_id for item in result.observations] == [
            "exec-expired-a",
            "exec-expired-b",
        ]
        assert all(item.state == "RUNNING" for item in result.observations)
        assert all(
            item.lease_expires_at <= cutoff
            for item in result.observations
        )
        assert all(
            item.owner_instance_id
            for item in result.observations
        )

        after = {
            execution_id: _durable_snapshot(
                await store.load_execution(execution_id)
            )
            for execution_id in ids
        }
        assert after == before
    finally:
        await engine.dispose()


def test_r12_d2b_source_has_no_activation_or_mutation_authority():
    source = inspect.getsource(scanner_module)

    assert "asyncio.create_task" not in source
    assert "create_task(" not in source
    assert "acquire_execution_lease(" not in source
    assert "renew_execution_lease(" not in source
    assert "release_execution_lease(" not in source
    assert "compare_and_set_execution(" not in source
    assert "commit_waiting_checkpoint(" not in source
    assert "list_expired_execution_leases(" in source
