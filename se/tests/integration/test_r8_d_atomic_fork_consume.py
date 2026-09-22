from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import (
    TaskBudgetLimits,
    TaskBudgetPolicy,
)
from se.src.infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentIterationRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.contracts.fork import (
    ForkAdmission,
    fork_plan_fingerprint,
)
from se.src.runtimes.agent.fork_planning import AgentForkPlanningService
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import (
    ForkConsumeConflict,
    ForkConsumeError,
    TaskBudgetService,
)
from se.src.runtimes.agent.waiting_checkpoint import (
    WaitingCheckpointConflictError,
)


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


def _limits(**updates):
    values = {
        "max_total_executions": 8,
        "max_active_executions": 4,
        "max_active_branches": 4,
        "max_parallel_agents": 4,
        "max_total_tool_calls": 32,
        "max_total_inference_calls": 32,
        "max_total_tokens": 10000,
        "max_total_cost_usd": "10",
        "max_delegation_depth": 4,
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


async def _setup(tmp_path, *, name="r8_d.sqlite", limits=None):
    database = tmp_path / name
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
        default_limits=limits or _limits(),
        default_policy=TaskBudgetPolicy(version="r8-d-test"),
        max_conflict_retries=16,
    )
    store = DurableAgentStore(factory)
    planner = AgentForkPlanningService(store)
    return engine, sessions, service, planner


async def _seed_source(
    sessions,
    service,
    planner,
    *,
    task_id="task-r8-d",
    fork_request_id="fork-request-1",
    task_waiting=False,
):
    session_id = f"session-{task_id}"
    source_execution_id = f"exec-{task_id}"
    checkpoint_id = f"cp-{task_id}"

    await service.create_task_with_budget(
        {
            "id": task_id,
            "session_id": session_id,
            "created_by": "user-r8-d",
            "assigned_agent_id": "agent-r8-d",
            "revision": 0,
            "status": "ASSIGNED",
            "wait_reasons": [],
            "input": {"goal": task_id},
        }
    )
    await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )
    root = await service.start_root_task_scoped_execution(
        task_id,
        execution_id=source_execution_id,
        execution_values={
            "id": source_execution_id,
            "session_id": session_id,
            "agent_id": "agent-r8-d",
            "task_id": task_id,
            "parent_execution_id": None,
            "retry_of_execution_id": None,
            "base_execution_id": None,
            "base_checkpoint_id": None,
            "correlation_id": f"corr-{task_id}",
            "state": "RUNNING",
            "revision": 1,
            "remaining_active_budget_seconds": 30.0,
            "request": {"prompt": "source"},
            "started_at": datetime.now(timezone.utc),
        },
    )

    async with _Uow(sessions) as uow:
        uow.session.add(
            AgentIterationRecord(
                id=f"iter-{task_id}",
                execution_id=source_execution_id,
                iteration=1,
                state="WAITING",
                tool_call_ids=[],
            )
        )
        await uow.commit()

    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=source_execution_id,
        source_revision=1,
        transition_values={
            "state": "WAITING",
            "wait_reason": "RESOURCE",
            "remaining_active_budget_seconds": 30.0,
            "wait_expires_at": None,
            "completed_at": None,
        },
        delegated=False,
        checkpoint_values={
            "checkpoint_id": checkpoint_id,
            "execution_id": source_execution_id,
            "execution_revision": 2,
            "session_id": session_id,
            "task_id": task_id,
            "branch_id": root.branch_id,
            "iteration": 1,
            "wait_reason": "RESOURCE",
            "remaining_active_budget_seconds": 30.0,
            "wait_expires_at": None,
            "transcript_snapshot": [
                {
                    "role": "user",
                    "content": "base",
                }
            ],
            "metadata_json": {"phase": "r8-d"},
        },
        pending_invocations=(),
    ) == 2

    if task_waiting:
        await service.transition_task(
            task_id,
            allowed_source_states=("RUNNING",),
            target_state="WAITING",
            values={"wait_reasons": ["RESOURCE"]},
        )

    plan = await planner.build_fork_plan(
        fork_request_id=fork_request_id,
        task_id=task_id,
        source_branch_id=root.branch_id,
        source_execution_id=source_execution_id,
        source_checkpoint_id=checkpoint_id,
        target_user_id="user-r8-d",
        overlay_messages=(
            {
                "role": "user",
                "content": "fork-local",
            },
        ),
    )
    return {
        "task_id": task_id,
        "session_id": session_id,
        "source_execution_id": source_execution_id,
        "source_branch_id": root.branch_id,
        "checkpoint_id": checkpoint_id,
        "plan": plan,
    }


