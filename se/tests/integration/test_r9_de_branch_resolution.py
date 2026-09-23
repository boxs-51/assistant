from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.resume import ResumeClaimIntent, ResumeTriggerType
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_claim import ResumeClaimRejected
from se.src.runtimes.agent.retry_planning import AgentRetryPlanningService
from se.src.runtimes.agent.task_budget import BranchResolutionError
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _seed_source,
    _setup,
)


async def _seed_two_completed_branches(
    sessions,
    service,
    planner,
    *,
    task_id: str,
    with_claim: bool = False,
):
    source = await _seed_source(
        sessions,
        service,
        planner,
        task_id=task_id,
        fork_request_id=f"fork-{task_id}",
    )
    fork = await service.consume_fork_plan(source["plan"])

    assert await service.resume_task_scoped_execution(
        task_id,
        execution_id=source["source_execution_id"],
        source_revision=2,
        transition_values={
            "state": "RUNNING",
            "wait_reason": None,
            "current_checkpoint_id": None,
            "remaining_active_budget_seconds": 30.0,
            "wait_expires_at": None,
        },
        delegated=False,
    ) == 3
    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=source["source_execution_id"],
        source_revision=3,
        transition_values={
            "state": "COMPLETED",
            "wait_reason": None,
            "result": {"winner": "root"},
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
    ) == 4
    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=fork.execution_id,
        source_revision=1,
        transition_values={
            "state": "COMPLETED",
            "wait_reason": None,
            "result": {"winner": "fork"},
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
    ) == 2

    if with_claim:
        async with _Uow(sessions) as uow:
            await uow.agents.save_resume_claim(
                {
                    "claim_id": f"claim-{task_id}",
                    "execution_id": source["source_execution_id"],
                    "checkpoint_id": source["checkpoint_id"],
                    "resume_request_id": f"resume-{task_id}",
                    "expected_execution_revision": 2,
                    "user_id": "user-r8-d",
                    "client_id": None,
                    "connection_id": None,
                    "wait_reason": "RESOURCE",
                    "trigger_type": "EXPLICIT",
                    "state": "CREATED",
                    "revision": 0,
                    "plan_fingerprint": "a" * 64,
                    "rejection_code": None,
                    "metadata_json": {},
                    "claim_expires_at": (
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ),
                }
            )
            await uow.commit()
    return source, fork


@pytest.mark.asyncio
async def test_r9_d_discard_is_monotonic_accounted_once_and_keeps_task_open(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_d_discard.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r9-d-discard",
        )
        fork = await service.consume_fork_plan(source["plan"])
        before = await service.get_budget(source["task_id"])
        result = await service.discard_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        after = await service.get_budget(source["task_id"])
        replay = await service.discard_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        repeated = await service.get_budget(source["task_id"])

        assert result.branch_id == replay.branch_id == fork.branch_id
        assert after.active_branches == before.active_branches - 1
        assert after.active_executions == before.active_executions - 1
        assert repeated.active_branches == after.active_branches
        assert repeated.active_executions == after.active_executions
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(fork.branch_id)
            task = await uow.agents.get_task(source["task_id"])
            fork_execution = await uow.agents.get_execution(fork.execution_id)
        assert branch.resolution_state == "DISCARDED"
        assert fork_execution.state == "CANCELLED"
        assert fork_execution.revision == 2
        assert fork_execution.error == "BRANCH_DISCARDED_BEFORE_ACTIVATION"
        assert task.status not in {"COMPLETED", "FAILED", "CANCELLED"}
        assert task.output is None

        with pytest.raises(BranchResolutionError) as raised:
            await service.discard_branch(
                source["task_id"],
                source["source_branch_id"],
                target_user_id="user-r8-d",
            )
        assert raised.value.code == "BRANCH_DISCARD_LAST_OPEN_FORBIDDEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_state", ["RUNNING", "WAITING"])
