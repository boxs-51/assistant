from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentTaskRecord,
    TaskBudgetRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
    LeaseAuthorityConflictError,
)
from se.src.runtimes.agent.recovery_control_plane import (
    H1ARecoveryControlPlane,
)
from se.src.runtimes.agent.safe_point_reconstruction import (
    SafePointReconstructionError,
)
from se.src.runtimes.agent.stale_lease_scanner import (
    StaleLeaseObservation,
    StaleLeaseScanCoordinator,
    StaleLeaseScanPolicy,
    StaleLeaseSweepStopReason,
)
from se.src.runtimes.agent.task_budget import TaskBudgetService


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


async def _setup(tmp_path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    return (
        engine,
        factory,
        DurableAgentStore(factory),
        TaskBudgetService(factory),
    )


async def _seed_execution(
    factory,
    *,
    execution_id: str,
    expiry: datetime,
    task_id: str | None = None,
    corrupt: bool = False,
    context_state: dict | None = None,
):
    async with factory() as uow:
        if task_id is not None:
            uow.session.add(
                AgentTaskRecord(
                    id=task_id,
                    session_id=f"session-{task_id}",
                    created_by="user-h1a",
                    assigned_agent_id="agent-h1a",
                    status="RUNNING",
                    wait_reasons=[],
                    input={},
                )
            )
            uow.session.add(
                TaskBudgetRecord(
                    task_id=task_id,
                    incarnation_generation=1,
                    revision=0,
                    state="OPEN",
                    max_total_executions=10,
                    max_active_executions=4,
                    max_active_branches=4,
                    max_parallel_agents=4,
                    max_total_tool_calls=100,
                    max_total_inference_calls=100,
                    max_total_tokens=100000,
                    max_total_cost_usd=Decimal("100.00"),
                    max_delegation_depth=8,
                    policy_version="h1a",
                    policy_fingerprint="a" * 64,
                    deny_recursive_agent_cycle=True,
                    used_executions=3,
                    active_executions=1,
                    active_branches=1,
                    active_parallel_agents=0,
                    used_tool_calls=7,
                    used_inference_calls=5,
                    used_tokens=777,
                    used_cost_usd=Decimal("1.25000000"),
                )
            )

        uow.session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=(
                    f"session-{task_id}"
                    if task_id is not None
                    else f"session-{execution_id}"
                ),
                agent_id="agent-h1a",
                task_id=task_id,
                branch_id="branch-h1a" if task_id is not None else None,
                correlation_id=f"corr-{execution_id}",
                state="RUNNING",
                revision=5,
                owner_instance_id="dead-worker",
                lease_expires_at=expiry,
                lease_generation=3,
                remaining_active_budget_seconds=19.5,
                request={"prompt": "h1a"},
                context_state=context_state,
                transcript=(
                    ["not-a-message"]
                    if corrupt
                    else [{"role": "user", "content": "safe"}]
                ),
            )
        )
        uow.session.add(
            AgentIterationRecord(
                id=f"{execution_id}:iteration:1",
                execution_id=execution_id,
                iteration=1,
                state="RUNNING",
                tool_call_ids=[],
            )
        )
        await uow.commit()


