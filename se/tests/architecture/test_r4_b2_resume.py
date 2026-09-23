from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import AgentExecutionContext
from se.src.runtimes.agent.persistence import ExecutionConflictError
from se.src.runtimes.agent.runtime import (
    AgentRuntime,
    ExecutionResumeBudgetError,
    ExecutionWaitExpiredError,
)


class _FakeClock:
    def __init__(
        self,
        *,
        monotonic: float = 100.0,
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


class _MemoryStore:
    def __init__(self, record) -> None:
        self.record = record
        self._lock = asyncio.Lock()

    async def load_execution(self, execution_id):
        await asyncio.sleep(0)
        if self.record.id != execution_id:
            return None
        return SimpleNamespace(**vars(self.record))

    async def save_execution(self, values):
        raise AssertionError("resume test must not create a new execution")

    async def compare_and_set_execution(
        self,
        execution_id,
        expected_revision,
        values,
    ):
        async with self._lock:
            if (
                self.record.id != execution_id
                or self.record.revision != expected_revision
            ):
                raise ExecutionConflictError("stale")
            for key, value in values.items():
                setattr(self.record, key, value)
            self.record.revision += 1
            return self.record


def _context(
    clock: _FakeClock,
    *,
    execution_id: str = "exec-r4-b2",
    revision: int = 7,
    remaining: float | None = 40.0,
    expiry: datetime | None = None,
    task_id: str | None = None,
) -> AgentExecutionContext:
    context = AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=60),
        task_id=task_id,
        remaining_active_budget_seconds=remaining,
        wait_expires_at=expiry,
        clock=clock,
        activate_budget=False,
    )
    context.resume_revision = revision
    return context


def _record(
    clock: _FakeClock,
    *,
    execution_id: str = "exec-r4-b2",
    revision: int = 7,
    remaining: float | None = 40.0,
    expiry: datetime | None = None,
):
    del clock
    return SimpleNamespace(
        id=execution_id,
        state="WAITING",
        wait_reason="CONNECTION",
        revision=revision,
        remaining_active_budget_seconds=remaining,
        wait_expires_at=expiry,
    )


class _ActivityBudget:
    def __init__(self) -> None:
        self.reconciled: list[str] = []

    async def reconcile_multibranch_task_activity(self, task_id: str):
        self.reconciled.append(task_id)
        return SimpleNamespace(id=task_id, status="WAITING")


def _runtime(store) -> AgentRuntime:
    return AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )


@pytest.mark.asyncio
async def test_r4_b2_resume_restores_exact_persisted_budget_after_claim():
    clock = _FakeClock(monotonic=500.0)
    expiry = clock.now_utc() + timedelta(hours=1)
    store = _MemoryStore(_record(clock, remaining=40.0, expiry=expiry))
    context = _context(clock, remaining=40.0, expiry=expiry)
    runtime = _runtime(store)

    assert context.active_budget_running is False
    clock.advance(300)
    assert context.remaining_active_seconds == 40.0

    revision = await runtime._begin_durable_execution(context)

    assert revision == 8
    assert store.record.state == "RUNNING"
    assert store.record.wait_reason is None
    assert store.record.wait_expires_at is None
    assert context.active_budget_running is True
    assert context.remaining_seconds == 40.0
    assert context.active_deadline_monotonic == clock.monotonic() + 40.0


@pytest.mark.asyncio
async def test_r4_b2_legacy_null_budget_fails_closed_without_mutation():
    clock = _FakeClock()
    store = _MemoryStore(_record(clock, remaining=None))
    context = _context(clock, remaining=None)
    runtime = _runtime(store)

    with pytest.raises(
        ExecutionResumeBudgetError,
        match="UNKNOWN_ACTIVE_BUDGET",
    ):
        await runtime._begin_durable_execution(context)

    assert store.record.state == "WAITING"
    assert store.record.revision == 7
    assert store.record.wait_reason == "CONNECTION"


@pytest.mark.asyncio
async def test_r4_b2_wait_ttl_allows_before_expiry_and_rejects_at_expiry():
    before_clock = _FakeClock()
    expiry = before_clock.now_utc() + timedelta(seconds=10)
    before_store = _MemoryStore(_record(before_clock, expiry=expiry))
    before_context = _context(before_clock, expiry=expiry)
    before_clock.advance(9.999)

    revision = await _runtime(before_store)._begin_durable_execution(
        before_context
    )
    assert revision == 8
    assert before_store.record.state == "RUNNING"

    at_clock = _FakeClock(wall=expiry)
    at_store = _MemoryStore(_record(at_clock, expiry=expiry))
    at_context = _context(at_clock, expiry=expiry)

    with pytest.raises(
        ExecutionWaitExpiredError,
        match="WAIT_TTL_EXPIRED",
    ):
        await _runtime(at_store)._begin_durable_execution(at_context)

    assert at_store.record.state == "TIMEOUT"
    assert at_store.record.revision == 8
    assert at_store.record.wait_reason is None
    assert at_store.record.wait_expires_at is None
    assert at_store.record.error == "WAIT_TTL_EXPIRED"


@pytest.mark.asyncio
async def test_r4_b2_two_valid_resume_claims_have_one_winner():
    clock = _FakeClock()
    expiry = clock.now_utc() + timedelta(hours=1)
    store = _MemoryStore(_record(clock, expiry=expiry))
    runtime = _runtime(store)
    first = _context(clock, expiry=expiry)
    second = _context(clock, expiry=expiry)

    outcomes = await asyncio.gather(
        runtime._begin_durable_execution(first),
        runtime._begin_durable_execution(second),
        return_exceptions=True,
    )

    assert sum(isinstance(item, int) for item in outcomes) == 1
    assert sum(
        isinstance(item, ExecutionConflictError)
        for item in outcomes
    ) == 1
    assert store.record.state == "RUNNING"
    assert store.record.revision == 8


@pytest.mark.asyncio
async def test_r8_f_task_scoped_direct_wait_expiry_reconciles_activity():
    clock = _FakeClock()
    expiry = clock.now_utc()
    store = _MemoryStore(_record(clock, expiry=expiry))
    budget = _ActivityBudget()
    context = _context(
        clock,
        expiry=expiry,
        task_id="task-r8-f-direct-expiry",
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
        task_budget_service=budget,
    )

    with pytest.raises(
        ExecutionWaitExpiredError,
        match="WAIT_TTL_EXPIRED",
    ):
        await runtime._begin_durable_execution(context)

    assert store.record.state == "TIMEOUT"
    assert store.record.revision == 8
    assert budget.reconciled == ["task-r8-f-direct-expiry"]
