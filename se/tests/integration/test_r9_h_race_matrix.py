from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from se.src.infrastructure.storage.repositories.agent import AgentRepository

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
        claim_outcome, adopt_outcome = outcomes
        if isinstance(claim_outcome, BaseException):
            assert isinstance(claim_outcome, ResumeClaimRejected), repr(claim_outcome)
            assert claim_outcome.code == "TASK_TERMINAL"
        else:
            assert claim_outcome.resume_request_id == resume_request_id
        # return_exceptions=True must never hide an ADOPT failure, including
        # SQLite busy, exhausted retries, or an unrelated lifecycle error.
        assert not isinstance(adopt_outcome, BaseException), repr(adopt_outcome)
        assert adopt_outcome.task_id == source["task_id"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "schedule", ("claim_commits_first", "adopt_commits_first", "adopt_scans_first")
)
@pytest.mark.asyncio
async def test_r9_h_resume_claim_adopt_isolation_ordering_diagnostic(
    tmp_path, schedule
):
    """Exercise real SQLite sessions in two winning orders and the scan gap.

    The ADOPT scan barrier awaits *after* the actual database SELECT but
    before Task CAS. It never mocks a lock, overrides an ORM result, or
    changes production transaction semantics. On unfixed SQLite, the
    adopt_scans_first case is expected to expose the orphan CREATED claim
    and remain RED until a separately approved production repair.
    """
    adopt_scanned = asyncio.Event()
    release_adopt = asyncio.Event()
    trace = []

    class _AdoptScanBarrierRepository(AgentRepository):
        async def list_created_resume_claims_for_task_for_update(self, task_id):
            claims = await super().list_created_resume_claims_for_task_for_update(
                task_id
            )
            if schedule == "adopt_scans_first":
                trace.append(("adopt_scanned", tuple(c.claim_id for c in claims)))
                adopt_scanned.set()
                await asyncio.wait_for(release_adopt.wait(), timeout=15)
            return claims

    engine, sessions, service, planner = await _setup(
        tmp_path,
        name=f"r9_h_isolation_{schedule}.sqlite",
        repository_cls=_AdoptScanBarrierRepository,
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id=f"task-r9-h-{schedule}"
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

        async def adopt():
            result = await service.adopt_branch(
                source["task_id"], fork.branch_id, target_user_id="user-r8-d"
            )
            trace.append(("adopt_committed", result.task_revision))
            return result

        if schedule == "claim_commits_first":
            created = await store.get_or_create_resume_claim(intent)
            assert created.resume_request_id == resume_request_id
            trace.append(("claim_committed", created.claim_id))
            adopted = await adopt()
        elif schedule == "adopt_commits_first":
            adopted = await adopt()
            with pytest.raises(ResumeClaimRejected) as rejection:
                await store.get_or_create_resume_claim(intent)
            assert rejection.value.code == "TASK_TERMINAL"
            trace.append(("claim_rejected", rejection.value.code))
        else:
            adopt_task = asyncio.create_task(adopt())
            create_task = None
            try:
                await asyncio.wait_for(adopt_scanned.wait(), timeout=15)
                # The real ADOPT SELECT saw an empty predicate.
                assert trace == [("adopt_scanned", ())]
                claim_started = asyncio.Event()

                async def create_after_real_adopt_scan():
                    # Signal the CREATE *attempt*, not an impossible-to-
                    # guarantee commit while a correct Task fence is held.
                    trace.append(("claim_attempt_started", resume_request_id))
                    claim_started.set()
                    value = await store.get_or_create_resume_claim(intent)
                    trace.append(("claim_committed", value.claim_id))
                    return value

                create_task = asyncio.create_task(
                    create_after_real_adopt_scan()
                )
                await asyncio.wait_for(claim_started.wait(), timeout=15)
                # Let CREATE reach its real SQL work. Under a production
                # writer fence it may block until ADOPT finishes; never
                # require the CREATE transaction to commit before releasing.
                await asyncio.sleep(0)
            finally:
                release_adopt.set()

            if create_task is None:
                adopted = await asyncio.wait_for(adopt_task, timeout=20)
            else:
                claim_outcome, adopted = await asyncio.wait_for(
                    asyncio.gather(
                        create_task, adopt_task, return_exceptions=True
                    ),
                    timeout=20,
                )
                # Fail on genuine SQLite busy/timeout or any unrelated fault.
                assert not isinstance(adopted, BaseException), repr(adopted)
                if isinstance(claim_outcome, BaseException):
                    assert isinstance(
                        claim_outcome, ResumeClaimRejected
                    ), repr(claim_outcome)
                    assert claim_outcome.code == "TASK_TERMINAL"
                    trace.append(("claim_rejected", claim_outcome.code))
                else:
                    assert claim_outcome.resume_request_id == resume_request_id

        assert adopted.task_id == source["task_id"]
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            claim = await uow.agents.get_resume_claim_by_request_id(
                resume_request_id
            )
        # These durability assertions intentionally remain strict. No
        # assertion waiver is allowed when the SQLite interleaving is RED.
        assert task.status == "COMPLETED", (schedule, trace, task.status)
        assert claim is None or claim.state == "REJECTED", (
            schedule, trace, None if claim is None else claim.state
        )
        assert claim is None or claim.rejection_code == "TASK_RESOLVED", (
            schedule, trace, None if claim is None else claim.rejection_code
        )
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


@pytest.mark.parametrize("terminal_kind", ("cancel", "terminalize"))
@pytest.mark.asyncio
async def test_r9_h_p1g_create_vs_terminal_writer_rejects_actionable_claim(
    tmp_path, terminal_kind
):
    """CREATE racing CANCEL/TERMINALIZE must not survive Task terminal state."""
    engine, sessions, service, planner = await _setup(
        tmp_path, name=f"r9_h_p1g_create_{terminal_kind}.sqlite"
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id=f"task-r9-h-p1g-{terminal_kind}",
        )
        store = DurableAgentStore(lambda: _Uow(sessions))
        intent = ResumeClaimIntent(
            resume_request_id=f"resume-{source['task_id']}",
            execution_id=source["source_execution_id"],
            checkpoint_id=source["checkpoint_id"],
            expected_execution_revision=2,
            plan_fingerprint="d" * 64,
            user_id="user-r8-d",
            client_id=None,
            connection_id=None,
            wait_reason="RESOURCE",
            trigger_type=ResumeTriggerType.RESOURCE_READY,
            claim_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        async def terminal_writer():
            if terminal_kind == "cancel":
                return await service.cancel_task(source["task_id"])
            return await service.terminalize_task(
                source["task_id"],
                allowed_source_states=("RUNNING",),
                target_state="FAILED",
                values={"error": "r9-h-p1g-terminal"},
            )

        create_outcome, terminal_outcome = await asyncio.gather(
            store.get_or_create_resume_claim(intent),
            terminal_writer(),
            return_exceptions=True,
        )
        assert not isinstance(terminal_outcome, BaseException), repr(
            terminal_outcome
        )
        if isinstance(create_outcome, BaseException):
            assert isinstance(create_outcome, ResumeClaimRejected), repr(
                create_outcome
            )
            assert create_outcome.code == "TASK_TERMINAL"

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            claim = await uow.agents.get_resume_claim_by_request_id(
                intent.resume_request_id
            )
            await uow.commit()

        expected_status = (
            "CANCELLED" if terminal_kind == "cancel" else "FAILED"
        )
        assert task.status == expected_status
        assert claim is None or claim.state == "REJECTED"
        assert claim is None or claim.rejection_code == "TASK_RESOLVED"
    finally:
        await engine.dispose()


# D0-L0-E1 TEST-ONLY amendment (#409): actual no-TaskBudget / R7-D preflight.
#
# This uses a fresh on-disk SQLite database and real repositories, an R6 SQL
# invocation store, production K1/K2 connection lifecycle and catalog
# registration, and the actual R7-D planner. Unlike _resume_plan(), no
# synthetic ResumePlan can turn a missing execution/checkpoint into a success.
# The original budget-backed D0 SQLite race remains intentionally unwaived.
from types import SimpleNamespace

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskStatus
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
)
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.agent.resume_planning import (
    AgentResumePlanningService,
    ResumePlanRejected,
)
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.registration import (
    CapabilityRegistration,
    ClientCapabilityRegistration,
)
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.tests.integration.test_r7_f_resume_claim_atomicity import (
    AGENT as _E1_AGENT,
    CAPABILITY as _E1_CAPABILITY,
    CHECKPOINT as _E1_CHECKPOINT,
    CLIENT as _E1_CLIENT,
    EXECUTION as _E1_EXECUTION,
    FINGERPRINT as _E1_FINGERPRINT,
    INVOCATION as _E1_INVOCATION,
    K1 as _E1_K1,
    K2 as _E1_K2,
    SESSION as _E1_SESSION,
    TOOL_CALL as _E1_TOOL_CALL,
    USER as _E1_USER,
    _intent as _e1_intent,
    _invocation_values as _e1_invocation_values,
)


async def _r9_h_e1_seed_sqlite_waiting(sessions, factory, store, *, task_id):
    """Persist authentic R7 checkpoint, ordered iteration, R6 and SQL Task.

    task_id=None is the canonical Task-less comparison. A non-None task_id
    creates a genuine legacy Task without creating any TaskBudget or branch.
    """
    if task_id is not None:
        await store.save_task(
            {
                "id": task_id,
                "session_id": _E1_SESSION,
                "created_by": _E1_USER,
                "assigned_agent_id": _E1_AGENT,
                "revision": 0,
                "status": "WAITING",
                "wait_reasons": ["CONNECTION"],
                "input": {"source": "r9-h-e1"},
            }
        )
    prefix = [{"role": "user", "content": "R9-H E1 persisted reconnect"}]
    async with factory() as uow:
        # This assertion must be from a real database, not from a mock budget
        # adapter or service-less shortcut.
        if task_id is not None:
            task = await uow.agents.get_task(task_id)
            assert task is not None and task.status == "WAITING"
            assert await uow.agents.get_task_budget(task_id) is None

        uow.session.add(
            AgentExecutionRecord(
                id=_E1_EXECUTION,
                task_id=task_id,
                session_id=_E1_SESSION,
                agent_id=_E1_AGENT,
                correlation_id="corr-r9-h-e1",
                state="WAITING",
                wait_reason="CONNECTION",
                revision=2,
                current_checkpoint_id=_E1_CHECKPOINT,
                bound_client_id=_E1_CLIENT,
                remaining_active_budget_seconds=20.0,
                transcript=prefix,
                request={},
                context_state={"request_id": "request-r9-h-e1"},
            )
        )
        iteration_id = "r9-h-e1-iteration-1"
        uow.session.add(
            AgentIterationRecord(
                id=iteration_id,
                execution_id=_E1_EXECUTION,
                iteration=1,
                state="WAITING_TOOL",
                tool_call_ids=[_E1_TOOL_CALL],
                transcript=prefix,
            )
        )
        uow.session.add(
            AgentToolCallRecord(
                id="r9-h-e1-tool-call",
                execution_id=_E1_EXECUTION,
                iteration_id=iteration_id,
                invocation_id=_E1_INVOCATION,
                tool_call_id=_E1_TOOL_CALL,
                capability_id=_E1_CAPABILITY,
                arguments={"value": "x"},
                status="PENDING",
            )
        )
        uow.session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=_E1_CHECKPOINT,
                execution_id=_E1_EXECUTION,
                execution_revision=2,
                session_id=_E1_SESSION,
                task_id=task_id,
                iteration=1,
                wait_reason="CONNECTION",
                remaining_active_budget_seconds=20.0,
                origin_client_id=_E1_CLIENT,
                origin_connection_id=_E1_K1,
                transcript_snapshot=prefix,
                metadata_json={},
            )
        )
        # NOT_DISPATCHED is genuine persisted R6 lifecycle authority. This
        # takes the planner's real dispatch-safe branch without faking any
        # remote reconciliation outcome or issuing an actual remote command.
        r6 = _e1_invocation_values()
        r6["remote_outcome_state"] = "NOT_DISPATCHED"
        uow.session.add(CapabilityInvocationRecord(**r6))
        await uow.session.flush()
        await uow.agents.save_checkpoint_pending_invocation(
            {
                "checkpoint_id": _E1_CHECKPOINT,
                "ordinal": 0,
                "invocation_id": _E1_INVOCATION,
                "invocation_revision": r6["revision"],
                "tool_call_id": _E1_TOOL_CALL,
                "capability_id": _E1_CAPABILITY,
                "capability_version": "1.0",
                "request_fingerprint": _E1_FINGERPRINT,
                "idempotency": "IDEMPOTENT",
                "observed_remote_outcome_state": "NOT_DISPATCHED",
                "origin_client_id": _E1_CLIENT,
                "origin_connection_id": _E1_K1,
            }
        )
        await uow.commit()