def _control_plane(store, budget, *, max_rows: int = 1000):
    scanner = StaleLeaseScanCoordinator(
        store,
        policy=StaleLeaseScanPolicy(
            page_size=min(max_rows, 100),
            max_pages=10,
            max_rows=max_rows,
            max_duration_seconds=5.0,
        ),
    )
    return H1ARecoveryControlPlane(
        scanner,
        store,
        budget,
        sweep_interval_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_h1a_non_task_expired_running_evacuates_to_waiting(tmp_path):
    engine, factory, store, budget = await _setup(
        tmp_path, "h1a_non_task_waiting.sqlite"
    )
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        await _seed_execution(
            factory,
            execution_id="exec-h1a-waiting",
            expiry=expiry,
        )
        report = await _control_plane(store, budget).sweep_once()
        loaded = await store.load_execution("exec-h1a-waiting")

        assert report.waiting == 1
        assert report.failed == 0
        assert report.deferred == 0
        assert loaded.state == "WAITING"
        assert loaded.wait_reason == "RECOVERY"
        assert loaded.revision == 6
        assert loaded.owner_instance_id is None
        assert loaded.lease_expires_at is None
        assert loaded.lease_generation == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_h1a_allowlisted_non_task_corruption_evacuates_to_failed(tmp_path):
    engine, factory, store, budget = await _setup(
        tmp_path, "h1a_non_task_failed.sqlite"
    )
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        await _seed_execution(
            factory,
            execution_id="exec-h1a-failed",
            expiry=expiry,
            corrupt=True,
        )
        report = await _control_plane(store, budget).sweep_once()
        loaded = await store.load_execution("exec-h1a-failed")

        assert report.failed == 1
        assert report.waiting == 0
        assert report.deferred == 0
        assert loaded.state == "FAILED"
        assert loaded.revision == 6
        assert loaded.owner_instance_id is None
        assert loaded.lease_expires_at is None
        assert loaded.lease_generation == 4
        assert loaded.error == (
            "R12_STALE_RECOVERY_UNRECOVERABLE:"
            "SAFE_POINT_TRANSCRIPT_CORRUPT"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_h1a_non_task_failed_replay_binds_exact_expiry_receipt(tmp_path):
    engine, factory, store, _budget = await _setup(
        tmp_path, "h1a_non_task_exact_replay.sqlite"
    )
    now = datetime.now(timezone.utc)
    first_expiry = now + timedelta(seconds=10)
    renewed_expiry = now + timedelta(seconds=20)
    first_takeover = now + timedelta(seconds=15)
    renewed_takeover = now + timedelta(seconds=30)
    try:
        await _seed_execution(
            factory,
            execution_id="exec-h1a-exact-replay",
            expiry=first_expiry,
            context_state={"lifecycle": "preserve"},
        )

        async with factory() as uow:
            renewed = await uow.agents.renew_execution_lease(
                "exec-h1a-exact-replay",
                owner_instance_id="dead-worker",
                lease_generation=3,
                now_utc=now,
                new_lease_expires_at=renewed_expiry,
            )
            assert renewed is not None
            assert renewed.revision == 5
            assert renewed.lease_generation == 3
            await uow.commit()

        winner = await store.fail_expired_execution_unrecoverable(
            "exec-h1a-exact-replay",
            source_revision=5,
            observed_owner_instance_id="dead-worker",
            observed_lease_generation=3,
            observed_lease_expires_at=renewed_expiry,
            takeover_now_utc=renewed_takeover,
            reason_code="SAFE_POINT_TRANSCRIPT_CORRUPT",
        )
        assert winner.state == "FAILED"

        exact_replay = await store.fail_expired_execution_unrecoverable(
            "exec-h1a-exact-replay",
            source_revision=5,
            observed_owner_instance_id="dead-worker",
            observed_lease_generation=3,
            observed_lease_expires_at=renewed_expiry,
            takeover_now_utc=renewed_takeover,
            reason_code="SAFE_POINT_TRANSCRIPT_CORRUPT",
        )
        assert exact_replay.revision == 6

        with pytest.raises(
            LeaseAuthorityConflictError,
            match="RECOVERY_TERMINAL_REJECTED",
        ):
            await store.fail_expired_execution_unrecoverable(
                "exec-h1a-exact-replay",
                source_revision=5,
                observed_owner_instance_id="dead-worker",
                observed_lease_generation=3,
                observed_lease_expires_at=first_expiry,
                takeover_now_utc=first_takeover,
                reason_code="SAFE_POINT_TRANSCRIPT_CORRUPT",
            )

        loaded = await store.load_execution("exec-h1a-exact-replay")
        assert loaded.revision == 6
        assert loaded.lease_generation == 4
        assert loaded.context_state["lifecycle"] == "preserve"
        receipt = loaded.context_state["r12_h1a_terminal_receipt_v1"]
        assert receipt["observed_lease_expires_at"] == (
            renewed_expiry.isoformat()
        )
        assert receipt["takeover_now_utc"] == renewed_takeover.isoformat()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_h1a_task_corruption_fails_and_releases_capacity_once(tmp_path):
    engine, factory, store, budget = await _setup(
        tmp_path, "h1a_task_failed.sqlite"
    )
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        await _seed_execution(
            factory,
            execution_id="exec-h1a-task",
            expiry=expiry,
            task_id="task-h1a",
            corrupt=True,
        )
        report = await _control_plane(store, budget).sweep_once()
        assert report.failed == 1

        async with factory() as uow:
            execution = await uow.agents.get_execution("exec-h1a-task")
            task_budget = await uow.agents.get_task_budget("task-h1a")
            reservation = await uow.agents.get_task_budget_reservation(
                "task-h1a",
                "RELEASE_EXECUTION",
                "exec-h1a-task:6",
            )
            assert execution.state == "FAILED"
            assert execution.revision == 6
            assert execution.lease_generation == 4
            assert task_budget.active_executions == 0
            assert task_budget.used_executions == 3
            assert task_budget.used_tool_calls == 7
            assert task_budget.used_inference_calls == 5
            assert task_budget.used_tokens == 777
            assert task_budget.used_cost_usd == Decimal("1.25000000")
            assert task_budget.incarnation_generation == 1
            assert reservation is not None
            first_budget_revision = task_budget.revision
            replay_takeover = execution.completed_at
            if replay_takeover.tzinfo is None:
                replay_takeover = replay_takeover.replace(tzinfo=timezone.utc)
            await uow.commit()

        replay = await budget.fail_unrecoverable_task_scoped_execution(
            "task-h1a",
            execution_id="exec-h1a-task",
            source_revision=5,
            observed_owner_instance_id="dead-worker",
            observed_lease_generation=3,
            observed_lease_expires_at=expiry,
            takeover_now_utc=replay_takeover,
            reason_code="SAFE_POINT_TRANSCRIPT_CORRUPT",
        )
        assert replay == 6
        async with factory() as uow:
            task_budget = await uow.agents.get_task_budget("task-h1a")
            assert task_budget.active_executions == 0
            assert task_budget.revision == first_budget_revision
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_h1a_waiting_and_failed_same_receipt_have_one_durable_winner(
    tmp_path,
):
    engine, factory, store, _budget = await _setup(
        tmp_path, "h1a_waiting_failed_race.sqlite"
    )
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        await _seed_execution(
            factory,
            execution_id="exec-h1a-race",
            expiry=expiry,
        )

        async def waiting():
            return await store.commit_recovery_waiting_checkpoint(
                "exec-h1a-race",
                observed_owner_instance_id="dead-worker",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
                observed_revision=5,
            )

        async def failed():
            return await store.fail_expired_execution_unrecoverable(
                "exec-h1a-race",
                source_revision=5,
                observed_owner_instance_id="dead-worker",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
                reason_code="SAFE_POINT_TRANSCRIPT_CORRUPT",
            )

        await asyncio.gather(waiting(), failed(), return_exceptions=True)
        loaded = await store.load_execution("exec-h1a-race")
        assert loaded.state in {"WAITING", "FAILED"}
        assert loaded.revision == 6
        assert loaded.lease_generation == 4
        assert loaded.owner_instance_id is None
        assert loaded.lease_expires_at is None
    finally:
        await engine.dispose()


class _CursorStore:
    def __init__(self, rows):
        self.rows = tuple(rows)

    async def list_expired_execution_leases(
        self,
        *,
        cutoff_utc,
        limit,
        after_expiry=None,
        after_execution_id=None,
    ):
        rows = [
            row
            for row in self.rows
            if row.lease_expires_at <= cutoff_utc
            and (
                after_expiry is None
                or (row.lease_expires_at, row.id)
                > (after_expiry, after_execution_id)
            )
        ]
        rows.sort(key=lambda row: (row.lease_expires_at, row.id))
        return rows[:limit]


@pytest.mark.asyncio
async def test_h1a_cross_sweep_cursor_continues_then_wraps():
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id="a",
            owner_instance_id="owner",
            lease_generation=1,
            lease_expires_at=now - timedelta(minutes=2),
            state="RUNNING",
            revision=1,
        ),
        SimpleNamespace(
            id="b",
            owner_instance_id="owner",
            lease_generation=1,
            lease_expires_at=now - timedelta(minutes=1),
            state="RUNNING",
            revision=1,
        ),
    ]
    scanner = StaleLeaseScanCoordinator(
        _CursorStore(rows),
        policy=StaleLeaseScanPolicy(
            page_size=1,
            max_pages=1,
            max_rows=1,
            max_duration_seconds=5.0,
        ),
    )

    first = await scanner.scan_once()
    assert [item.execution_id for item in first.observations] == ["a"]
    assert first.stop_reason == StaleLeaseSweepStopReason.MAX_ROWS
    assert scanner.cursor[1] == "a"

    second = await scanner.scan_once()
    assert [item.execution_id for item in second.observations] == ["b"]
    assert second.stop_reason == StaleLeaseSweepStopReason.MAX_ROWS
    assert scanner.cursor[1] == "b"

    third = await scanner.scan_once()
    assert third.observations == ()
    assert third.stop_reason == StaleLeaseSweepStopReason.EXHAUSTED
    assert scanner.cursor == (None, None)

    fourth = await scanner.scan_once()
    assert [item.execution_id for item in fourth.observations] == ["a"]


