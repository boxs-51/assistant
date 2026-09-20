from __future__ import annotations

from datetime import datetime, timezone

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
from se.src.runtimes.agent.contracts import AgentExecutionContext
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import (
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
        "max_total_executions": 4,
        "max_active_executions": 2,
        "max_active_branches": 2,
        "max_parallel_agents": 1,
        "max_total_tool_calls": 8,
        "max_total_inference_calls": 8,
        "max_total_tokens": 1000,
        "max_total_cost_usd": "10",
        "max_delegation_depth": 4,
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


async def _setup(tmp_path, *, limits=None):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r5c.sqlite').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = TaskBudgetService(
        lambda: _Uow(sessions),
        default_limits=limits or _limits(),
        default_policy=TaskBudgetPolicy(version="r5-c-test"),
    )
    return engine, sessions, service


def _task_values(task_id):
    return {
        "id": task_id,
        "session_id": f"session-{task_id}",
        "created_by": "user-r5-c",
        "assigned_agent_id": "agent-r5-c",
        "revision": 0,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"prompt": "hello"},
    }


def _execution_values(task_id, execution_id, *, parent=None):
    return {
        "id": execution_id,
        "session_id": f"session-{task_id}",
        "agent_id": "agent-r5-c",
        "task_id": task_id,
        "parent_execution_id": parent,
        "correlation_id": f"corr-{execution_id}",
        "state": "RUNNING",
        "revision": 1,
        "request": {"prompt": "hello"},
        "started_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_r5_c2_task_and_budget_creation_is_one_transaction(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_r5c_budget
                    BEFORE INSERT ON agent_task_budgets
                    WHEN NEW.task_id = 'task-rejected'
                    BEGIN
                        SELECT RAISE(ABORT, 'reject budget');
                    END
                    """
                )
            )

        with pytest.raises(Exception):
            await service.create_task_with_budget(
                _task_values("task-rejected")
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_task("task-rejected") is None
            assert await repo.get_task_budget("task-rejected") is None

        await service.create_task_with_budget(_task_values("task-ok"))
        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_task("task-ok") is not None
            assert await repo.get_task_budget("task-ok") is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_c3_c4_execution_slot_transitions_are_atomic(tmp_path):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-flow"))
        values = _execution_values("task-flow", "exec-flow")
        revision = await service.start_task_scoped_execution(
            "task-flow",
            execution_id="exec-flow",
            execution_values=values,
        )
        assert revision == 1

        budget = await service.get_budget("task-flow")
        assert budget.used_executions == 1
        assert budget.active_executions == 1

        revision = await service.finish_task_scoped_execution(
            "task-flow",
            execution_id="exec-flow",
            source_revision=1,
            transition_values={
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 30.0,
                "completed_at": None,
            },
            delegated=False,
        )
        assert revision == 2
        budget = await service.get_budget("task-flow")
        assert budget.used_executions == 1
        assert budget.active_executions == 0

        revision = await service.resume_task_scoped_execution(
            "task-flow",
            execution_id="exec-flow",
            source_revision=2,
            transition_values={
                "state": "RUNNING",
                "wait_reason": None,
                "started_at": datetime.now(timezone.utc),
            },
            delegated=False,
        )
        assert revision == 3
        budget = await service.get_budget("task-flow")
        assert budget.used_executions == 1
        assert budget.active_executions == 1

        revision = await service.finish_task_scoped_execution(
            "task-flow",
            execution_id="exec-flow",
            source_revision=3,
            transition_values={
                "state": "COMPLETED",
                "wait_reason": None,
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
        )
        assert revision == 4
        budget = await service.get_budget("task-flow")
        assert budget.used_executions == 1
        assert budget.active_executions == 0

        # Replaying the exact logical RESUME reservation after the execution
        # has progressed further is idempotent. This is required for
        # commit/ACK-loss recovery: the durable reservation ledger is the
        # authority that the source_revision=2 resume already committed.
        replay_revision = await service.resume_task_scoped_execution(
            "task-flow",
            execution_id="exec-flow",
            source_revision=2,
            transition_values={
                "state": "RUNNING",
                "wait_reason": None,
            },
            delegated=False,
        )
        assert replay_revision == 3
        replay_budget = await service.get_budget("task-flow")
        assert replay_budget.used_executions == 1
        assert replay_budget.active_executions == 0

        async with sessions() as session:
            replay_execution = await AgentRepository(session).get_execution(
                "exec-flow"
            )
            assert replay_execution.state == "COMPLETED"
            assert replay_execution.revision == 4

        # A different, never-committed logical resume using an old source
        # revision remains a genuine stale CAS and must fail without changing
        # either the execution or TaskBudget.
        with pytest.raises(TaskBudgetConflictError):
            await service.resume_task_scoped_execution(
                "task-flow",
                execution_id="exec-flow",
                source_revision=3,
                transition_values={
                    "state": "RUNNING",
                    "wait_reason": None,
                },
                delegated=False,
            )
        budget_after_stale = await service.get_budget("task-flow")
        assert budget_after_stale.active_executions == 0
        assert budget_after_stale.used_executions == 1

        async with sessions() as session:
            stale_execution = await AgentRepository(session).get_execution(
                "exec-flow"
            )
            assert stale_execution.state == "COMPLETED"
            assert stale_execution.revision == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_c4_agent_runtime_reacquires_and_releases_same_slot(
    tmp_path,
):
    engine, sessions, service = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-runtime-resume"))
        await service.start_task_scoped_execution(
            "task-runtime-resume",
            execution_id="exec-runtime-resume",
            execution_values=_execution_values(
                "task-runtime-resume",
                "exec-runtime-resume",
            ),
        )
        await service.finish_task_scoped_execution(
            "task-runtime-resume",
            execution_id="exec-runtime-resume",
            source_revision=1,
            transition_values={
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 30.0,
                "completed_at": None,
            },
            delegated=False,
        )

        store = DurableAgentStore(lambda: _Uow(sessions))
        runtime = AgentRuntime(
            context_builder=None,
            inference=None,
            tool_execution=None,
            execution_policy=None,
            durable_store=store,
            task_budget_service=service,
        )
        context = AgentExecutionContext.create(
            execution_id="exec-runtime-resume",
            agent_id="agent-r5-c",
            session_id="session-task-runtime-resume",
            correlation_id="corr-runtime-resume",
            identity=Identity(
                user_id="user-r5-c",
                auth_type="api_key",
                scopes={"*"},
            ),
            limits=AgentExecutionLimits(timeout_seconds=30),
            task_id="task-runtime-resume",
            input={"prompt": "hello"},
            remaining_active_budget_seconds=30.0,
            activate_budget=False,
        )
        context.resume_revision = 2

        revision = await runtime._begin_durable_execution(context)
        assert revision == 3
        budget = await service.get_budget("task-runtime-resume")
        assert budget.used_executions == 1
        assert budget.active_executions == 1

        await runtime._cancel_durable_revision(
            context,
            revision,
            error_message="test cancel",
        )
        budget = await service.get_budget("task-runtime-resume")
        assert budget.used_executions == 1
        assert budget.active_executions == 0

        async with sessions() as session:
            execution = await AgentRepository(session).get_execution(
                "exec-runtime-resume"
            )
            assert execution.state == "CANCELLED"
            assert execution.revision == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_c3_budget_exhaustion_creates_no_execution(tmp_path):
    engine, sessions, service = await _setup(
        tmp_path,
        limits=_limits(
            max_total_executions=1,
            max_active_executions=1,
        ),
    )
    try:
        await service.create_task_with_budget(_task_values("task-limit"))
        await service.start_task_scoped_execution(
            "task-limit",
            execution_id="exec-first",
            execution_values=_execution_values(
                "task-limit",
                "exec-first",
            ),
        )

        with pytest.raises(TaskBudgetExceededError):
            await service.start_task_scoped_execution(
                "task-limit",
                execution_id="exec-second",
                execution_values=_execution_values(
                    "task-limit",
                    "exec-second",
                ),
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_execution("exec-second") is None
        budget = await service.get_budget("task-limit")
        assert budget.used_executions == 1
        assert budget.active_executions == 1
    finally:
        await engine.dispose()