def _r9_h_e1_planner_with_real_k2(factory, store):
    """Use actual connection and registration state, not a fabricated K2."""
    connections = ConnectionRegistry()
    connections.register(
        _E1_SESSION, _E1_USER,
        connection_id=_E1_K1,
        metadata={"client_id": _E1_CLIENT},
    )
    connections.activate(_E1_K1)
    connections.disconnect(_E1_K1)
    connections.register(
        _E1_SESSION, _E1_USER,
        connection_id=_E1_K2,
        metadata={"client_id": _E1_CLIENT},
    )
    connections.activate(_E1_K2)
    catalog = CapabilityCatalog()
    registration = ClientCapabilityRegistrationService(catalog, connections)
    definition = CapabilityDefinition(
        id=_E1_CAPABILITY,
        name=_E1_CAPABILITY,
        description="D0-L0-E1 SQLite reconnect test capability",
        version="1.0",
        source="LOCAL",
        execution_kind="PYTHON",
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        input_schema={"type": "object"},
    )
    registration.register(
        ClientCapabilityRegistration(
            connection_id=_E1_K2,
            client_id=_E1_CLIENT,
            owner_id=_E1_USER,
            capabilities=[
                CapabilityRegistration(
                    definition=definition,
                    location=CapabilityExecutionLocation.CLIENT,
                    driver_kind="REMOTE_CLIENT",
                    owner_type=CapabilityOwnerType.CLIENT,
                    owner_id=_E1_USER,
                    connection_id=_E1_K2,
                    implementation_id=f"{_E1_K2}:{_E1_CAPABILITY}",
                )
            ],
        )
    )
    # The runtime's three read-only authority ports are all production
    # implementations. No mocked DB, connection, catalog or invocation store.
    capability_ports = SimpleNamespace(
        connection_registry=connections,
        catalog=catalog,
        invocation_lifecycle=SimpleNamespace(
            store=SqlCapabilityInvocationStore(factory)
        ),
    )
    return AgentResumePlanningService(store, capability_ports), connections