@pytest.mark.asyncio
async def test_r8_d_wrong_checkpoint_branch_rolls_back_waiting_transition(
    tmp_path,
):
    engine, sessions, service, _planner = await _setup(
        tmp_path,
        name="r8_d_checkpoint.sqlite",
    )
    try:
        task_id = "task-wrong-branch"
        await service.create_task_with_budget(
            {
                "id": task_id,
                "session_id": "session-wrong-branch",
                "created_by": "user-r8-d",
                "assigned_agent_id": "agent-r8-d",
                "revision": 0,
                "status": "RUNNING",
                "wait_reasons": [],
                "input": {},
            }
        )
        root = await service.start_root_task_scoped_execution(
            task_id,
            execution_id="exec-wrong-branch",
            execution_values={
                "id": "exec-wrong-branch",
                "session_id": "session-wrong-branch",
                "agent_id": "agent-r8-d",
                "task_id": task_id,
                "correlation_id": "corr-wrong-branch",
                "state": "RUNNING",
                "revision": 1,
                "request": {},
            },
        )
        with pytest.raises(
            WaitingCheckpointConflictError,
            match="branch_id",
        ):
            await service.finish_task_scoped_execution(
                task_id,
                execution_id="exec-wrong-branch",
                source_revision=1,
                transition_values={
                    "state": "WAITING",
                    "wait_reason": "RESOURCE",
                },
                delegated=False,
                checkpoint_values={
                    "checkpoint_id": "cp-wrong-branch",
                    "execution_id": "exec-wrong-branch",
                    "execution_revision": 2,
                    "session_id": "session-wrong-branch",
                    "task_id": task_id,
                    "branch_id": "branch-not-root",
                    "iteration": 1,
                    "wait_reason": "RESOURCE",
                    "transcript_snapshot": [],
                    "metadata_json": {},
                },
            )

        budget = await service.get_budget(task_id)
        async with _Uow(sessions) as uow:
            execution = await uow.agents.get_execution(
                "exec-wrong-branch"
            )
            checkpoint = await uow.agents.get_execution_checkpoint(
                "cp-wrong-branch"
            )
            assert execution.branch_id == root.branch_id
            assert execution.state == "RUNNING"
            assert execution.revision == 1
            assert checkpoint is None
            assert budget.active_executions == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_fresh_consume_is_one_atomic_fork(tmp_path):
    engine, sessions, service, planner = await _setup(tmp_path)
    try:
        source = await _seed_source(sessions, service, planner)
        before = await service.get_budget(source["task_id"])
        admission = await service.consume_fork_plan(source["plan"])
        after = await service.get_budget(source["task_id"])

        assert isinstance(admission, ForkAdmission)
        assert admission.branch_id != source["source_branch_id"]
        assert admission.execution_id != source["source_execution_id"]
        assert admission.branch_revision == 0
        assert admission.execution_revision == 1

        assert after.revision == before.revision + 1
        assert after.active_branches == before.active_branches + 1
        assert after.used_executions == before.used_executions + 1
        assert after.active_executions == before.active_executions + 1

        async with _Uow(sessions) as uow:
            source_branch = await uow.agents.get_task_branch(
                source["source_branch_id"]
            )
            source_execution = await uow.agents.get_execution(
                source["source_execution_id"]
            )
            branch = await uow.agents.get_task_branch(admission.branch_id)
            context = await uow.agents.get_task_branch_context(
                admission.branch_id
            )
            execution = await uow.agents.get_execution(
                admission.execution_id
            )
            receipt = await uow.agents.get_task_fork_admission(
                source["task_id"],
                source["plan"].fork_request_id,
            )
            branch_ledger = await uow.agents.get_task_budget_reservation(
                source["task_id"],
                "BRANCH",
                admission.branch_id,
            )
            execution_ledger = await uow.agents.get_task_budget_reservation(
                source["task_id"],
                "NEW_EXECUTION",
                admission.execution_id,
            )

            assert source_branch.current_execution_id == source[
                "source_execution_id"
            ]
            assert source_branch.revision == 0
            assert source_execution.state == "WAITING"
            assert source_execution.revision == 2
            assert source_execution.current_checkpoint_id == source[
                "checkpoint_id"
            ]

            assert branch.parent_branch_id == source["source_branch_id"]
            assert branch.base_execution_id == source[
                "source_execution_id"
            ]
            assert branch.base_checkpoint_id == source["checkpoint_id"]
            assert branch.current_execution_id == admission.execution_id
            assert branch.resolution_state == "OPEN"
            assert branch.reason == "R8_FORK"

            assert context.revision == 0
            assert context.overlay_messages[0]["content"] == "fork-local"

            assert execution.task_id == source["task_id"]
            assert execution.branch_id == admission.branch_id
            assert execution.parent_execution_id is None
            assert execution.retry_of_execution_id is None
            assert execution.base_execution_id == source[
                "source_execution_id"
            ]
            assert execution.base_checkpoint_id == source["checkpoint_id"]
            assert execution.state == "RUNNING"
            assert execution.revision == 1
            assert execution.current_checkpoint_id is None
            assert execution.bound_client_id is None
            assert execution.bound_connection_id is None
            assert execution.request == {"prompt": "source"}

            assert receipt.branch_id == admission.branch_id
            assert receipt.execution_id == admission.execution_id
            assert receipt.plan_fingerprint == source[
                "plan"
            ].plan_fingerprint
            assert branch_ledger is not None
            assert execution_ledger is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_waiting_task_returns_to_running_in_same_consume(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_d_waiting_task.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-waiting-fork",
            task_waiting=True,
        )
        async with _Uow(sessions) as uow:
            before_task = await uow.agents.get_task(source["task_id"])
            before_revision = before_task.revision
            assert before_task.status == "WAITING"

        admission = await service.consume_fork_plan(source["plan"])

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            execution = await uow.agents.get_execution(
                admission.execution_id
            )
            assert task.status == "RUNNING"
            assert task.wait_reasons == []
            assert task.revision == before_revision + 1
            assert execution.state == "RUNNING"
            assert admission.task_revision == task.revision
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_delegated_source_preserves_parent_and_parallel_charge(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_d_delegated.sqlite",
    )
    try:
        task_id = "task-delegated-fork"
        session_id = f"session-{task_id}"
        root_execution_id = "exec-delegated-root"
        child_execution_id = "exec-delegated-child"
        checkpoint_id = "cp-delegated-child"

        await service.create_task_with_budget(
            {
                "id": task_id,
                "session_id": session_id,
                "created_by": "user-r8-d",
                "assigned_agent_id": "agent-r8-d",
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
        root = await service.start_root_task_scoped_execution(
            task_id,
            execution_id=root_execution_id,
            execution_values={
                "id": root_execution_id,
                "session_id": session_id,
                "agent_id": "agent-r8-d",
                "task_id": task_id,
                "correlation_id": "corr-delegated",
                "state": "RUNNING",
                "revision": 1,
                "request": {"prompt": "root"},
            },
        )

        await service.start_task_scoped_execution(
            task_id,
            execution_id=child_execution_id,
            execution_values={
                "id": child_execution_id,
                "session_id": session_id,
                "agent_id": "agent-r8-d",
                "task_id": task_id,
                "branch_id": root.branch_id,
                "parent_execution_id": root_execution_id,
                "correlation_id": "corr-delegated",
                "state": "RUNNING",
                "revision": 1,
                "remaining_active_budget_seconds": 18.0,
                "request": {"prompt": "delegated-source"},
            },
            delegation_depth=1,
        )
        async with _Uow(sessions) as uow:
            uow.session.add(
                AgentIterationRecord(
                    id="iter-delegated-child",
                    execution_id=child_execution_id,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[],
                )
            )
            await uow.commit()

        assert await service.finish_task_scoped_execution(
            task_id,
            execution_id=child_execution_id,
            source_revision=1,
            transition_values={
                "state": "WAITING",
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 18.0,
                "completed_at": None,
            },
            delegated=True,
            checkpoint_values={
                "checkpoint_id": checkpoint_id,
                "execution_id": child_execution_id,
                "execution_revision": 2,
                "session_id": session_id,
                "task_id": task_id,
                "branch_id": root.branch_id,
                "iteration": 1,
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 18.0,
                "transcript_snapshot": [
                    {"role": "user", "content": "delegated-base"}
                ],
                "metadata_json": {},
            },
        ) == 2

        async with _Uow(sessions) as uow:
            promoted = await uow.agents.compare_and_set_task_branch(
                root.branch_id,
                0,
                {"current_execution_id": child_execution_id},
            )
            assert promoted is not None
            await uow.commit()

        plan = await planner.build_fork_plan(
            fork_request_id="fork-delegated",
            task_id=task_id,
            source_branch_id=root.branch_id,
            source_execution_id=child_execution_id,
            source_checkpoint_id=checkpoint_id,
            target_user_id="user-r8-d",
            overlay_messages=(),
        )

        before = await service.get_budget(task_id)
        assert before.active_parallel_agents == 0
        admission = await service.consume_fork_plan(plan)
        after = await service.get_budget(task_id)

        assert after.active_branches == before.active_branches + 1
        assert after.used_executions == before.used_executions + 1
        assert after.active_executions == before.active_executions + 1
        assert after.active_parallel_agents == 1

        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(admission.branch_id)
            forked = await uow.agents.get_execution(admission.execution_id)
            source = await uow.agents.get_execution(child_execution_id)

            assert branch.parent_branch_id == root.branch_id
            assert branch.base_execution_id == child_execution_id
            assert branch.base_checkpoint_id == checkpoint_id

            assert source.parent_execution_id == root_execution_id
            assert forked.parent_execution_id == root_execution_id
            assert forked.parent_execution_id != child_execution_id
            assert forked.retry_of_execution_id is None
            assert forked.base_execution_id == child_execution_id
            assert forked.base_checkpoint_id == checkpoint_id
            assert forked.branch_id == admission.branch_id
            assert forked.remaining_active_budget_seconds == 18.0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_same_request_replays_after_source_is_no_longer_forkable(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_d_replay.sqlite",
    )
    try:
        source = await _seed_source(sessions, service, planner)
        first = await service.consume_fork_plan(source["plan"])
        budget_before_replay = await service.get_budget(source["task_id"])

        async with _Uow(sessions) as uow:
            changed = await uow.agents.compare_and_set_execution(
                source["source_execution_id"],
                2,
                {
                    "state": "COMPLETED",
                    "wait_reason": None,
                    "completed_at": datetime.now(timezone.utc),
                },
            )
            assert changed is not None
            await uow.commit()

        replay = await service.consume_fork_plan(source["plan"])
        budget_after_replay = await service.get_budget(source["task_id"])

        assert replay.branch_id == first.branch_id
        assert replay.execution_id == first.execution_id
        assert budget_after_replay.revision == budget_before_replay.revision
        assert (
            budget_after_replay.active_branches
            == budget_before_replay.active_branches
        )
        assert (
            budget_after_replay.used_executions
            == budget_before_replay.used_executions
        )

        changed_plan = replace(
            source["plan"],
            overlay_messages=(
                {
                    "role": "user",
                    "content": "different semantics",
                    "tool_calls": [],
                    "name": None,
                    "tool_call_id": None,
                    "metadata": {},
                },
            ),
        )
        changed_plan = replace(
            changed_plan,
            plan_fingerprint=fork_plan_fingerprint(changed_plan),
        )
        with pytest.raises(
            ForkConsumeConflict,
            match="FORK_REQUEST_SEMANTIC_CONFLICT",
        ):
            await service.consume_fork_plan(changed_plan)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_concurrent_same_request_has_one_durable_winner(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_d_same_request_race.sqlite",
    )
    try:
        source = await _seed_source(sessions, service, planner)

        results = await asyncio.gather(
            service.consume_fork_plan(source["plan"]),
            service.consume_fork_plan(source["plan"]),
        )
        assert results[0].branch_id == results[1].branch_id
        assert results[0].execution_id == results[1].execution_id

        async with _Uow(sessions) as uow:
            branches = await uow.agents.list_task_branches(
                source["task_id"]
            )
            receipts = await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_fork_admissions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            )
            executions = await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_executions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            )
            assert len(branches) == 2
            assert receipts == 1
            assert executions == 2

        budget = await service.get_budget(source["task_id"])
        assert budget.active_branches == 2
        assert budget.used_executions == 2
        assert budget.active_executions == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_d_different_requests_at_final_capacity_have_one_winner(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_d_capacity_race.sqlite",
        limits=_limits(max_active_branches=2),
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            fork_request_id="fork-a",
        )
        second_plan = await planner.build_fork_plan(
            fork_request_id="fork-b",
            task_id=source["task_id"],
            source_branch_id=source["source_branch_id"],
            source_execution_id=source["source_execution_id"],
            source_checkpoint_id=source["checkpoint_id"],
            target_user_id="user-r8-d",
            overlay_messages=(
                {"role": "user", "content": "fork-local"},
            ),
        )

        results = await asyncio.gather(
            service.consume_fork_plan(source["plan"]),
            service.consume_fork_plan(second_plan),
            return_exceptions=True,
        )
        winners = [
            item for item in results
            if isinstance(item, ForkAdmission)
        ]
        losers = [
            item for item in results
            if isinstance(item, ForkConsumeError)
        ]
        assert len(winners) == 1
        assert len(losers) == 1

        async with _Uow(sessions) as uow:
            assert len(
                await uow.agents.list_task_branches(source["task_id"])
            ) == 2
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_fork_admissions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == 1

        budget = await service.get_budget(source["task_id"])
        assert budget.active_branches == 2
        assert budget.used_executions == 2
        assert budget.active_executions == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "task",
        "branch",
        "execution",
        "checkpoint",
        "transcript",
        "pending",
        "side-effect",
        "budget",
    ],
)
async def test_r8_d_revalidates_plan_inside_consume_transaction(
    tmp_path,
    mutation,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name=f"r8_d_stale_{mutation}.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id=f"task-stale-{mutation}",
        )

        async with _Uow(sessions) as uow:
            if mutation == "task":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_tasks
                        SET revision = revision + 1
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": source["task_id"]},
                )
            elif mutation == "branch":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_task_branches
                        SET revision = revision + 1
                        WHERE branch_id = :branch_id
                        """
                    ),
                    {"branch_id": source["source_branch_id"]},
                )
            elif mutation == "execution":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_executions
                        SET revision = revision + 1
                        WHERE id = :execution_id
                        """
                    ),
                    {"execution_id": source["source_execution_id"]},
                )
            elif mutation == "checkpoint":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_execution_checkpoints
                        SET iteration = iteration + 1
                        WHERE checkpoint_id = :checkpoint_id
                        """
                    ),
                    {"checkpoint_id": source["checkpoint_id"]},
                )
            elif mutation == "transcript":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_execution_checkpoints
                        SET transcript_snapshot = :snapshot
                        WHERE checkpoint_id = :checkpoint_id
                        """
                    ),
                    {
                        "checkpoint_id": source["checkpoint_id"],
                        "snapshot": json.dumps(
                            [{"role": "user", "content": "changed"}]
                        ),
                    },
                )
            elif mutation == "pending":
                uow.session.add(
                    AgentCheckpointPendingInvocationRecord(
                        checkpoint_id=source["checkpoint_id"],
                        ordinal=0,
                        invocation_id="inv-stale",
                        invocation_revision=1,
                        tool_call_id="call-stale",
                        capability_id="tool.stale",
                        capability_version="1",
                        request_fingerprint="s" * 64,
                        idempotency="IDEMPOTENT",
                        observed_remote_outcome_state="TERMINAL_COMMITTED",
                    )
                )
            elif mutation == "side-effect":
                uow.session.add(
                    CapabilityInvocationRecord(
                        invocation_id="inv-late-effect",
                        capability_id="tool.late",
                        capability_version="1",
                        kind="TOOL",
                        execution_mode="ONE_SHOT",
                        idempotency="IDEMPOTENT",
                        request_fingerprint="e" * 64,
                        remote_outcome_state="TERMINAL_COMMITTED",
                        driver_kind="LOCAL",
                        state="COMPLETED",
                        execution_id=source["source_execution_id"],
                        tool_call_id="call-late-effect",
                        attempt=1,
                        max_attempts=1,
                        arguments={},
                        output={"late": True},
                        revision=1,
                    )
                )
                uow.session.add(
                    AgentToolResultRecord(
                        id="result-late-effect",
                        execution_id=source["source_execution_id"],
                        iteration_id=f"iter-{source['task_id']}",
                        tool_call_id="call-late-effect",
                        invocation_id="inv-late-effect",
                        capability_id="tool.late",
                        success=True,
                        output={"late": True},
                        retryable=False,
                        commit_state="COMMITTED",
                        attempt=1,
                    )
                )
            elif mutation == "budget":
                await uow.session.execute(
                    text(
                        """
                        UPDATE agent_task_budgets
                        SET revision = revision + 1
                        WHERE task_id = :task_id
                        """
                    ),
                    {"task_id": source["task_id"]},
                )
            await uow.commit()

        with pytest.raises(ForkConsumeError):
            await service.consume_fork_plan(source["plan"])

        async with _Uow(sessions) as uow:
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_fork_admissions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == 0
            assert len(
                await uow.agents.list_task_branches(source["task_id"])
            ) == 1
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_executions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    [
        "task",
        "budget",
        "execution",
        "branch",
        "context",
        "branch-ledger",
        "execution-ledger",
        "receipt",
    ],
)
async def test_r8_d_failure_at_each_write_boundary_rolls_back_everything(
    tmp_path,
    failure_stage,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name=f"r8_d_rollback_{failure_stage}.sqlite",
    )
    service._max_conflict_retries = 2
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id=f"task-rollback-{failure_stage}",
            task_waiting=True,
        )
        before_budget = await service.get_budget(source["task_id"])

        async with _Uow(sessions) as uow:
            before_task = await uow.agents.get_task(source["task_id"])
            before_branch_count = len(
                await uow.agents.list_task_branches(source["task_id"])
            )
            before_execution_count = await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_executions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            )
            before_reservation_count = await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_budget_reservations
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            )

        task_id = source["task_id"]
        trigger = {
            "task": f"""
                CREATE TRIGGER fail_r8d_task
                BEFORE UPDATE ON agent_tasks
                WHEN OLD.id = '{task_id}' AND NEW.status = 'RUNNING'
                BEGIN SELECT RAISE(ABORT, 'fail task'); END
            """,
            "budget": f"""
                CREATE TRIGGER fail_r8d_budget
                BEFORE UPDATE ON agent_task_budgets
                WHEN OLD.task_id = '{task_id}'
                  AND NEW.active_branches > OLD.active_branches
                BEGIN SELECT RAISE(ABORT, 'fail budget'); END
            """,
            "execution": """
                CREATE TRIGGER fail_r8d_execution
                BEFORE INSERT ON agent_executions
                WHEN NEW.id LIKE 'r8_exec_%'
                BEGIN SELECT RAISE(ABORT, 'fail execution'); END
            """,
            "branch": """
                CREATE TRIGGER fail_r8d_branch
                BEFORE INSERT ON agent_task_branches
                WHEN NEW.reason = 'R8_FORK'
                BEGIN SELECT RAISE(ABORT, 'fail branch'); END
            """,
            "context": """
                CREATE TRIGGER fail_r8d_context
                BEFORE INSERT ON agent_task_branch_contexts
                WHEN NEW.branch_id LIKE 'r8_fork_%'
                BEGIN SELECT RAISE(ABORT, 'fail context'); END
            """,
            "branch-ledger": """
                CREATE TRIGGER fail_r8d_branch_ledger
                BEFORE INSERT ON agent_task_budget_reservations
                WHEN NEW.kind = 'BRANCH'
                  AND NEW.reservation_key LIKE 'r8_fork_%'
                BEGIN SELECT RAISE(ABORT, 'fail branch ledger'); END
            """,
            "execution-ledger": """
                CREATE TRIGGER fail_r8d_execution_ledger
                BEFORE INSERT ON agent_task_budget_reservations
                WHEN NEW.kind = 'NEW_EXECUTION'
                  AND NEW.reservation_key LIKE 'r8_exec_%'
                BEGIN SELECT RAISE(ABORT, 'fail execution ledger'); END
            """,
            "receipt": """
                CREATE TRIGGER fail_r8d_receipt
                BEFORE INSERT ON agent_task_fork_admissions
                BEGIN SELECT RAISE(ABORT, 'fail receipt'); END
            """,
        }[failure_stage]

        async with engine.begin() as connection:
            await connection.execute(text(trigger))

        with pytest.raises(Exception):
            await service.consume_fork_plan(source["plan"])

        after_budget = await service.get_budget(source["task_id"])
        assert after_budget.revision == before_budget.revision
        assert after_budget.active_branches == before_budget.active_branches
        assert after_budget.used_executions == before_budget.used_executions
        assert after_budget.active_executions == before_budget.active_executions

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            assert task.status == "WAITING"
            assert task.revision == before_task.revision
            assert task.wait_reasons == before_task.wait_reasons
            assert len(
                await uow.agents.list_task_branches(source["task_id"])
            ) == before_branch_count
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_executions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == before_execution_count
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_budget_reservations
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == before_reservation_count
            assert await uow.session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_task_fork_admissions
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": source["task_id"]},
            ) == 0
    finally:
        await engine.dispose()
