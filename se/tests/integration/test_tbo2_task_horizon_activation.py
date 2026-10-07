from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.models.sql.agent import (
    TaskBudgetReservationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.task_budget import (
    TaskBudgetError,
    TaskBudgetService,
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


@dataclass
class _FakeClock:
    value: float
    calls: int = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=1,
        max_total_tool_calls=8,
        max_total_inference_calls=8,
        max_total_tokens=1000,
        max_total_cost_usd="10",
        max_delegation_depth=4,
    )


async def _setup(tmp_path, *, now: float):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'tbo2.sqlite').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    clock = _FakeClock(now)
    service = TaskBudgetService(
        lambda: _Uow(sessions),
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="tbo2-test"),
        wall_clock=clock,
    )
    return engine, sessions, service, clock


def _task_values(
    task_id: str,
    *,
    task_horizon_at: float | None = None,
    review_horizon_at: float | None = None,
):
    return {
        "id": task_id,
        "session_id": f"session-{task_id}",
        "created_by": "user-tbo2",
        "assigned_agent_id": "agent-tbo2",
        "revision": 0,
        "task_mode": "FINITE",
        "task_horizon_at": task_horizon_at,
        "review_horizon_at": review_horizon_at,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"prompt": "hello"},
        "output": None,
        "error": None,
    }


async def _activate(service: TaskBudgetService, task_id: str):
    return await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )


async def _task_snapshot(sessions, task_id: str):
    async with sessions() as session:
        repo = AgentRepository(session)
        task = await repo.get_task(task_id)
        assert task is not None
        return (
            str(task.status),
            int(task.revision),
            list(task.wait_reasons or []),
            task.output,
            task.error,
        )


async def _budget_snapshot(service: TaskBudgetService, task_id: str):
    budget = await service.get_budget(task_id)
    assert budget is not None
    return (
        budget.revision,
        budget.state.value,
        budget.used_executions,
        budget.active_executions,
        budget.active_branches,
        budget.active_parallel_agents,
        budget.used_tool_calls,
        budget.used_inference_calls,
        budget.used_tokens,
        str(budget.used_cost_usd),
    )


async def _reservation_count(sessions, task_id: str) -> int:
    async with sessions() as session:
        rows = await session.execute(
            select(TaskBudgetReservationRecord).where(
                TaskBudgetReservationRecord.task_id == task_id
            )
        )
        return len(list(rows.scalars().all()))


@pytest.mark.asyncio
async def test_tbo2_null_horizons_preserve_assigned_to_running(tmp_path):
    engine, _sessions, service, clock = await _setup(tmp_path, now=100.0)
    try:
        await service.create_task_with_budget(_task_values("task-null"))
        task = await _activate(service, "task-null")

        assert str(task.status) == "RUNNING"
        assert task.revision == 1
        assert clock.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "horizon", "now", "expected_code", "should_run"),
    [
        ("task_horizon_at", 101.0, 100.0, None, True),
        (
            "task_horizon_at",
            100.0,
            100.0,
            "TASK_HORIZON_EXPIRED",
            False,
        ),
        (
            "task_horizon_at",
            99.0,
            100.0,
            "TASK_HORIZON_EXPIRED",
            False,
        ),
        ("review_horizon_at", 101.0, 100.0, None, True),
        ("review_horizon_at", 100.0, 100.0, "REVIEW_REQUIRED", False),
        ("review_horizon_at", 99.0, 100.0, "REVIEW_REQUIRED", False),
    ],
)
async def test_tbo2_horizon_boundary_matrix(
    tmp_path,
    field,
    horizon,
    now,
    expected_code,
    should_run,
):
    engine, sessions, service, clock = await _setup(tmp_path, now=now)
    task_id = f"task-{field}-{horizon}-{now}"
    values = _task_values(task_id)
    values[field] = horizon

    try:
        await service.create_task_with_budget(values)

        if should_run:
            task = await _activate(service, task_id)
            assert str(task.status) == "RUNNING"
            assert task.revision == 1
        else:
            before_task = await _task_snapshot(sessions, task_id)
            before_budget = await _budget_snapshot(service, task_id)
            before_reservations = await _reservation_count(sessions, task_id)

            with pytest.raises(TaskBudgetError) as exc_info:
                await _activate(service, task_id)

            assert exc_info.value.code == expected_code
            assert await _task_snapshot(sessions, task_id) == before_task
            assert await _budget_snapshot(service, task_id) == before_budget
            assert (
                await _reservation_count(sessions, task_id)
                == before_reservations
                == 0
            )
            async with sessions() as session:
                assert not await AgentRepository(session).has_execution_for_task(
                    task_id
                )

        assert clock.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo2_task_horizon_dominates_review_when_both_due(tmp_path):
    engine, sessions, service, clock = await _setup(tmp_path, now=100.0)
    try:
        await service.create_task_with_budget(
            _task_values(
                "task-both",
                task_horizon_at=100.0,
                review_horizon_at=100.0,
            )
        )
        before = await _task_snapshot(sessions, "task-both")

        with pytest.raises(TaskBudgetError) as exc_info:
            await _activate(service, "task-both")

        assert exc_info.value.code == "TASK_HORIZON_EXPIRED"
        assert await _task_snapshot(sessions, "task-both") == before
        assert clock.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo2_already_running_winner_is_not_retroactively_denied(tmp_path):
    engine, _sessions, service, clock = await _setup(tmp_path, now=99.0)
    try:
        await service.create_task_with_budget(
            _task_values("task-running", task_horizon_at=100.0)
        )
        first = await _activate(service, "task-running")
        assert str(first.status) == "RUNNING"
        assert first.revision == 1
        assert clock.calls == 1

        clock.value = 101.0
        second = await _activate(service, "task-running")

        assert str(second.status) == "RUNNING"
        assert second.revision == 1
        assert clock.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo2_terminal_winner_remains_authoritative_without_clock_sample(
    tmp_path,
):
    engine, _sessions, service, clock = await _setup(tmp_path, now=101.0)
    try:
        await service.create_task_with_budget(
            _task_values("task-terminal", task_horizon_at=100.0)
        )
        terminal = await service.terminalize_task(
            "task-terminal",
            allowed_source_states=("ASSIGNED",),
            target_state="CANCELLED",
            values={"error": "cancelled"},
        )
        assert str(terminal.status) == "CANCELLED"

        observed = await _activate(service, "task-terminal")

        assert str(observed.status) == "CANCELLED"
        assert observed.revision == terminal.revision
        assert clock.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tbo2_review_denial_does_not_create_waiting_projection(tmp_path):
    engine, sessions, service, clock = await _setup(tmp_path, now=100.0)
    try:
        await service.create_task_with_budget(
            _task_values("task-review", review_horizon_at=100.0)
        )

        with pytest.raises(TaskBudgetError) as exc_info:
            await _activate(service, "task-review")

        assert exc_info.value.code == "REVIEW_REQUIRED"
        task = await _task_snapshot(sessions, "task-review")
        assert task == ("ASSIGNED", 0, [], None, None)
        assert clock.calls == 1
    finally:
        await engine.dispose()
