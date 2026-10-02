from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentSessionRecord,
    AgentTaskBranchRecord,
    AgentTaskRecord,
    TaskBudgetRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.contracts.recovery import RecoveryActivationSpec
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimIntent,
    ResumeClaimState,
    ResumeTriggerType,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_activation import (
    AgentRecoveryActivationService,
)
from se.src.runtimes.agent.recovery_planning import (
    AgentRecoveryPlanningService,
)
from se.src.runtimes.agent.resume_claim import (
    ResumeClaimDeferred,
    ResumeClaimRejected,
)
from se.src.runtimes.agent.task_budget import TaskBudgetService


class _Uow:
    def __init__(self, sessions, repository_cls=AgentRepository):
        self._sessions = sessions
        self._repository_cls = repository_cls
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = self._repository_cls(self.session)
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


class _Capabilities:
    tool_quota_service = None


class _RejectLeaseRepository(AgentRepository):
    async def acquire_execution_lease(self, *args, **kwargs):
        return None


class _RejectConsumedClaimRepository(AgentRepository):
    async def compare_and_set_resume_claim(
        self,
        claim_id,
        expected_revision,
        expected_state,
        values,
    ):
        if values.get("state") == ResumeClaimState.CONSUMED.value:
            return None
        return await super().compare_and_set_resume_claim(
            claim_id,
            expected_revision,
            expected_state,
            values,
        )


async def _setup(tmp_path, name: str, *, repository_cls=AgentRepository):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions, repository_cls)
    store = DurableAgentStore(factory)
    return engine, factory, store


async def _seed_recovery_cut(
    factory,
    store,
    *,
    execution_id: str,
    takeover_at: datetime,
):
    async with factory() as uow:
        uow.session.add(
            AgentSessionRecord(
                id=f"session-{execution_id}",
                owner_user_id="user-r12-f2",
                status="ACTIVE",
            )
        )
        uow.session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id="agent-r12-f2",
                correlation_id=f"corr-{execution_id}",
                state="RUNNING",
                revision=2,
                owner_instance_id="worker-expired",
                lease_expires_at=takeover_at,
                lease_generation=1,
                remaining_active_budget_seconds=30.0,
                request={"prompt": "recover"},
                transcript=[],
            )
        )
        await uow.commit()

    recovered = await store.commit_recovery_waiting_checkpoint(
        execution_id,
        observed_owner_instance_id="worker-expired",
        observed_lease_generation=1,
        observed_lease_expires_at=takeover_at,
        takeover_now_utc=takeover_at,
    )
    assert recovered.state == "WAITING"
    assert recovered.wait_reason == "RECOVERY"
    assert recovered.owner_instance_id is None
    assert recovered.lease_expires_at is None
    assert recovered.lease_generation == 2
    return recovered



async def _seed_task_recovery_cut(
    factory,
    store,
    *,
    task_id: str,
    execution_id: str,
    branch_id: str,
    takeover_at: datetime,
):
    async with factory() as uow:
        uow.session.add(
            AgentSessionRecord(
                id=f"session-{execution_id}",
                owner_user_id="user-r12-f2",
                status="ACTIVE",
            )
        )
        uow.session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"session-{execution_id}",
                created_by="user-r12-f2",
                assigned_agent_id="agent-r12-f2",
                status="RUNNING",
                wait_reasons=[],
                input={},
            )
        )
        uow.session.add(
            TaskBudgetRecord(
                task_id=task_id,
                incarnation_generation=1,
                revision=0,
                state="OPEN",
                max_total_executions=10,
                max_active_executions=4,
                max_active_branches=4,
                max_parallel_agents=4,
                max_total_tool_calls=100,
                max_total_inference_calls=100,
                max_total_tokens=100000,
                max_total_cost_usd=Decimal("100.00"),
                max_delegation_depth=8,
                policy_version="r12-f2",
                policy_fingerprint="f" * 64,
                deny_recursive_agent_cycle=True,
                used_executions=1,
                active_executions=1,
                active_branches=1,
                active_parallel_agents=0,
                used_tool_calls=0,
                used_inference_calls=0,
                used_tokens=0,
                used_cost_usd=Decimal("0.00000000"),
            )
        )
        uow.session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id="agent-r12-f2",
                task_id=task_id,
                branch_id=branch_id,
                correlation_id=f"corr-{execution_id}",
                state="RUNNING",
                revision=4,
                owner_instance_id="worker-expired",
                lease_expires_at=takeover_at,
                lease_generation=2,
                remaining_active_budget_seconds=30.0,
                request={"prompt": "recover-task"},
                transcript=[],
            )
        )
        uow.session.add(
            AgentTaskBranchRecord(
                branch_id=branch_id,
                task_id=task_id,
                current_execution_id=execution_id,
                resolution_state="OPEN",
                revision=0,
                created_by="user-r12-f2",
            )
        )
        await uow.commit()

    budget_service = TaskBudgetService(factory)
    recovered_revision = await budget_service.recover_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        observed_owner_instance_id="worker-expired",
        observed_lease_generation=2,
        observed_lease_expires_at=takeover_at,
        takeover_now_utc=takeover_at,
    )
    recovered = await store.load_execution(execution_id)
    assert recovered_revision == 5
    assert recovered.state == "WAITING"
    assert recovered.wait_reason == "RECOVERY"
    assert recovered.lease_generation == 3
    return recovered