@pytest.mark.asyncio
async def test_h1a_malformed_scan_row_isolated_and_revisited_after_wrap():
    now = datetime.now(timezone.utc)
    bad = SimpleNamespace(
        id="a",
        owner_instance_id=None,
        lease_generation=1,
        lease_expires_at=now - timedelta(minutes=2),
        state="RUNNING",
        revision=1,
    )
    good = SimpleNamespace(
        id="b",
        owner_instance_id="owner",
        lease_generation=1,
        lease_expires_at=now - timedelta(minutes=1),
        state="RUNNING",
        revision=1,
    )
    scanner = StaleLeaseScanCoordinator(
        _CursorStore([bad, good]),
        policy=StaleLeaseScanPolicy(
            page_size=2,
            max_pages=1,
            max_rows=2,
            max_duration_seconds=5.0,
        ),
    )

    first = await scanner.scan_once()
    assert [item.execution_id for item in first.observations] == ["b"]
    assert first.stop_reason == StaleLeaseSweepStopReason.MAX_ROWS
    assert len(first.observation_errors) == 1
    assert first.observation_errors[0].startswith("a:ValueError:")
    assert scanner.cursor[1] == "b"

    wrapped = await scanner.scan_once()
    assert wrapped.observations == ()
    assert wrapped.observation_errors == ()
    assert wrapped.stop_reason == StaleLeaseSweepStopReason.EXHAUSTED
    assert scanner.cursor == (None, None)

    revisited = await scanner.scan_once()
    assert [item.execution_id for item in revisited.observations] == ["b"]
    assert len(revisited.observation_errors) == 1
    assert revisited.observation_errors[0].startswith("a:ValueError:")


