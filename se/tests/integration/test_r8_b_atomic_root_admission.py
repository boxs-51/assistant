from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.task_budget import (
    RootExecutionAdmission,
    TaskBudgetConflictError,
    TaskBudgetExceededError,
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
        "max_total_executions": 8,
        "max_active_executions": 4,
        "max_active_branches": 2,
        "max_parallel_agents": 4,
        "max_total_tool_calls": 32,
        "max_total_inference_calls": 32,
        "max_delegation_depth": 4,
        "max_total_tokens": 10000,
        "max_total_cost_usd": "10",
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


async def _setup(tmp_path, *, limits=None):
    database = tmp_path / "r8_b_atomic_root.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = TaskBudgetService(
        lambda: _Uow(sessions),
        default_limits=limits or _limits(),
        default_policy=TaskBudgetPolicy(version="r8-b-test"),
        max_conflict_retries=16,
    )
    return engine, sessions, service


def _task_values(task_id: str):
    return {
        "id": task_id,
        "session_id": f"session-{task_id}",
        "created_by": "user-r8-b",
        "assigned_agent_id": "agent-root",
        "revision": 0,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"prompt": "root"},
    }


def _execution_values(
    task_id: str,
    execution_id: str,
    *,
    parent: str | None = None,
    branch_id: str | None = None,
    agent_id: str = "agent-root",
):
    return {
        "id": execution_id,
        "session_id": f"session-{task_id}",
        "agent_id": agent_id,
        "task_id": task_id,
        "branch_id": branch_id,
        "parent_execution_id": parent,
        "retry_of_execution_id": None,
        "base_execution_id": None,
        "base_checkpoint_id": None,
        "correlation_id": f"corr-{execution_id}",
        "state": "RUNNING",
        "revision": 1,
        "request": {"prompt": execution_id},
        "started_at": datetime.now(timezone.utc),
    }


async def _create_running_task(service: TaskBudgetService, task_id: str):
    await service.create_task_with_budget(_task_values(task_id))
    await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )


