from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
)
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentTaskRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.task_budget import (
    DelegationDepthExceededError,
    TaskBudgetClosedError,
    TaskBudgetConflictError,
    TaskBudgetExceededError,
    TaskBudgetLegacyUninitializedError,
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


def _limits(**updates):
    values = {
        "max_total_executions": 4,
        "max_active_executions": 2,
        "max_active_branches": 2,
        "max_parallel_agents": 1,
        "max_total_tool_calls": 2,
        "max_total_inference_calls": 2,
        "max_delegation_depth": 2,
        "max_total_tokens": 100,
        "max_total_cost_usd": "1.0",
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


async def _setup(tmp_path):
    database = tmp_path / "r5_b_service.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = TaskBudgetService(lambda: _Uow(sessions))
    return engine, sessions, service


async def _add_task(
    sessions,
    task_id: str,
    *,
    status: str = "CREATED",
):
    async with sessions() as session:
        session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"session-{task_id}",
                created_by="user-r5-b",
                assigned_agent_id="agent-r5-b",
                status=status,
                input={},
            )
        )
        await session.commit()


def _execution_values(task_id: str, execution_id: str):
    return {
        "id": execution_id,
        "session_id": f"session-{task_id}",
        "agent_id": "agent-r5-b",
        "task_id": task_id,
        "parent_execution_id": None,
        "correlation_id": f"corr-{execution_id}",
        "state": "CREATED",
        "revision": 0,
        "request": {},
    }


