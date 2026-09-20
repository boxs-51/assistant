from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import (
    AgentDelegationCycleError,
    DelegationDepthExceededError,
    TaskBudgetConflictError,
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


def _limits(depth=2):
    return TaskBudgetLimits(
        max_total_executions=16,
        max_active_executions=16,
        max_active_branches=4,
        max_parallel_agents=16,
        max_total_tool_calls=64,
        max_total_inference_calls=64,
        max_total_tokens=10000,
        max_total_cost_usd=None,
        max_delegation_depth=depth,
    )


async def _setup(
    tmp_path: Path,
    *,
    depth=2,
    deny_recursive_agent_cycle=True,
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r5-d.sqlite').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        uow_factory,
        default_limits=_limits(depth),
        default_policy=TaskBudgetPolicy(
            version="r5-d-test",
            deny_recursive_agent_cycle=deny_recursive_agent_cycle,
        ),
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=DurableAgentStore(uow_factory),
        task_budget_service=service,
    )
    return engine, sessions, service, runtime


async def _create_task(service, task_id):
    await service.create_task_with_budget(
        {
            "id": task_id,
            "session_id": f"session-{task_id}",
            "created_by": "user-r5-d",
            "assigned_agent_id": "agent-root",
            "revision": 0,
            "status": "ASSIGNED",
            "wait_reasons": [],
            "input": {},
        }
    )


def _context(task_id, execution_id, agent_id, parent=None):
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id=agent_id,
        session_id=f"session-{task_id}",
        correlation_id=f"corr-{task_id}",
        identity=Identity(
            user_id="user-r5-d",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=30),
        task_id=task_id,
        parent_execution_id=parent,
        input={"prompt": agent_id},
    )


@pytest.mark.asyncio
async def test_r5_d_runtime_derives_depth_and_enforces_exact_cap(tmp_path):
    engine, sessions, service, runtime = await _setup(tmp_path, depth=2)
    try:
        await _create_task(service, "task-depth")

        assert await runtime._begin_durable_execution(
            _context("task-depth", "exec-a", "agent-a")
        ) == 1
        assert await runtime._begin_durable_execution(
            _context(
                "task-depth",
                "exec-b",
                "agent-b",
                parent="exec-a",
            )
        ) == 1
        assert await runtime._begin_durable_execution(
            _context(
                "task-depth",
                "exec-c",
                "agent-c",
                parent="exec-b",
            )
        ) == 1

        with pytest.raises(
            DelegationDepthExceededError,
            match="DELEGATION_DEPTH_EXCEEDED",
        ):
            await runtime._begin_durable_execution(
                _context(
                    "task-depth",
                    "exec-d",
                    "agent-d",
                    parent="exec-c",
                )
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_execution("exec-d") is None

        budget = await service.get_budget("task-depth")
        assert budget.used_executions == 3
        assert budget.active_executions == 3
        assert budget.active_parallel_agents == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_d_recursive_agent_cycle_is_rejected_without_charge(tmp_path):
    engine, sessions, service, runtime = await _setup(tmp_path, depth=8)
    try:
        await _create_task(service, "task-cycle")
        await runtime._begin_durable_execution(
            _context("task-cycle", "exec-a", "agent-a")
        )
        await runtime._begin_durable_execution(
            _context(
                "task-cycle",
                "exec-b",
                "agent-b",
                parent="exec-a",
            )
        )
        before = await service.get_budget("task-cycle")

        with pytest.raises(
            AgentDelegationCycleError,
            match="AGENT_DELEGATION_CYCLE",
        ):
            await runtime._begin_durable_execution(
                _context(
                    "task-cycle",
                    "exec-a2",
                    "agent-a",
                    parent="exec-b",
                )
            )

        after = await service.get_budget("task-cycle")
        assert after.used_executions == before.used_executions
        assert after.active_executions == before.active_executions
        async with sessions() as session:
            assert (
                await AgentRepository(session).get_execution("exec-a2")
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_d_cycle_policy_false_allows_repeated_agent_until_depth_cap(
    tmp_path,
):
    engine, _sessions, service, runtime = await _setup(
        tmp_path,
        depth=3,
        deny_recursive_agent_cycle=False,
    )
    try:
        await _create_task(service, "task-recursive")
        await runtime._begin_durable_execution(
            _context("task-recursive", "exec-a", "agent-a")
        )
        await runtime._begin_durable_execution(
            _context(
                "task-recursive",
                "exec-b",
                "agent-b",
                parent="exec-a",
            )
        )
        assert await runtime._begin_durable_execution(
            _context(
                "task-recursive",
                "exec-a2",
                "agent-a",
                parent="exec-b",
            )
        ) == 1
        budget = await service.get_budget("task-recursive")
        assert budget.used_executions == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_d_cross_task_parent_fails_closed(tmp_path):
    engine, sessions, service, runtime = await _setup(tmp_path, depth=8)
    try:
        await _create_task(service, "task-one")
        await _create_task(service, "task-two")
        await runtime._begin_durable_execution(
            _context("task-one", "exec-one", "agent-a")
        )

        with pytest.raises(
            TaskBudgetConflictError,
            match="different AgentTask",
        ):
            await runtime._begin_durable_execution(
                _context(
                    "task-two",
                    "exec-two",
                    "agent-b",
                    parent="exec-one",
                )
            )

        async with sessions() as session:
            assert (
                await AgentRepository(session).get_execution("exec-two")
                is None
            )
        budget = await service.get_budget("task-two")
        assert budget.used_executions == 0
        assert budget.active_executions == 0
    finally:
        await engine.dispose()