@pytest.mark.asyncio
async def test_r8_b_root_admission_is_atomic_and_idempotent(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-root")
        values = _execution_values("task-root", "exec-root")

        first = await service.start_root_task_scoped_execution(
            "task-root",
            execution_id="exec-root",
            execution_values=values,
        )
        second = await service.start_root_task_scoped_execution(
            "task-root",
            execution_id="exec-root",
            execution_values=values,
        )

        assert isinstance(first, RootExecutionAdmission)
        assert second == first
        assert first.execution_revision == 1
        assert first.branch_revision == 0
        assert first.branch_id.startswith("r8_root_")

        budget = await service.get_budget("task-root")
        assert budget.revision == 1
        assert budget.active_branches == 1
        assert budget.used_executions == 1
        assert budget.active_executions == 1
        assert budget.active_parallel_agents == 0

        async with sessions() as session:
            repo = AgentRepository(session)
            branch = await repo.get_task_branch(first.branch_id)
            context = await repo.get_task_branch_context(first.branch_id)
            execution = await repo.get_execution("exec-root")
            branch_reservation = await repo.get_task_budget_reservation(
                "task-root",
                "BRANCH",
                first.branch_id,
            )
            execution_reservation = await repo.get_task_budget_reservation(
                "task-root",
                "NEW_EXECUTION",
                "exec-root",
            )

            assert branch.current_execution_id == "exec-root"
            assert branch.resolution_state == "OPEN"
            assert branch.reason == "R8_ROOT_FIRST_EXECUTION"
            assert context.overlay_messages == []
            assert execution.branch_id == first.branch_id
            assert execution.parent_execution_id is None
            assert branch_reservation is not None
            assert execution_reservation is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_b_concurrent_root_execution_ids_have_one_winner(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-race")

        async def admit(execution_id: str):
            return await service.start_root_task_scoped_execution(
                "task-race",
                execution_id=execution_id,
                execution_values=_execution_values(
                    "task-race",
                    execution_id,
                ),
            )

        results = await asyncio.gather(
            admit("exec-race-a"),
            admit("exec-race-b"),
            return_exceptions=True,
        )
        winners = [
            item for item in results
            if isinstance(item, RootExecutionAdmission)
        ]
        losers = [
            item for item in results
            if isinstance(item, TaskBudgetConflictError)
        ]
        assert len(winners) == 1
        assert len(losers) == 1

        winner = winners[0]
        budget = await service.get_budget("task-race")
        assert budget.active_branches == 1
        assert budget.used_executions == 1
        assert budget.active_executions == 1

        async with sessions() as session:
            repo = AgentRepository(session)
            branches = await repo.list_task_branches("task-race")
            assert len(branches) == 1
            assert branches[0].branch_id == winner.branch_id
            assert branches[0].current_execution_id == winner.execution_id

            count = await session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_executions
                    WHERE task_id = 'task-race'
                    """
                )
            )
            assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    [
        "budget",
        "execution",
        "branch",
        "context",
        "branch-ledger",
        "execution-ledger",
    ],
)
async def test_r8_b_root_admission_rolls_back_every_conceptual_write(
    tmp_path,
    failure_stage: str,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        task_id = f"task-rollback-{failure_stage}"
        execution_id = f"exec-rollback-{failure_stage}"
        await _create_running_task(service, task_id)

        trigger_sql = {
            "budget": f"""
                CREATE TRIGGER fail_r8b_budget
                BEFORE UPDATE ON agent_task_budgets
                WHEN NEW.task_id = '{task_id}'
                BEGIN
                    SELECT RAISE(ABORT, 'fail budget');
                END
            """,
            "execution": f"""
                CREATE TRIGGER fail_r8b_execution
                BEFORE INSERT ON agent_executions
                WHEN NEW.id = '{execution_id}'
                BEGIN
                    SELECT RAISE(ABORT, 'fail execution');
                END
            """,
            "branch": """
                CREATE TRIGGER fail_r8b_branch
                BEFORE INSERT ON agent_task_branches
                BEGIN
                    SELECT RAISE(ABORT, 'fail branch');
                END
            """,
            "context": """
                CREATE TRIGGER fail_r8b_context
                BEFORE INSERT ON agent_task_branch_contexts
                BEGIN
                    SELECT RAISE(ABORT, 'fail context');
                END
            """,
            "branch-ledger": """
                CREATE TRIGGER fail_r8b_branch_ledger
                BEFORE INSERT ON agent_task_budget_reservations
                WHEN NEW.kind = 'BRANCH'
                BEGIN
                    SELECT RAISE(ABORT, 'fail branch ledger');
                END
            """,
            "execution-ledger": """
                CREATE TRIGGER fail_r8b_execution_ledger
                BEFORE INSERT ON agent_task_budget_reservations
                WHEN NEW.kind = 'NEW_EXECUTION'
                BEGIN
                    SELECT RAISE(ABORT, 'fail execution ledger');
                END
            """,
        }[failure_stage]

        async with engine.begin() as connection:
            await connection.execute(text(trigger_sql))

        with pytest.raises(Exception):
            await service.start_root_task_scoped_execution(
                task_id,
                execution_id=execution_id,
                execution_values=_execution_values(
                    task_id,
                    execution_id,
                ),
            )

        budget = await service.get_budget(task_id)
        assert budget.revision == 0
        assert budget.active_branches == 0
        assert budget.used_executions == 0
        assert budget.active_executions == 0

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_execution(execution_id) is None
            assert await repo.list_task_branches(task_id) == []
            reservation_count = await session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_budget_reservations
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
            assert reservation_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_b_delegated_child_stays_in_root_branch_without_branch_charge(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-child")
        root = await service.start_root_task_scoped_execution(
            "task-child",
            execution_id="exec-parent",
            execution_values=_execution_values(
                "task-child",
                "exec-parent",
            ),
        )

        child_values = _execution_values(
            "task-child",
            "exec-child",
            parent="exec-parent",
            branch_id=root.branch_id,
            agent_id="agent-child",
        )
        assert await service.start_task_scoped_execution(
            "task-child",
            execution_id="exec-child",
            execution_values=child_values,
            delegation_depth=1,
        ) == 1

        budget = await service.get_budget("task-child")
        assert budget.active_branches == 1
        assert budget.used_executions == 2
        assert budget.active_executions == 2
        assert budget.active_parallel_agents == 1

        async with sessions() as session:
            repo = AgentRepository(session)
            branch = await repo.get_task_branch(root.branch_id)
            child = await repo.get_execution("exec-child")
            assert branch.current_execution_id == "exec-parent"
            assert child.branch_id == root.branch_id

        before = await service.get_budget("task-child")
        bad = _execution_values(
            "task-child",
            "exec-bad-child",
            parent="exec-parent",
            branch_id="branch-does-not-exist",
            agent_id="agent-bad",
        )
        with pytest.raises(TaskBudgetConflictError, match="TaskBranch"):
            await service.start_task_scoped_execution(
                "task-child",
                execution_id="exec-bad-child",
                execution_values=bad,
                delegation_depth=1,
            )
        after = await service.get_budget("task-child")
        assert after.revision == before.revision
        assert after.used_executions == before.used_executions
        assert after.active_executions == before.active_executions
        assert after.active_branches == before.active_branches
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_b_max_active_branches_blocks_root_without_partial_state(
    tmp_path,
):
    engine, sessions, service = await _setup(
        tmp_path,
        limits=_limits(max_active_branches=1),
    )
    try:
        await _create_running_task(service, "task-branch-limit")
        await service.reserve_branch_slot(
            "task-branch-limit",
            reservation_key="preexisting-capacity",
        )

        with pytest.raises(
            TaskBudgetExceededError,
            match="max_active_branches",
        ):
            await service.start_root_task_scoped_execution(
                "task-branch-limit",
                execution_id="exec-blocked",
                execution_values=_execution_values(
                    "task-branch-limit",
                    "exec-blocked",
                ),
            )

        budget = await service.get_budget("task-branch-limit")
        assert budget.active_branches == 1
        assert budget.used_executions == 0
        assert budget.active_executions == 0

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_execution("exec-blocked") is None
            assert await repo.list_task_branches("task-branch-limit") == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_b_second_top_level_execution_is_not_root_admission(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await _create_running_task(service, "task-second-root")
        await service.start_task_scoped_execution(
            "task-second-root",
            execution_id="exec-first",
            execution_values=_execution_values(
                "task-second-root",
                "exec-first",
            ),
        )
        before = await service.get_budget("task-second-root")

        with pytest.raises(
            TaskBudgetConflictError,
            match="already has a normalized TaskBranch",
        ):
            await service.start_task_scoped_execution(
                "task-second-root",
                execution_id="exec-second",
                execution_values=_execution_values(
                    "task-second-root",
                    "exec-second",
                ),
            )

        after = await service.get_budget("task-second-root")
        assert after.revision == before.revision
        assert after.used_executions == 1
        assert after.active_branches == 1
        async with sessions() as session:
            assert (
                await AgentRepository(session).get_execution("exec-second")
                is None
            )
    finally:
        await engine.dispose()
