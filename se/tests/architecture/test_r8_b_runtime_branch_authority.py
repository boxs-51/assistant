from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
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
from se.src.runtimes.agent.task_budget import TaskBudgetService


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


def _limits():
    return TaskBudgetLimits(
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=2,
        max_parallel_agents=4,
        max_total_tool_calls=32,
        max_total_inference_calls=32,
        max_total_tokens=10000,
        max_total_cost_usd="10",
        max_delegation_depth=4,
    )


async def _setup(tmp_path: Path):
    database = tmp_path / "r8_b_runtime.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r8-b-runtime"),
    )
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=DurableAgentStore(factory),
        task_budget_service=service,
    )
    return engine, sessions, service, runtime


async def _create_running_task(service: TaskBudgetService, task_id: str):
    await service.create_task_with_budget(
        {
            "id": task_id,
            "session_id": f"session-{task_id}",
            "created_by": "user-r8-b",
            "assigned_agent_id": "agent-root",
            "revision": 0,
            "status": "ASSIGNED",
            "wait_reasons": [],
            "input": {},
        }
    )
    await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )


def _context(
    task_id: str,
    execution_id: str,
    agent_id: str,
    *,
    parent_execution_id: str | None = None,
):
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id=agent_id,
        session_id=f"session-{task_id}",
        correlation_id=f"corr-{task_id}",
        identity=Identity(
            user_id="user-r8-b",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=30),
        task_id=task_id,
        parent_execution_id=parent_execution_id,
        input={"prompt": agent_id},
        remaining_active_budget_seconds=30.0,
        activate_budget=False,
    )


@pytest.mark.asyncio
async def test_r8_b_runtime_handoff_sets_root_and_delegated_branch_authority(
    tmp_path,
):
    engine, sessions, service, runtime = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-runtime")

        root = _context(
            "task-runtime",
            "exec-root",
            "agent-root",
        )
        assert root.branch_id is None
        assert await runtime._begin_durable_execution(root) == 1
        assert root.branch_id is not None

        child = _context(
            "task-runtime",
            "exec-child",
            "agent-child",
            parent_execution_id="exec-root",
        )
        assert child.branch_id is None
        assert await runtime._begin_durable_execution(child) == 1
        assert child.branch_id == root.branch_id

        budget = await service.get_budget("task-runtime")
        assert budget.active_branches == 1
        assert budget.used_executions == 2
        assert budget.active_executions == 2
        assert budget.active_parallel_agents == 1

        async with sessions() as session:
            repo = AgentRepository(session)
            branch = await repo.get_task_branch(root.branch_id)
            root_record = await repo.get_execution("exec-root")
            child_record = await repo.get_execution("exec-child")

            assert branch.current_execution_id == "exec-root"
            assert root_record.branch_id == root.branch_id
            assert child_record.branch_id == root.branch_id
            assert child_record.parent_execution_id == "exec-root"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_b_r7_resume_keeps_same_branch_without_new_branch_charge(
    tmp_path,
):
    engine, sessions, service, runtime = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-resume")
        context = _context(
            "task-resume",
            "exec-resume",
            "agent-root",
        )
        assert await runtime._begin_durable_execution(context) == 1
        branch_id = context.branch_id

        assert await service.finish_task_scoped_execution(
            "task-resume",
            execution_id="exec-resume",
            source_revision=1,
            transition_values={
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 30.0,
                "completed_at": None,
            },
            delegated=False,
        ) == 2

        before = await service.get_budget("task-resume")
        assert before.active_branches == 1
        assert before.active_executions == 0

        context.resume_revision = 2
        assert await runtime._begin_durable_execution(context) == 3
        assert context.branch_id == branch_id

        after = await service.get_budget("task-resume")
        assert after.active_branches == 1
        assert after.active_executions == 1
        assert after.used_executions == 1

        async with sessions() as session:
            branch_count = await session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_budget_reservations
                    WHERE task_id = 'task-resume'
                      AND kind = 'BRANCH'
                    """
                )
            )
            assert branch_count == 1

            execution = await AgentRepository(session).get_execution(
                "exec-resume"
            )
            assert execution.branch_id == branch_id
            assert execution.revision == 3
    finally:
        await engine.dispose()