async def _plan_and_claim(
    factory,
    store,
    *,
    execution_id: str,
    now_utc: datetime,
    request_id: str,
):
    planner = AgentRecoveryPlanningService(
        factory,
        _Capabilities(),
        now_utc=lambda: now_utc,
    )
    plan = await planner.build_recovery_plan(execution_id)
    claim = await store.get_or_create_resume_claim(
        ResumeClaimIntent(
            resume_request_id=request_id,
            execution_id=plan.execution_id,
            checkpoint_id=plan.checkpoint_id,
            expected_execution_revision=plan.expected_execution_revision,
            plan_fingerprint=plan.plan_fingerprint,
            user_id=plan.resolved_recovery_principal,
            client_id=plan.target_client_id,
            connection_id=plan.target_connection_id,
            wait_reason="RECOVERY",
            trigger_type=ResumeTriggerType.SERVER_RECOVERY,
            claim_expires_at=now_utc + timedelta(minutes=2),
        )
    )
    return planner, plan, claim


@pytest.mark.asyncio
async def test_r12_f2_atomic_activation_and_consumed_replay_do_not_remint_lease(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_atomic_replay.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    lease_expiry = activation_now + timedelta(seconds=30)
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id="exec-r12-f2-replay",
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id="exec-r12-f2-replay",
            now_utc=activation_now,
            request_id="recover-r12-f2-replay",
        )
        service = AgentRecoveryActivationService(planner, store)

        activated = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2",
            activation_now_utc=activation_now,
            activation_lease_expires_at=lease_expiry,
        )
        assert activated.already_consumed is False
        assert activated.consumed_execution_revision == (
            plan.expected_execution_revision + 1
        )
        assert activated.lease_generation == (
            plan.expected_unowned_lease_generation + 1
        )

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            persisted_claim = await uow.agents.get_resume_claim(claim.claim_id)
            handoff = dict(persisted_claim.metadata_json or {}).get(
                "r12_f2_activation_handoff"
            )
            assert execution.state == "RUNNING"
            persisted_started_at = execution.started_at
            if persisted_started_at.tzinfo is None:
                persisted_started_at = persisted_started_at.replace(
                    tzinfo=timezone.utc
                )
            assert persisted_started_at == activation_now
            assert execution.owner_instance_id == "worker-r12-f2"
            assert execution.lease_generation == activated.lease_generation
            assert execution.lease_expires_at == lease_expiry
            assert persisted_claim.state == ResumeClaimState.CONSUMED.value
            assert handoff["kind"] == "SERVER_RECOVERY_ACTIVATION"
            assert handoff["version"] == 1
            assert handoff["activation_owner_instance_id"] == "worker-r12-f2"
            assert handoff["activation_now_utc"] == activation_now.isoformat()
            first_generation = execution.lease_generation
            await uow.commit()

        replayed = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2",
            activation_now_utc=activation_now + timedelta(seconds=1),
            activation_lease_expires_at=lease_expiry,
        )
        assert replayed.already_consumed is True
        assert replayed.lease_generation == first_generation

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            assert execution.lease_generation == first_generation
            assert execution.owner_instance_id == "worker-r12-f2"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_consumed_replay_rejects_foreign_owner_without_lease_change(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_foreign_replay.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 10, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    lease_expiry = activation_now + timedelta(seconds=30)
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id="exec-r12-f2-foreign",
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id="exec-r12-f2-foreign",
            now_utc=activation_now,
            request_id="recover-r12-f2-foreign",
        )
        service = AgentRecoveryActivationService(planner, store)
        activated = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2",
            activation_now_utc=activation_now,
            activation_lease_expires_at=lease_expiry,
        )

        with pytest.raises(
            ResumeClaimRejected,
            match="STALE_RECOVERY_ACTIVATION",
        ):
            await service.activate(
                plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                activation_owner_instance_id="worker-foreign",
                activation_now_utc=activation_now + timedelta(seconds=1),
                activation_lease_expires_at=lease_expiry,
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            assert execution.owner_instance_id == "worker-r12-f2"
            assert execution.lease_generation == activated.lease_generation
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_lease_acquire_loss_rolls_back_running_and_claim(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_lease_rollback.sqlite",
        repository_cls=_RejectLeaseRepository,
    )
    takeover_at = datetime(2026, 10, 2, 7, 20, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id="exec-r12-f2-lease-loss",
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id="exec-r12-f2-lease-loss",
            now_utc=activation_now,
            request_id="recover-r12-f2-lease-loss",
        )
        service = AgentRecoveryActivationService(planner, store)

        with pytest.raises(
            ResumeClaimDeferred,
            match="RECOVERY_ACTIVATION_CONFLICT",
        ):
            await service.activate(
                plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                activation_owner_instance_id="worker-r12-f2",
                activation_now_utc=activation_now,
                activation_lease_expires_at=(
                    activation_now + timedelta(seconds=30)
                ),
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            persisted_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.wait_reason == "RECOVERY"
            assert execution.owner_instance_id is None
            assert execution.lease_expires_at is None
            assert (
                execution.lease_generation
                == plan.expected_unowned_lease_generation
            )
            assert persisted_claim.state == ResumeClaimState.CREATED.value
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_claim_cas_loss_rolls_back_fresh_lease_and_running(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_claim_rollback.sqlite",
        repository_cls=_RejectConsumedClaimRepository,
    )
    takeover_at = datetime(2026, 10, 2, 7, 30, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id="exec-r12-f2-claim-loss",
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id="exec-r12-f2-claim-loss",
            now_utc=activation_now,
            request_id="recover-r12-f2-claim-loss",
        )
        service = AgentRecoveryActivationService(planner, store)

        with pytest.raises(
            ResumeClaimDeferred,
            match="RECOVERY_ACTIVATION_CONFLICT",
        ):
            await service.activate(
                plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                activation_owner_instance_id="worker-r12-f2",
                activation_now_utc=activation_now,
                activation_lease_expires_at=(
                    activation_now + timedelta(seconds=30)
                ),
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            persisted_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.wait_reason == "RECOVERY"
            assert execution.owner_instance_id is None
            assert execution.lease_expires_at is None
            assert (
                execution.lease_generation
                == plan.expected_unowned_lease_generation
            )
            assert persisted_claim.state == ResumeClaimState.CREATED.value
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_task_scoped_activation_reacquires_capacity_once(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_task_success.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 35, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    lease_expiry = activation_now + timedelta(seconds=30)
    task_id = "task-r12-f2"
    execution_id = "exec-task-r12-f2"
    branch_id = "branch-r12-f2"
    try:
        await _seed_task_recovery_cut(
            factory,
            store,
            task_id=task_id,
            execution_id=execution_id,
            branch_id=branch_id,
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="recover-r12-f2-task",
        )
        service = AgentRecoveryActivationService(planner, store)

        async with factory() as uow:
            budget_before = await uow.agents.get_task_budget(task_id)
            assert budget_before.active_executions == 0
            assert (
                budget_before.incarnation_generation
                == plan.task_budget_incarnation_generation
            )
            await uow.commit()

        activated = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2-task",
            activation_now_utc=activation_now,
            activation_lease_expires_at=lease_expiry,
        )
        assert activated.already_consumed is False

        async with factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            budget = await uow.agents.get_task_budget(task_id)
            reservation = await uow.agents.get_task_budget_reservation(
                task_id,
                "RESUME_EXECUTION",
                f"{execution_id}:{plan.expected_execution_revision}",
                expected_incarnation_generation=(
                    plan.task_budget_incarnation_generation
                ),
            )
            assert execution.state == "RUNNING"
            assert execution.owner_instance_id == "worker-r12-f2-task"
            assert budget.active_executions == 1
            assert (
                budget.incarnation_generation
                == plan.task_budget_incarnation_generation
            )
            assert reservation is not None
            await uow.commit()

        replayed = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2-task",
            activation_now_utc=activation_now + timedelta(seconds=1),
            activation_lease_expires_at=lease_expiry,
        )
        assert replayed.already_consumed is True

        async with factory() as uow:
            budget = await uow.agents.get_task_budget(task_id)
            assert budget.active_executions == 1
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_mutable_lineage_revision_drift_is_not_activation_identity(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_mutable_revision_drift.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 36, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    task_id = "task-r12-f2-revision-drift"
    execution_id = "exec-task-r12-f2-revision-drift"
    branch_id = "branch-r12-f2-revision-drift"
    try:
        await _seed_task_recovery_cut(
            factory,
            store,
            task_id=task_id,
            execution_id=execution_id,
            branch_id=branch_id,
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="recover-r12-f2-revision-drift",
        )
        service = AgentRecoveryActivationService(planner, store)

        async with factory() as uow:
            task = await uow.agents.get_task(task_id)
            budget = await uow.agents.get_task_budget(task_id)
            branch = await uow.agents.get_task_branch(branch_id)
            task.revision = int(task.revision) + 1
            budget.revision = int(budget.revision) + 1
            branch.revision = int(branch.revision) + 1
            await uow.commit()

        fresh = await planner.build_recovery_plan(execution_id)
        assert fresh.task_revision != plan.task_revision
        assert fresh.task_budget_revision != plan.task_budget_revision
        assert fresh.branch_revision != plan.branch_revision
        assert fresh.task_budget_incarnation_generation == (
            plan.task_budget_incarnation_generation
        )
        # Mutable Task/Branch/TaskBudget revisions remain observable planning
        # evidence but are not part of immutable recovery activation identity.
        assert fresh.plan_fingerprint == plan.plan_fingerprint

        activated = await service.activate(
            plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            activation_owner_instance_id="worker-r12-f2-revision-drift",
            activation_now_utc=activation_now,
            activation_lease_expires_at=(
                activation_now + timedelta(seconds=30)
            ),
        )
        assert activated.already_consumed is False

        async with factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            budget = await uow.agents.get_task_budget(task_id)
            assert execution.state == "RUNNING"
            assert (
                execution.owner_instance_id
                == "worker-r12-f2-revision-drift"
            )
            assert budget.active_executions == 1
            assert budget.incarnation_generation == (
                plan.task_budget_incarnation_generation
            )
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_task_budget_incarnation_drift_fails_before_capacity(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_task_generation_drift.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 37, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    task_id = "task-r12-f2-drift"
    execution_id = "exec-task-r12-f2-drift"
    branch_id = "branch-r12-f2-drift"
    try:
        await _seed_task_recovery_cut(
            factory,
            store,
            task_id=task_id,
            execution_id=execution_id,
            branch_id=branch_id,
            takeover_at=takeover_at,
        )
        _, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="recover-r12-f2-task-drift",
        )

        async with factory() as uow:
            budget = await uow.agents.get_task_budget(task_id)
            budget.incarnation_generation = (
                int(plan.task_budget_incarnation_generation) + 1
            )
            await uow.commit()

        with pytest.raises(
            ResumeClaimRejected,
            match="TASK_BUDGET_CONFLICT",
        ):
            await store.consume_recovery_claim(
                RecoveryActivationSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    activation_owner_instance_id="worker-r12-f2-task",
                    activation_now_utc=activation_now,
                    activation_lease_expires_at=(
                        activation_now + timedelta(seconds=30)
                    ),
                )
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            budget = await uow.agents.get_task_budget(task_id)
            reservation = await uow.agents.get_task_budget_reservation(
                task_id,
                "RESUME_EXECUTION",
                f"{execution_id}:{plan.expected_execution_revision}",
                expected_incarnation_generation=(
                    plan.task_budget_incarnation_generation
                ),
            )
            persisted_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.owner_instance_id is None
            assert budget.active_executions == 0
            assert reservation is None
            assert persisted_claim.state == ResumeClaimState.REJECTED.value
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_f2_non_null_recovery_wait_ttl_is_not_synthesized_or_consumed(
    tmp_path,
):
    engine, factory, store = await _setup(
        tmp_path,
        "r12_f2_non_null_ttl.sqlite",
    )
    takeover_at = datetime(2026, 10, 2, 7, 40, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    future_wait_expiry = activation_now + timedelta(minutes=5)
    try:
        recovered = await _seed_recovery_cut(
            factory,
            store,
            execution_id="exec-r12-f2-ttl",
            takeover_at=takeover_at,
        )
        async with factory() as uow:
            execution = await uow.agents.get_execution("exec-r12-f2-ttl")
            checkpoint = await uow.agents.get_execution_checkpoint(
                recovered.current_checkpoint_id
            )
            execution.wait_expires_at = future_wait_expiry
            checkpoint.wait_expires_at = future_wait_expiry
            await uow.commit()

        planner, plan, claim = await _plan_and_claim(
            factory,
            store,
            execution_id="exec-r12-f2-ttl",
            now_utc=activation_now,
            request_id="recover-r12-f2-ttl",
        )
        service = AgentRecoveryActivationService(planner, store)

        with pytest.raises(
            ResumeClaimRejected,
            match="RECOVERY_WAIT_TTL_UNRELEASED",
        ):
            await service.activate(
                plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                activation_owner_instance_id="worker-r12-f2",
                activation_now_utc=activation_now,
                activation_lease_expires_at=(
                    activation_now + timedelta(seconds=30)
                ),
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            persisted_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.wait_reason == "RECOVERY"
            assert execution.owner_instance_id is None
            assert execution.lease_expires_at is None
            assert persisted_claim.state == ResumeClaimState.REJECTED.value
            assert (
                persisted_claim.rejection_code
                == "RECOVERY_WAIT_TTL_UNRELEASED"
            )
            await uow.commit()
    finally:
        await engine.dispose()