class _OneObservationScanner:
    def __init__(self, observation, cutoff):
        self.observation = observation
        self.cutoff = cutoff
        self.calls = 0
        self.second = asyncio.Event()

    async def scan_once(self):
        self.calls += 1
        if self.calls >= 2:
            self.second.set()
        return SimpleNamespace(
            scan_cutoff_utc=self.cutoff,
            observations=(self.observation,),
        )


class _UnknownReasonStore:
    def __init__(self, observation):
        self.current = SimpleNamespace(
            id=observation.execution_id,
            state="RUNNING",
            revision=observation.revision,
            owner_instance_id=observation.owner_instance_id,
            lease_generation=observation.lease_generation,
            lease_expires_at=observation.lease_expires_at,
            task_id=None,
        )
        self.failed_calls = 0

    async def load_execution(self, _execution_id):
        return self.current

    async def commit_recovery_waiting_checkpoint(self, *_args, **_kwargs):
        safe = SafePointReconstructionError(
            "SAFE_POINT_INVOCATION_REPOSITORY_MISSING: unavailable"
        )
        raise ExecutionConflictError(str(safe)) from safe

    async def fail_expired_execution_unrecoverable(self, *_args, **_kwargs):
        self.failed_calls += 1
        raise AssertionError("non-terminalizable reason reached FAILED seam")


class _UnusedBudget:
    pass


@pytest.mark.asyncio
async def test_h1a_non_terminal_safe_point_reason_stays_running():
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    observation = StaleLeaseObservation(
        execution_id="exec-unknown",
        owner_instance_id="owner",
        lease_generation=1,
        lease_expires_at=expiry,
        state="RUNNING",
        revision=2,
    )
    scanner = _OneObservationScanner(observation, expiry)
    store = _UnknownReasonStore(observation)
    control = H1ARecoveryControlPlane(
        scanner,
        store,
        _UnusedBudget(),
        sweep_interval_seconds=60,
    )

    report = await control.sweep_once()
    assert report.failed == 0
    assert report.deferred == 1
    assert store.failed_calls == 0
    assert store.current.state == "RUNNING"


class _LifecycleStore:
    def __init__(self, observation):
        self.current = SimpleNamespace(
            id=observation.execution_id,
            state="RUNNING",
            revision=observation.revision,
            owner_instance_id=observation.owner_instance_id,
            lease_generation=observation.lease_generation,
            lease_expires_at=observation.lease_expires_at,
            task_id=None,
        )

    async def load_execution(self, _execution_id):
        return self.current

    async def commit_recovery_waiting_checkpoint(self, *_args, **_kwargs):
        self.current.state = "WAITING"
        self.current.revision += 1
        self.current.owner_instance_id = None
        self.current.lease_expires_at = None
        self.current.lease_generation += 1
        return self.current


@pytest.mark.asyncio
async def test_h1a_start_is_immediate_periodic_and_quiesce_drains_worker():
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    observation = StaleLeaseObservation(
        execution_id="exec-lifecycle",
        owner_instance_id="owner",
        lease_generation=1,
        lease_expires_at=expiry,
        state="RUNNING",
        revision=2,
    )
    scanner = _OneObservationScanner(observation, expiry)
    store = _LifecycleStore(observation)
    control = H1ARecoveryControlPlane(
        scanner,
        store,
        _UnusedBudget(),
        sweep_interval_seconds=0.01,
    )

    first = await control.start()
    assert first.waiting == 1
    await asyncio.wait_for(scanner.second.wait(), timeout=1.0)
    await control.quiesce()
    calls_after_quiesce = scanner.calls
    await asyncio.sleep(0.03)
    assert scanner.calls == calls_after_quiesce
    assert scanner.calls >= 2