async def test_r9_d_discard_rejects_execution_with_runtime_authority(
    tmp_path,
    execution_state,
):
    engine, sessions, service, planner = await _setup(
        tmp_path, name=f"r9_d_active_{execution_state.lower()}.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id=f"task-r9-d-active-{execution_state.lower()}",
        )
        fork = await service.consume_fork_plan(source["plan"])
        before = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            changed = await uow.agents.compare_and_set_execution(
                fork.execution_id,
                1,
                {
                    "state": execution_state,
                    "wait_reason": (
                        "RESOURCE" if execution_state == "WAITING" else None
                    ),
                },
            )
            assert changed is not None
            await uow.commit()

        with pytest.raises(BranchResolutionError) as raised:
            await service.discard_branch(
                source["task_id"],
                fork.branch_id,
                target_user_id="user-r8-d",
            )
        assert raised.value.code == "BRANCH_EXECUTION_ACTIVE"

        after = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(fork.execution_id)
        assert branch.resolution_state == "OPEN"
        assert execution.state == execution_state
        assert after.active_branches == before.active_branches
        assert after.active_executions == before.active_executions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_d_discard_settles_dormant_retry_and_releases_capacity(tmp_path):
    engine, sessions, service, fork_planner = await _setup(
        tmp_path, name="r9_d_retry_discard.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            fork_planner,
            task_id="task-r9-d-retry-discard",
        )
        fork = await service.consume_fork_plan(source["plan"])
        assert await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=fork.execution_id,
            source_revision=1,
            transition_values={
                "state": "FAILED",
                "wait_reason": None,
                "error": "fork failed",
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
        ) == 2

        retry_planner = AgentRetryPlanningService(
            DurableAgentStore(lambda: _Uow(sessions))
        )
        retry_plan = await retry_planner.build_retry_plan(
            retry_request_id="retry-before-discard",
            task_id=source["task_id"],
            branch_id=fork.branch_id,
            source_execution_id=fork.execution_id,
            target_user_id="user-r8-d",
        )
        retry = await service.consume_retry_plan(retry_plan)
        before = await service.get_budget(source["task_id"])

        await service.discard_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        after = await service.get_budget(source["task_id"])

        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(retry.execution_id)
        assert branch.resolution_state == "DISCARDED"
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert after.active_branches == before.active_branches - 1
        assert after.active_executions == before.active_executions - 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_d_resolved_branch_cannot_create_resume_claim(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_d_claim_after_resolution.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r9-d-claim-after-resolution",
        )
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(source["source_branch_id"])
            changed = await uow.agents.compare_and_set_task_branch(
                branch.branch_id,
                int(branch.revision),
                {"resolution_state": "DISCARDED"},
            )
            assert changed is not None
            await uow.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        with pytest.raises(ResumeClaimRejected) as raised:
            await store.get_or_create_resume_claim(
                ResumeClaimIntent(
                    resume_request_id="resume-on-discarded-branch",
                    execution_id=source["source_execution_id"],
                    checkpoint_id=source["checkpoint_id"],
                    expected_execution_revision=2,
                    plan_fingerprint="d" * 64,
                    user_id="user-r8-d",
                    client_id=None,
                    connection_id=None,
                    wait_reason="RESOURCE",
                    trigger_type=ResumeTriggerType.RESOURCE_READY,
                    claim_expires_at=(
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ),
                )
            )
        assert raised.value.code == "BRANCH_NOT_OPEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_e_adopt_commits_result_supersedes_and_rejects_claims(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_e_adopt.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-e-adopt",
            with_claim=True,
        )
        result = await service.adopt_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        assert result.selected_execution_id == fork.execution_id
        assert result.superseded_branch_ids == (source["source_branch_id"],)
        assert result.rejected_resume_claim_ids == (
            f"claim-{source['task_id']}",
        )

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            budget = await uow.agents.get_task_budget(source["task_id"])
            selected = await uow.agents.get_task_branch(fork.branch_id)
            loser = await uow.agents.get_task_branch(
                source["source_branch_id"]
            )
            claim = await uow.agents.get_resume_claim(
                f"claim-{source['task_id']}"
            )

        assert task.status == "COMPLETED"
        assert task.output == {"winner": "fork"}
        assert budget.state == "CLOSED"
        assert budget.active_branches == 0
        assert selected.resolution_state == "ADOPTED"
        assert loser.resolution_state == "SUPERSEDED"
        assert claim.state == "REJECTED"
        assert claim.rejection_code == "TASK_RESOLVED"
        assert claim.revision == 1

        replay = await service.adopt_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        assert replay.selected_execution_id == fork.execution_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_e_two_adopts_have_one_durable_winner(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_e_adopt_race.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-e-adopt-race",
        )
        outcomes = await asyncio.gather(
            service.adopt_branch(
                source["task_id"],
                source["source_branch_id"],
                target_user_id="user-r8-d",
            ),
            service.adopt_branch(
                source["task_id"],
                fork.branch_id,
                target_user_id="user-r8-d",
            ),
            return_exceptions=True,
        )
        winners = [item for item in outcomes if not isinstance(item, BaseException)]
        losers = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], BranchResolutionError)
        assert losers[0].code in {
            "TASK_ALREADY_RESOLVED",
            "TASK_RESOLUTION_CONFLICT",
        }
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            branches = await uow.agents.list_task_branches(source["task_id"])
        assert task.status == "COMPLETED"
        assert sum(item.resolution_state == "ADOPTED" for item in branches) == 1
        assert all(item.resolution_state != "OPEN" for item in branches)
        assert task.output in ({"winner": "root"}, {"winner": "fork"})
    finally:
        await engine.dispose()
