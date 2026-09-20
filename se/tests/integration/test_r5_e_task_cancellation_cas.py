from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
    TaskBudgetState,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import (
    TaskBudgetClosedError,
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


def _identity():
    return Identity(
        user_id="user-r5-e",
        auth_type="api_key",
        scopes={"*"},
    )


def _limits():
    return TaskBudgetLimits(
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=2,
        max_parallel_agents=2,
        max_total_tool_calls=16,
        max_total_inference_calls=16,
        max_total_tokens=1000,
        max_total_cost_usd=None,
        max_delegation_depth=4,
    )


async def _setup(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r5-e.sqlite').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        uow_factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r5-e-test"),
    )
    return engine, sessions, service, DurableAgentStore(uow_factory)


def _task_values(task_id):
    return {
        "id": task_id,
        "session_id": f"session-{task_id}",
        "created_by": "user-r5-e",
        "assigned_agent_id": "worker",
        "revision": 0,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"prompt": "hello"},
    }


@pytest.mark.asyncio
async def test_r5_e_terminal_task_and_budget_close_are_atomic(tmp_path):
    engine, sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-atomic"))
        await service.transition_task(
            "task-atomic",
            allowed_source_states=("ASSIGNED",),
            target_state="RUNNING",
        )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_r5e_budget_close
                    BEFORE UPDATE ON agent_task_budgets
                    WHEN NEW.task_id = 'task-atomic'
                         AND NEW.state = 'CLOSED'
                    BEGIN
                        SELECT RAISE(ABORT, 'reject close');
                    END
                    """
                )
            )

        with pytest.raises(Exception):
            await service.terminalize_task(
                "task-atomic",
                allowed_source_states=("RUNNING",),
                target_state="COMPLETED",
                values={"output": {"ok": True}},
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            task = await repo.get_task("task-atomic")
            budget = await repo.get_task_budget("task-atomic")
            assert task.status == "RUNNING"
            assert task.revision == 1
            assert budget.state == "OPEN"
            assert budget.closed_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_e_cancel_vs_complete_has_one_durable_terminal_winner(
    tmp_path,
):
    engine, sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-race"))
        await service.transition_task(
            "task-race",
            allowed_source_states=("ASSIGNED",),
            target_state="RUNNING",
        )

        cancelled, completed = await asyncio.gather(
            service.cancel_task("task-race"),
            service.terminalize_task(
                "task-race",
                allowed_source_states=("RUNNING",),
                target_state="COMPLETED",
                values={"output": {"ok": True}},
            ),
        )

        async with sessions() as session:
            repo = AgentRepository(session)
            task = await repo.get_task("task-race")
            budget = await repo.get_task_budget("task-race")
            assert task.status in {"CANCELLED", "COMPLETED"}
            assert budget.state == "CLOSED"
            assert budget.closed_at is not None

            # Both callers observe the same durable terminal winner rather
            # than overwriting one another.
            assert cancelled.status == task.status
            assert completed.status == task.status
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_e_closed_task_budget_blocks_new_execution(tmp_path):
    engine, sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-closed"))
        await service.cancel_task("task-closed")

        with pytest.raises(TaskBudgetClosedError):
            await service.start_task_scoped_execution(
                "task-closed",
                execution_id="exec-after-cancel",
                execution_values={
                    "id": "exec-after-cancel",
                    "session_id": "session-task-closed",
                    "agent_id": "worker",
                    "task_id": "task-closed",
                    "parent_execution_id": None,
                    "correlation_id": "corr-after-cancel",
                    "state": "RUNNING",
                    "revision": 1,
                    "request": {},
                },
            )

        async with sessions() as session:
            assert (
                await AgentRepository(session).get_execution(
                    "exec-after-cancel"
                )
                is None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_e_active_execution_can_release_after_budget_close(tmp_path):
    engine, _sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-release"))
        await service.transition_task(
            "task-release",
            allowed_source_states=("ASSIGNED",),
            target_state="RUNNING",
        )
        await service.start_task_scoped_execution(
            "task-release",
            execution_id="exec-release",
            execution_values={
                "id": "exec-release",
                "session_id": "session-task-release",
                "agent_id": "worker",
                "task_id": "task-release",
                "parent_execution_id": None,
                "correlation_id": "corr-release",
                "state": "RUNNING",
                "revision": 1,
                "request": {},
            },
        )
        await service.cancel_task("task-release")

        revision = await service.finish_task_scoped_execution(
            "task-release",
            execution_id="exec-release",
            source_revision=1,
            transition_values={
                "state": "CANCELLED",
                "wait_reason": None,
            },
            delegated=False,
        )
        assert revision == 2
        budget = await service.get_budget("task-release")
        assert budget.state is TaskBudgetState.CLOSED
        assert budget.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_e_closed_waiting_task_cannot_resume(tmp_path):
    engine, _sessions, service, _store = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(_task_values("task-wait"))
        await service.transition_task(
            "task-wait",
            allowed_source_states=("ASSIGNED",),
            target_state="RUNNING",
        )
        await service.start_task_scoped_execution(
            "task-wait",
            execution_id="exec-wait",
            execution_values={
                "id": "exec-wait",
                "session_id": "session-task-wait",
                "agent_id": "worker",
                "task_id": "task-wait",
                "parent_execution_id": None,
                "correlation_id": "corr-wait",
                "state": "RUNNING",
                "revision": 1,
                "request": {},
            },
        )
        await service.finish_task_scoped_execution(
            "task-wait",
            execution_id="exec-wait",
            source_revision=1,
            transition_values={
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 10.0,
            },
            delegated=False,
        )
        await service.transition_task(
            "task-wait",
            allowed_source_states=("RUNNING",),
            target_state="WAITING",
            values={"wait_reasons": ["CONNECTION"]},
        )
        await service.cancel_task("task-wait")

        with pytest.raises(TaskBudgetClosedError):
            await service.resume_task_scoped_execution(
                "task-wait",
                execution_id="exec-wait",
                source_revision=2,
                transition_values={
                    "state": "RUNNING",
                    "wait_reason": None,
                },
                delegated=False,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r5_e_coordinator_cancels_durably_before_local_drain(tmp_path):
    engine, sessions, service, store = await _setup(tmp_path)
    try:
        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                name="worker",
                goal="work",
                instruction="work",
            )
        )

        class Supervisor:
            def __init__(self):
                self.cancelled = []
                self.budget_state_at_cancel = None

            async def cancel_task(self, task_id):
                self.budget_state_at_cancel = (
                    await service.get_budget(task_id)
                ).state
                self.cancelled.append(task_id)

        supervisor = Supervisor()
        coordinator = MultiAgentCoordinator(
            registry,
            durable_store=store,
            execution_supervisor=supervisor,
            task_budget_service=service,
        )
        identity = _identity()
        session = coordinator.create_session(identity, ["worker"])
        task = await coordinator.create_task_async(
            session.session_id,
            "worker",
            {"prompt": "hello"},
            identity,
        )
        started = asyncio.Event()

        async def executor(
            _task,
            *,
            identity,
            execution_id,
            correlation_id,
            parent_execution_id=None,
        ):
            started.set()
            await asyncio.Event().wait()

        await coordinator.start_task(task.task_id, identity, executor)
        await asyncio.wait_for(started.wait(), timeout=1)

        cancelled = await coordinator.cancel_task_and_wait(
            task.task_id,
            identity,
        )

        assert cancelled.status.value == "CANCELLED"
        assert supervisor.cancelled == [task.task_id]
        assert supervisor.budget_state_at_cancel is TaskBudgetState.CLOSED

        async with sessions() as db:
            repo = AgentRepository(db)
            durable_task = await repo.get_task(task.task_id)
            durable_budget = await repo.get_task_budget(task.task_id)
            assert durable_task.status == "CANCELLED"
            assert durable_budget.state == "CLOSED"
            assert durable_budget.closed_at is not None
    finally:
        await engine.dispose()