@pytest.mark.asyncio
async def test_r5_b_pristine_task_initializes_once_and_policy_is_immutable(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-pristine")
        first = await service.ensure_budget(
            "task-pristine",
            _limits(),
            TaskBudgetPolicy(version="policy-1"),
        )
        second = await service.ensure_budget(
            "task-pristine",
            _limits(),
            TaskBudgetPolicy(version="policy-1"),
        )
        assert first.revision == second.revision == 0

        with pytest.raises(TaskBudgetConflictError):
            await service.ensure_budget(
                "task-pristine",
                _limits(max_total_executions=5),
                TaskBudgetPolicy(version="policy-1"),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_legacy_task_with_execution_history_fails_closed(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-legacy", status="RUNNING")
        async with sessions() as session:
            session.add(
                AgentExecutionRecord(
                    id="exec-legacy",
                    session_id="session-task-legacy",
                    agent_id="agent-r5-b",
                    task_id="task-legacy",
                    correlation_id="corr-legacy",
                    state="COMPLETED",
                    revision=2,
                    request={},
                )
            )
            await session.commit()

        with pytest.raises(TaskBudgetLegacyUninitializedError):
            await service.ensure_budget("task-legacy", _limits())
        assert await service.get_budget("task-legacy") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_new_execution_is_atomic_and_idempotent(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-exec")
        await service.ensure_budget("task-exec", _limits())
        values = _execution_values("task-exec", "exec-1")

        first = await service.reserve_new_execution(
            "task-exec",
            execution_id="exec-1",
            execution_values=values,
        )
        second = await service.reserve_new_execution(
            "task-exec",
            execution_id="exec-1",
            execution_values=values,
        )

        assert first.used_executions == 1
        assert first.active_executions == 1
        assert second.used_executions == 1
        assert second.active_executions == 1

        async with sessions() as session:
            execution = await AgentRepository(session).get_execution("exec-1")
            assert execution is not None
            assert execution.task_id == "task-exec"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_execution_insert_failure_rolls_back_budget_charge(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-rollback")
        await service.ensure_budget("task-rollback", _limits())
        values = _execution_values("task-rollback", "exec-existing")

        async with sessions() as session:
            session.add(AgentExecutionRecord(**values))
            await session.commit()

        with pytest.raises(TaskBudgetConflictError):
            await service.reserve_new_execution(
                "task-rollback",
                execution_id="exec-existing",
                execution_values=values,
            )

        budget = await service.get_budget("task-rollback")
        assert budget.used_executions == 0
        assert budget.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_logical_reservations_are_idempotent_and_payload_safe(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-idem")
        await service.ensure_budget("task-idem", _limits())

        first = await service.reserve_tool_calls(
            "task-idem",
            reservation_key="tool-call-1",
            count=1,
        )
        second = await service.reserve_tool_calls(
            "task-idem",
            reservation_key="tool-call-1",
            count=1,
        )
        assert first.used_tool_calls == second.used_tool_calls == 1

        with pytest.raises(TaskBudgetConflictError):
            await service.reserve_tool_calls(
                "task-idem",
                reservation_key="tool-call-1",
                count=2,
            )

        usage = await service.account_usage(
            "task-idem",
            usage_key="inference-1",
            tokens=7,
            cost_usd="0.123456789",
        )
        usage_again = await service.account_usage(
            "task-idem",
            usage_key="inference-1",
            tokens=7,
            cost_usd="0.123456789",
        )
        assert usage.used_tokens == usage_again.used_tokens == 7
        assert usage.used_cost_usd == Decimal("0.12345679")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_concurrent_reservations_cannot_exceed_limit(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-race")
        await service.ensure_budget(
            "task-race",
            _limits(max_total_tool_calls=1),
        )

        outcomes = await asyncio.gather(
            service.reserve_tool_calls(
                "task-race",
                reservation_key="tool-a",
                count=1,
            ),
            service.reserve_tool_calls(
                "task-race",
                reservation_key="tool-b",
                count=1,
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(
            isinstance(item, TaskBudgetExceededError)
            for item in outcomes
        ) == 1
        budget = await service.get_budget("task-race")
        assert budget.used_tool_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_close_blocks_growth_but_release_remains_idempotent(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-close")
        await service.ensure_budget("task-close", _limits())
        await service.reserve_new_execution(
            "task-close",
            execution_id="exec-close",
            execution_values=_execution_values(
                "task-close",
                "exec-close",
            ),
        )

        closed = await service.close("task-close")
        assert closed.state.value == "CLOSED"

        with pytest.raises(TaskBudgetClosedError):
            await service.reserve_inference(
                "task-close",
                request_id="request-after-close",
            )

        released = await service.release_active_execution(
            "task-close",
            execution_id="exec-close",
            target_revision=2,
            delegated=False,
        )
        released_again = await service.release_active_execution(
            "task-close",
            execution_id="exec-close",
            target_revision=2,
            delegated=False,
        )
        assert released.active_executions == 0
        assert released_again.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_parallel_agent_limit_counts_delegated_children_only(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-parallel")
        await service.ensure_budget(
            "task-parallel",
            _limits(
                max_active_executions=4,
                max_parallel_agents=1,
            ),
        )

        root_values = _execution_values("task-parallel", "exec-root")
        root = await service.reserve_new_execution(
            "task-parallel",
            execution_id="exec-root",
            execution_values=root_values,
            delegation_depth=0,
        )
        assert root.active_executions == 1
        assert root.active_parallel_agents == 0

        child_values = _execution_values("task-parallel", "exec-child")
        child_values["parent_execution_id"] = "exec-root"
        child = await service.reserve_new_execution(
            "task-parallel",
            execution_id="exec-child",
            execution_values=child_values,
            delegation_depth=1,
        )
        assert child.active_executions == 2
        assert child.active_parallel_agents == 1

        second_child = _execution_values(
            "task-parallel",
            "exec-child-2",
        )
        second_child["parent_execution_id"] = "exec-root"
        with pytest.raises(
            TaskBudgetExceededError,
            match="max_parallel_agents",
        ):
            await service.reserve_new_execution(
                "task-parallel",
                execution_id="exec-child-2",
                execution_values=second_child,
                delegation_depth=1,
            )

        released = await service.release_active_execution(
            "task-parallel",
            execution_id="exec-child",
            target_revision=2,
            delegated=True,
        )
        assert released.active_executions == 1
        assert released.active_parallel_agents == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_b_delegation_depth_rejection_does_not_charge_budget(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _add_task(sessions, "task-depth")
        await service.ensure_budget(
            "task-depth",
            _limits(max_delegation_depth=1),
        )
        values = _execution_values("task-depth", "exec-too-deep")
        values["parent_execution_id"] = "exec-parent"

        with pytest.raises(DelegationDepthExceededError):
            await service.reserve_new_execution(
                "task-depth",
                execution_id="exec-too-deep",
                execution_values=values,
                delegation_depth=2,
            )

        budget = await service.get_budget("task-depth")
        assert budget.used_executions == 0
        assert budget.active_executions == 0

        async with sessions() as session:
            execution = await AgentRepository(session).get_execution(
                "exec-too-deep"
            )
            assert execution is None
    finally:
        await engine.dispose()