@pytest.mark.parametrize("task_scoped", (False, True))
@pytest.mark.asyncio
async def test_r9_h_e1_real_sqlite_r7_connection_plan_without_task_budget(
    tmp_path, task_scoped
):
    """Real R7-D plan on Task-less and Task-linked legacy SQLite histories."""
    engine, sessions, factory, store = await _resume_setup(
        tmp_path, f"r9-h-e1-r7-no-budget-{task_scoped}.sqlite"
    )
    task_id = "r9-h-e1-no-budget-task" if task_scoped else None
    try:
        await _r9_h_e1_seed_sqlite_waiting(
            sessions, factory, store, task_id=task_id
        )
        planner, connections = _r9_h_e1_planner_with_real_k2(factory, store)
        assert not connections.get(_E1_K1).is_usable
        assert connections.get(_E1_K2).is_usable
        plan = await planner.build_resume_plan(
            _E1_EXECUTION,
            _E1_CHECKPOINT,
            target_user_id=_E1_USER,
            target_client_id=_E1_CLIENT,
            target_connection_id=_E1_K2,
        )
        assert plan.task_id == task_id
        assert plan.execution_id == _E1_EXECUTION
        assert plan.expected_execution_revision == 2
        assert plan.ordered_tool_call_ids == (_E1_TOOL_CALL,)
        assert plan.invocation_actions[0].invocation_id == _E1_INVOCATION
        assert plan.target_connection_id == _E1_K2
        assert plan.plan_fingerprint

        with pytest.raises(ResumePlanRejected) as foreign:
            await planner.build_resume_plan(
                _E1_EXECUTION,
                _E1_CHECKPOINT,
                target_user_id="different-principal",
                target_client_id=_E1_CLIENT,
                target_connection_id=_E1_K2,
            )
        assert foreign.value.code == "FOREIGN_PRINCIPAL"

        intent = _e1_intent(plan, "rr-r9-h-e1-genuine-k2")
        first = await store.get_or_create_resume_claim(intent)
        replay = await store.get_or_create_resume_claim(intent)
        assert replay.claim_id == first.claim_id

        if task_id is None:
            # The real R7-F Task-less positive remains legal.
            consumed = await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=first.claim_id,
                    resume_request_id=first.resume_request_id,
                    expected_claim_revision=first.revision,
                    now_utc=datetime.now(timezone.utc),
                )
            )
            assert consumed.consumed_execution_revision == 3
            async with factory() as uow:
                execution = await uow.agents.get_execution(_E1_EXECUTION)
                claim = await uow.agents.get_resume_claim(first.claim_id)
                assert execution.state == "RUNNING"
                assert execution.bound_connection_id == _E1_K2
                assert claim.state == "CONSUMED"
                await uow.commit()
        else:
            async with factory() as uow:
                assert await uow.agents.get_task_budget(task_id) is None
                assert (await uow.agents.get_task(task_id)).status == "WAITING"
                claim = await uow.agents.get_resume_claim(first.claim_id)
                assert claim.state == "CREATED"
                await uow.commit()
    finally:
        await engine.dispose()



