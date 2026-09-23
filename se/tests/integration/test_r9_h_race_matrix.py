from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeTriggerType,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.retry_planning import AgentRetryPlanningService
from se.src.runtimes.agent.resume_claim import ResumeClaimRejected
from se.src.runtimes.agent.task_budget import (
    AggregateAdmissionError,
    BranchResolutionError,
    RetryConsumeError,
    TaskBudgetService,
)
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _seed_source,
    _setup,
)
from se.tests.integration.test_r9_b_atomic_retry_admission import (
    _seed_failed_source,
    _setup as _retry_setup,
)
from se.tests.integration.test_r9_de_branch_resolution import (
    _seed_two_completed_branches,
)


from se.tests.integration.test_r7_f_resume_claim_atomicity import (
    _intent as _resume_intent,
    _plan as _resume_plan,
    _seed_task_waiting as _seed_resume_task_waiting,
    _setup as _resume_setup,
)


def test_r9_h_cancel_task_locks_branches_in_canonical_order():
    source = inspect.getsource(TaskBudgetService.cancel_task)
    lock_all = source.index("list_task_branches_for_update")
    fork_receipts = source.index("list_task_fork_admissions")
    retry_receipts = source.index("list_task_retry_admissions")
    aggregate_receipts = source.index("list_task_aggregate_admissions")

    assert lock_all < fork_receipts < retry_receipts < aggregate_receipts
    assert "get_task_branch_for_update" not in source


async def _complete_root(source, service):
    assert await service.resume_task_scoped_execution(
        source["task_id"],
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
        source["task_id"],
        execution_id=source["source_execution_id"],
        source_revision=3,
        transition_values={
            "state": "COMPLETED",
            "result": {"winner": "root"},
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
    ) == 4


async def _retryable_fork(sessions, service, fork_planner, task_id):
    source = await _seed_source(
        sessions,
        service,
        fork_planner,
        task_id=task_id,
        fork_request_id=f"fork-{task_id}",
    )
    fork = await service.consume_fork_plan(source["plan"])
    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=fork.execution_id,
        source_revision=1,
        transition_values={
            "state": "FAILED",
            "error": "fork failed",
            "completed_at": datetime.now(timezone.utc),
        },
        delegated=False,
    ) == 2
    retry_planner = AgentRetryPlanningService(
        DurableAgentStore(lambda: _Uow(sessions))
    )
    plan = await retry_planner.build_retry_plan(
        retry_request_id=f"retry-{task_id}",
        task_id=task_id,
        branch_id=fork.branch_id,
        source_execution_id=fork.execution_id,
        target_user_id="user-r8-d",
    )
    return source, fork, plan


