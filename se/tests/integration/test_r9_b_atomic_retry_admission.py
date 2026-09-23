from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from se.src.domain.schemas.task_budget import TaskBudgetPolicy
from se.src.runtimes.agent.contracts.retry import (
    RetryAdmission,
    retry_plan_fingerprint,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.retry_planning import AgentRetryPlanningService
from se.src.runtimes.agent.task_budget import (
    RetryConsumeConflict,
    TaskBudgetService,
)
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _limits,
    _runtime_context_state,
)

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _setup(tmp_path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    from se.src.infrastructure.storage.models.sql.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r9-b-test"),
        max_conflict_retries=24,
    )
    store = DurableAgentStore(factory)
    return engine, sessions, service, AgentRetryPlanningService(store)


async def _seed_failed_source(
    sessions,
    service,
    planner,
    *,
    task_id: str,
    retry_request_id: str = "retry-r9-b",
):
    session_id = f"session-{task_id}"
    execution_id = f"exec-{task_id}"
    await service.create_task_with_budget(
        {
            "id": task_id,
            "session_id": session_id,
            "created_by": "user-r9",
            "assigned_agent_id": "agent-r9",
            "revision": 0,
            "status": "RUNNING",
            "wait_reasons": [],
            "input": {"goal": task_id},
        }
    )
    root = await service.start_root_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        execution_values={
            "id": execution_id,
            "session_id": session_id,
            "agent_id": "agent-r9",
            "task_id": task_id,
            "parent_execution_id": None,
            "retry_of_execution_id": None,
            "base_execution_id": None,
            "base_checkpoint_id": None,
            "correlation_id": f"corr-{task_id}",
            "state": "RUNNING",
            "revision": 1,
            "remaining_active_budget_seconds": 30.0,
            "request": {"prompt": "retry me"},
            "context_state": _runtime_context_state(task_id),
            "started_at": datetime.now(timezone.utc),
        },
    )
    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        source_revision=1,
        transition_values={
            "state": "FAILED",
            "wait_reason": None,
            "error": "source failed",
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
    ) == 2
    plan = await planner.build_retry_plan(
        retry_request_id=retry_request_id,
        task_id=task_id,
        branch_id=root.branch_id,
        source_execution_id=execution_id,
        target_user_id="user-r9",
    )
    return root, plan


@pytest.mark.asyncio
async def test_r9_b_retry_is_new_execution_in_same_branch_and_one_transaction(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_b_happy.sqlite"
    )
    try:
        root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-b-happy"
        )
        before = await service.get_budget(plan.task_id)
        admission = await service.consume_retry_plan(plan)
        after = await service.get_budget(plan.task_id)

        assert isinstance(admission, RetryAdmission)
        assert admission.branch_id == root.branch_id
        assert admission.execution_id != plan.source_execution_id
        assert after.used_executions == before.used_executions + 1
        assert after.active_executions == before.active_executions + 1
        assert after.active_branches == before.active_branches

        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(root.branch_id)
            source = await uow.agents.get_execution(plan.source_execution_id)
            retry = await uow.agents.get_execution(admission.execution_id)
            receipt = await uow.agents.get_task_retry_admission(
                plan.task_id, plan.retry_request_id
            )
            reservation = await uow.agents.get_task_budget_reservation(
                plan.task_id, "NEW_EXECUTION", admission.execution_id
            )
            task = await uow.agents.get_task(plan.task_id)

        assert source.state == "FAILED"
        assert source.revision == plan.expected_execution_revision
        assert branch.current_execution_id == admission.execution_id
        assert retry.task_id == plan.task_id
        assert retry.branch_id == root.branch_id
        assert retry.retry_of_execution_id == plan.source_execution_id
        assert retry.parent_execution_id == plan.parent_execution_id
        assert retry.base_execution_id == plan.base_execution_id
        assert retry.base_checkpoint_id == plan.base_checkpoint_id
        assert retry.state == "RUNNING"
        assert retry.revision == 1
        assert receipt.execution_id == admission.execution_id
        assert reservation is not None
        assert task.status == "RUNNING"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_b_retry_replay_and_same_request_race_charge_once(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_b_replay_race.sqlite"
    )
    try:
        _root, plan = await _seed_failed_source(
            sessions,
            service,
            planner,
            task_id="task-r9-b-replay-race",
        )
        before = await service.get_budget(plan.task_id)
        results = await asyncio.gather(
            service.consume_retry_plan(plan),
            service.consume_retry_plan(plan),
        )
        replay = await service.consume_retry_plan(plan)
        after = await service.get_budget(plan.task_id)

        assert {item.execution_id for item in (*results, replay)} == {
            results[0].execution_id
        }
        assert after.used_executions == before.used_executions + 1
        assert after.active_executions == before.active_executions + 1
        async with _Uow(sessions) as uow:
            rows = await uow.session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_task_retry_admissions "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": plan.task_id},
            )
            assert rows.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_b_same_request_semantic_drift_conflicts(tmp_path):
    engine, _sessions, service, planner = await _setup(
        tmp_path, "r9_b_semantic_conflict.sqlite"
    )
    try:
        _root, plan = await _seed_failed_source(
            _sessions,
            service,
            planner,
            task_id="task-r9-b-semantic-conflict",
        )
        admission = await service.consume_retry_plan(plan)
        changed = replace(plan, fresh_active_budget_seconds=31.0)
        changed = replace(
            changed, plan_fingerprint=retry_plan_fingerprint(changed)
        )
        with pytest.raises(RetryConsumeConflict) as raised:
            await service.consume_retry_plan(changed)
        assert raised.value.code == "RETRY_REQUEST_CONFLICT"
        assert (await service.consume_retry_plan(plan)).execution_id == (
            admission.execution_id
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_b_receipt_insert_failure_rolls_back_entire_admission(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_b_rollback.sqlite"
    )
    try:
        root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-b-rollback"
        )
        before = await service.get_budget(plan.task_id)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TRIGGER reject_r9_retry_receipt "
                    "BEFORE INSERT ON agent_task_retry_admissions "
                    "BEGIN SELECT RAISE(ABORT, 'forced receipt failure'); END"
                )
            )

        with pytest.raises(RetryConsumeConflict) as raised:
            await service.consume_retry_plan(plan)
        assert raised.value.code == "RETRY_CONSUME_CONFLICT"

        after = await service.get_budget(plan.task_id)
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(root.branch_id)
            count = await uow.session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_executions "
                    "WHERE retry_of_execution_id = :source"
                ),
                {"source": plan.source_execution_id},
            )
            receipt = await uow.agents.get_task_retry_admission(
                plan.task_id, plan.retry_request_id
            )

        assert branch.current_execution_id == plan.source_execution_id
        assert count.scalar_one() == 0
        assert receipt is None
        assert after.used_executions == before.used_executions
        assert after.active_executions == before.active_executions
        assert after.active_branches == before.active_branches
    finally:
        await engine.dispose()