@pytest.mark.parametrize("terminal_mode", ("complete", "cancel"))
@pytest.mark.asyncio
async def test_r9_h_p1g_service_less_coordinator_terminalizes_claim_atomically(
    tmp_path, terminal_mode
):
    """Real DurableAgentStore wiring must close Task and CREATED together."""
    engine, sessions, factory, store = await _resume_setup(
        tmp_path, f"r9-h-p1g-coordinator-{terminal_mode}.sqlite"
    )
    task_id = f"r9-h-p1g-coordinator-{terminal_mode}"
    try:
        await _r9_h_e1_seed_sqlite_waiting(
            sessions, factory, store, task_id=task_id
        )
        planner, _connections = _r9_h_e1_planner_with_real_k2(factory, store)
        plan = await planner.build_resume_plan(
            _E1_EXECUTION,
            _E1_CHECKPOINT,
            target_user_id=_E1_USER,
            target_client_id=_E1_CLIENT,
            target_connection_id=_E1_K2,
        )
        claim = await store.get_or_create_resume_claim(
            _e1_intent(plan, f"rr-r9-h-p1g-coordinator-{terminal_mode}")
        )

        async with factory() as uow:
            durable_task = await uow.agents.get_task(task_id)
            assert await uow.agents.get_task_budget(task_id) is None
            await uow.commit()

        coordinator = MultiAgentCoordinator(
            AgentRegistry(),
            durable_store=store,
        )
        coordinator._sessions[_E1_SESSION] = SimpleNamespace(
            owner_user_id=_E1_USER
        )
        local_task = coordinator._task_from_record(durable_task)
        coordinator._tasks[task_id] = local_task
        identity = Identity(
            user_id=_E1_USER,
            auth_type="api_key",
            scopes={"*"},
        )

        if terminal_mode == "cancel":
            terminal_task = await coordinator.cancel_task_and_wait(
                task_id, identity
            )
            assert terminal_task.status is AgentTaskStatus.CANCELLED
            expected_status = "CANCELLED"
        else:
            # Legacy service-less execution can have process-local RUNNING
            # while the last durable Task view is WAITING. The atomic helper
            # accepts both as source states and makes the terminal winner
            # authoritative.
            local_task.status = AgentTaskStatus.RUNNING

            async def executor(
                _task,
                *,
                identity,
                execution_id,
                correlation_id,
                parent_execution_id,
            ):
                return {"completed": True}

            execution = await coordinator.execute_task(
                task_id,
                identity,
                executor,
            )
            assert execution.state.value == "COMPLETED"
            assert local_task.status is AgentTaskStatus.COMPLETED
            expected_status = "COMPLETED"

        async with factory() as uow:
            task = await uow.agents.get_task(task_id)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            budget = await uow.agents.get_task_budget(task_id)
            await uow.commit()

        assert task.status == expected_status
        assert budget is None
        assert durable_claim.state == "REJECTED"
        assert durable_claim.rejection_code == "TASK_RESOLVED"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("terminal_status", ("COMPLETED", "CANCELLED"))