@pytest.mark.asyncio
async def test_r9_h_retry_vs_task_cancel_is_fail_closed(tmp_path):
    engine, sessions, service, planner = await _retry_setup(
        tmp_path, "r9_h_retry_cancel.sqlite"
    )
    try:
        _root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-h-retry-cancel"
        )
        outcomes = await asyncio.gather(
            service.consume_retry_plan(plan),
            service.cancel_task(plan.task_id),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(plan.task_id)
            budget = await uow.agents.get_task_budget(plan.task_id)
            receipt = await uow.agents.get_task_retry_admission(
                plan.task_id, plan.retry_request_id
            )
            retry = (
                await uow.agents.get_execution(receipt.execution_id)
                if receipt is not None
                else None
            )
        assert task.status == "CANCELLED"
        assert budget.state == "CLOSED"
        assert budget.active_executions == 0
        if retry is not None:
            assert retry.state == "CANCELLED"
        assert any(not isinstance(item, BaseException) for item in outcomes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_retry_vs_discard_never_reopens_branch(tmp_path):
    engine, sessions, service, fork_planner = await _setup(
        tmp_path, name="r9_h_retry_discard.sqlite"
    )
    try:
        source, fork, plan = await _retryable_fork(
            sessions, service, fork_planner, "task-r9-h-retry-discard"
        )
        outcomes = await asyncio.gather(
            service.consume_retry_plan(plan),
            service.discard_branch(
                source["task_id"],
                fork.branch_id,
                target_user_id="user-r8-d",
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(fork.branch_id)
            budget = await uow.agents.get_task_budget(source["task_id"])
            current = await uow.agents.get_execution(
                branch.current_execution_id
            )
        assert branch.resolution_state in {"OPEN", "DISCARDED"}
        assert (
            branch.resolution_state != "OPEN"
            or branch.current_execution_id != fork.execution_id
        )
        assert budget.active_branches in {1, 2}
        assert budget.active_branches >= 0
        assert budget.active_executions >= 0
        if branch.resolution_state == "DISCARDED":
            # A DISCARD winner may observe either the original terminal fork
            # or a just-admitted retry. If RETRY admission won first, DISCARD
            # must settle its dormant RUNNING@1 authority and release capacity.
            assert current.state in {"FAILED", "CANCELLED"}
            assert current.state != "RUNNING"
            assert budget.active_executions == 0
        for error in (
            item for item in outcomes if isinstance(item, BaseException)
        ):
            assert isinstance(error, (RetryConsumeError, BranchResolutionError))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_retry_vs_adopt_cannot_rewrite_task_result(tmp_path):
    engine, sessions, service, fork_planner = await _setup(
        tmp_path, name="r9_h_retry_adopt.sqlite"
    )
    try:
        source, fork, _stale_plan = await _retryable_fork(
            sessions, service, fork_planner, "task-r9-h-retry-adopt"
        )
        await _complete_root(source, service)

        # Root completion advances Task/TaskBudget authority. Re-plan after
        # that mutation so RETRY and ADOPT genuinely race the same current
        # Task revision instead of trivially rejecting a stale pre-completion
        # RetryPlan.
        retry_planner = AgentRetryPlanningService(
            DurableAgentStore(lambda: _Uow(sessions))
        )
        plan = await retry_planner.build_retry_plan(
            retry_request_id="retry-task-r9-h-retry-adopt-current",
            task_id=source["task_id"],
            branch_id=fork.branch_id,
            source_execution_id=fork.execution_id,
            target_user_id="user-r8-d",
        )
        outcomes = await asyncio.gather(
            service.consume_retry_plan(plan),
            service.adopt_branch(
                source["task_id"],
                source["source_branch_id"],
                target_user_id="user-r8-d",
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            root = await uow.agents.get_task_branch(
                source["source_branch_id"]
            )
            loser = await uow.agents.get_task_branch(fork.branch_id)
            budget = await uow.agents.get_task_budget(source["task_id"])
            receipt = await uow.agents.get_task_retry_admission(
                source["task_id"], plan.retry_request_id
            )
            retry_execution = (
                await uow.agents.get_execution(receipt.execution_id)
                if receipt is not None
                else None
            )
        assert task.status == "COMPLETED"
        assert task.output == {"winner": "root"}
        assert root.resolution_state == "ADOPTED"
        assert loser.resolution_state == "SUPERSEDED"
        assert budget.active_executions == 0
        if retry_execution is not None:
            assert retry_execution.state == "CANCELLED"
            assert retry_execution.revision == 2
            assert retry_execution.error == "TASK_ADOPTED_BEFORE_ACTIVATION"
        assert any(not isinstance(item, BaseException) for item in outcomes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_adopt_settles_dormant_retry_preactivation(tmp_path):
    engine, sessions, service, fork_planner = await _setup(
        tmp_path, name="r9_h_adopt_dormant_retry.sqlite"
    )
    try:
        source, fork, plan = await _retryable_fork(
            sessions,
            service,
            fork_planner,
            "task-r9-h-adopt-dormant-retry",
        )
        retry = await service.consume_retry_plan(plan)
        await _complete_root(source, service)
        before = await service.get_budget(source["task_id"])
        assert before.active_executions == 1

        await service.adopt_branch(
            source["task_id"],
            source["source_branch_id"],
            target_user_id="user-r8-d",
        )

        after = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            loser = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(retry.execution_id)
        assert loser.resolution_state == "SUPERSEDED"
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert execution.error == "TASK_ADOPTED_BEFORE_ACTIVATION"
        assert after.state.value == "CLOSED"
        assert after.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_adopt_settles_dormant_fork_preactivation(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_adopt_dormant_fork.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r9-h-adopt-dormant-fork",
        )
        fork = await service.consume_fork_plan(source["plan"])
        await _complete_root(source, service)
        before = await service.get_budget(source["task_id"])
        assert before.active_executions == 1

        await service.adopt_branch(
            source["task_id"],
            source["source_branch_id"],
            target_user_id="user-r8-d",
        )

        after = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            loser = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(fork.execution_id)
        assert loser.resolution_state == "SUPERSEDED"
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert execution.error == "TASK_ADOPTED_BEFORE_ACTIVATION"
        assert after.state.value == "CLOSED"
        assert after.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_adopt_settles_dormant_aggregate_preactivation(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_adopt_dormant_aggregate.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-h-adopt-dormant-aggregate",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        aggregate = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-before-adopt",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )
        before = await service.get_budget(source["task_id"])
        assert before.active_executions == 1

        await service.adopt_branch(
            source["task_id"],
            source["source_branch_id"],
            target_user_id="user-r8-d",
        )

        after = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            loser = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(
                aggregate.execution_id
            )
        assert loser.resolution_state == "SUPERSEDED"
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert execution.error == "TASK_ADOPTED_BEFORE_ACTIVATION"
        assert after.state.value == "CLOSED"
        assert after.active_executions == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_adopt_vs_late_loser_completion_freezes_output(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_late_loser.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r9-h-late-loser"
        )
        fork = await service.consume_fork_plan(source["plan"])
        await _complete_root(source, service)
        outcomes = await asyncio.gather(
            service.adopt_branch(
                source["task_id"],
                source["source_branch_id"],
                target_user_id="user-r8-d",
            ),
            service.finish_task_scoped_execution(
                source["task_id"],
                execution_id=fork.execution_id,
                source_revision=1,
                transition_values={
                    "state": "COMPLETED",
                    "result": {"winner": "late-loser"},
                    "completed_at": datetime.now(timezone.utc),
                },
                delegated=False,
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            budget = await uow.agents.get_task_budget(source["task_id"])
        assert task.status == "COMPLETED"
        assert task.output == {"winner": "root"}
        assert budget.active_branches == 0
        assert budget.active_executions >= 0
        assert any(not isinstance(item, BaseException) for item in outcomes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_discard_vs_adopt_same_branch_has_one_resolution(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_discard_adopt.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-h-discard-adopt",
        )
        outcomes = await asyncio.gather(
            service.discard_branch(
                source["task_id"], fork.branch_id, target_user_id="user-r8-d"
            ),
            service.adopt_branch(
                source["task_id"], fork.branch_id, target_user_id="user-r8-d"
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            branch = await uow.agents.get_task_branch(fork.branch_id)
            budget = await uow.agents.get_task_budget(source["task_id"])
        assert branch.resolution_state in {"DISCARDED", "ADOPTED"}
        assert branch.resolution_state != "ADOPTED" or task.status == "COMPLETED"
        assert budget.active_branches >= 0
        assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_created_resume_claim_rejects_if_branch_resolves_before_consume(
    tmp_path,
):
    engine, sessions, factory, store = await _resume_setup(
        tmp_path,
        "r9_h_claim_then_resolution.sqlite",
    )
    try:
        branch_id = await _seed_resume_task_waiting(sessions, factory)
        plan = _resume_plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(
            _resume_intent(plan, "rr-r9-h-resolution")
        )

        # Model the durable resolution fence winning after claim creation but
        # before consume. Public DISCARD itself rejects a WAITING head with
        # BRANCH_EXECUTION_ACTIVE; this direct branch CAS isolates and proves
        # the consume-time OPEN/current revalidation against stale authority.
        async with factory() as uow:
            branch = await uow.agents.get_task_branch(branch_id)
            changed = await uow.agents.compare_and_set_task_branch(
                branch_id,
                int(branch.revision),
                {"resolution_state": "DISCARDED"},
            )
            assert changed is not None
            await uow.commit()

        with pytest.raises(ResumeClaimRejected) as raised:
            await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=datetime.now(timezone.utc),
                )
            )
        assert raised.value.code == "BRANCH_NOT_OPEN"

        async with factory() as uow:
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            execution = await uow.agents.get_execution(plan.execution_id)
            branch = await uow.agents.get_task_branch(branch_id)
            await uow.commit()
        assert durable_claim.state == "REJECTED"
        assert durable_claim.rejection_code == "BRANCH_NOT_OPEN"
        assert execution.state == "WAITING"
        assert branch.resolution_state == "DISCARDED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_resume_claim_consume_vs_adopt_leaves_no_created_claim(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_claim_adopt.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r9-h-claim-adopt"
        )
        fork = await service.consume_fork_plan(source["plan"])
        await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=fork.execution_id,
            source_revision=1,
            transition_values={
                "state": "COMPLETED",
                "result": {"winner": "fork"},
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
        )
        claim_id = f"claim-{source['task_id']}"
        async with _Uow(sessions) as uow:
            await uow.agents.save_resume_claim(
                {
                    "claim_id": claim_id,
                    "execution_id": source["source_execution_id"],
                    "checkpoint_id": source["checkpoint_id"],
                    "resume_request_id": f"resume-{source['task_id']}",
                    "expected_execution_revision": 2,
                    "user_id": "user-r8-d",
                    "client_id": None,
                    "connection_id": None,
                    "wait_reason": "RESOURCE",
                    "trigger_type": "EXPLICIT",
                    "state": "CREATED",
                    "revision": 0,
                    "plan_fingerprint": "b" * 64,
                    "metadata_json": {},
                    "claim_expires_at": datetime.now(timezone.utc)
                    + timedelta(hours=1),
                }
            )
            await uow.commit()

        async def consume_claim():
            async with _Uow(sessions) as uow:
                await uow.agents.get_task_for_update(source["task_id"])
                claim = await uow.agents.get_resume_claim(claim_id)
                changed = await uow.agents.compare_and_set_resume_claim(
                    claim_id,
                    int(claim.revision),
                    "CREATED",
                    {
                        "state": "CONSUMED",
                        "consumed_at": datetime.now(timezone.utc),
                        "consumed_execution_revision": 3,
                    },
                )
                await uow.commit()
                return changed

        await asyncio.gather(
            consume_claim(),
            service.adopt_branch(
                source["task_id"], fork.branch_id, target_user_id="user-r8-d"
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            claim = await uow.agents.get_resume_claim(claim_id)
            task = await uow.agents.get_task(source["task_id"])
        assert task.status == "COMPLETED"
        assert claim.state in {"CONSUMED", "REJECTED"}
        assert claim.state != "CREATED"
        if claim.state == "REJECTED":
            assert claim.rejection_code == "TASK_RESOLVED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_resume_claim_create_vs_adopt_never_leaves_created_claim(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_claim_create_adopt.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r9-h-claim-create-adopt"
        )
        fork = await service.consume_fork_plan(source["plan"])
        await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=fork.execution_id,
            source_revision=1,
            transition_values={
                "state": "COMPLETED",
                "result": {"winner": "fork"},
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=False,
        )

        store = DurableAgentStore(lambda: _Uow(sessions))
        resume_request_id = f"resume-create-{source['task_id']}"
        intent = ResumeClaimIntent(
            resume_request_id=resume_request_id,
            execution_id=source["source_execution_id"],
            checkpoint_id=source["checkpoint_id"],
            expected_execution_revision=2,
            plan_fingerprint="c" * 64,
            user_id="user-r8-d",
            client_id=None,
            connection_id=None,
            wait_reason="RESOURCE",
            trigger_type=ResumeTriggerType.RESOURCE_READY,
            claim_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        outcomes = await asyncio.gather(
            store.get_or_create_resume_claim(intent),
            service.adopt_branch(
                source["task_id"], fork.branch_id, target_user_id="user-r8-d"
            ),
            return_exceptions=True,
        )

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            claim = await uow.agents.get_resume_claim_by_request_id(
                resume_request_id
            )

        assert task.status == "COMPLETED"
        assert claim is None or claim.state == "REJECTED"
        assert claim is None or claim.rejection_code == "TASK_RESOLVED"
        for outcome in outcomes:
            if isinstance(outcome, ResumeClaimRejected):
                assert outcome.code == "TASK_TERMINAL"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_h_aggregate_vs_source_mutation_preserves_snapshot(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_h_aggregate_mutation.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-h-aggregate-mutation",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        outcomes = await asyncio.gather(
            service.aggregate_branches(
                source["task_id"],
                aggregate_request_id="aggregate-race",
                target_branch_id=fork.branch_id,
                source_branch_ids=ordered,
                target_user_id="user-r8-d",
            ),
            service.discard_branch(
                source["task_id"],
                source["source_branch_id"],
                target_user_id="user-r8-d",
            ),
            return_exceptions=True,
        )
        async with _Uow(sessions) as uow:
            receipt = await uow.agents.get_task_aggregate_admission(
                source["task_id"], "aggregate-race"
            )
            budget = await uow.agents.get_task_budget(source["task_id"])
        if receipt is not None:
            assert tuple(
                item["branch_id"] for item in receipt.source_branch_snapshots
            ) == ordered
            assert len(receipt.result_fingerprints) == 2
        errors = [item for item in outcomes if isinstance(item, BaseException)]
        for error in errors:
            assert isinstance(
                error, (AggregateAdmissionError, BranchResolutionError)
            )
        assert budget.active_branches >= 0
    finally:
        await engine.dispose()