@pytest.mark.asyncio
async def test_r9_h_e1_no_budget_terminal_disallows_replay_and_late_claim(
    tmp_path, terminal_status
):
    """Legacy terminal writer must not leave a replayable orphan CREATED."""
    engine, sessions, factory, store = await _resume_setup(
        tmp_path, f"r9-h-e1-legacy-{terminal_status}.sqlite"
    )
    task_id = f"r9-h-e1-legacy-{terminal_status}"
    try:
        await _r9_h_e1_seed_sqlite_waiting(
            sessions, factory, store, task_id=task_id
        )
        planner, _connections = _r9_h_e1_planner_with_real_k2(factory, store)
        plan = await planner.build_resume_plan(
            _E1_EXECUTION,
            _E1_CHECKPOINT,
            target_user_id=_E1_USER,
            target_client_id=_E1_CLIENT,
            target_connection_id=_E1_K2,
        )
        intent = _e1_intent(plan, f"rr-r9-h-e1-{terminal_status}")
        first = await store.get_or_create_resume_claim(intent)

        # Exercise the new bounded service-less terminal durability seam,
        # not generic update_task() and not TaskBudgetService.  The SQLite Task
        # has NO Budget record, matching coordinator legacy authority.
        await store.terminalize_legacy_task_and_reject_resume_claims(
            task_id,
            allowed_source_states=("ASSIGNED", "RUNNING", "WAITING"),
            target_state=terminal_status,
            values={"wait_reasons": []},
        )
        with pytest.raises(ResumePlanRejected) as terminal:
            await planner.build_resume_plan(
                _E1_EXECUTION,
                _E1_CHECKPOINT,
                target_user_id=_E1_USER,
                target_client_id=_E1_CLIENT,
                target_connection_id=_E1_K2,
            )
        assert terminal.value.code == "TASK_TERMINAL"

        with pytest.raises(ResumeClaimRejected) as late:
            await store.get_or_create_resume_claim(
                _e1_intent(plan, f"rr-r9-h-e1-late-{terminal_status}")
            )
        assert late.value.code == "TASK_TERMINAL"

        # Replaying an already-CREATED id after Task closure MUST NOT return
        # fresh actionable authority. Existing SQLite source is expected
        # to expose this P1 failure; don't xfail or delete the orphan.
        replay = await store.get_or_create_resume_claim(intent)
        assert replay.claim_id == first.claim_id
        assert replay.state == "REJECTED"
        assert replay.rejection_code == "TASK_RESOLVED"

        # plan -> terminal -> R7-G rebind must preserve the durable rejected
        # winner rather than resurrecting CREATED on the newer generation.
        rebound = await store.rebind_created_resume_claim(
            first.claim_id,
            plan=plan,
            resume_request_id=first.resume_request_id,
        )
        assert rebound.claim_id == first.claim_id
        assert rebound.state == "REJECTED"
        assert rebound.rejection_code == "TASK_RESOLVED"

        async with factory() as uow:
            task = await uow.agents.get_task(task_id)
            budget = await uow.agents.get_task_budget(task_id)
            execution = await uow.agents.get_execution(_E1_EXECUTION)
            durable = await uow.agents.get_resume_claim(first.claim_id)
            assert task.status == terminal_status
            assert budget is None
            assert execution.state == "WAITING"
            assert durable.state != "CREATED", (
                "R9-H-P1: terminal legacy Task retains actionable CREATED "
                "ResumeClaim after existing-request replay", terminal_status
            )
            await uow.commit()
    finally:
        await engine.dispose()
