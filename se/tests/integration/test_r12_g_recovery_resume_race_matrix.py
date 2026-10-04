from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
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
from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeClaimState,
    ResumePlan,
    ResumeTriggerType,
    resume_plan_fingerprint,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_activation import AgentRecoveryActivationService
from se.src.runtimes.agent.recovery_planning import (
    AgentRecoveryPlanningService,
    RecoveryPlanRejected,
)
from se.src.runtimes.agent.resume_claim import ResumeClaimDeferred, ResumeClaimRejected
from se.src.runtimes.agent.task_budget import TaskBudgetService


USER = "user-r12-g0"
CLIENT = "client-r12-g0"
SESSION = "session-r12-g0"
AGENT = "agent-r12-g0"


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
        self.capability_invocations = CapabilityInvocationRepository(self.session)
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


async def _setup(tmp_path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    return engine, sessions, factory


def _store(factory):
    return DurableAgentStore(factory)


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
                owner_user_id=USER,
                status="ACTIVE",
            )
        )
        uow.session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id=AGENT,
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
                owner_user_id=USER,
                status="ACTIVE",
            )
        )
        uow.session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"session-{execution_id}",
                created_by=USER,
                assigned_agent_id=AGENT,
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
                policy_version="r12-g0",
                policy_fingerprint="a" * 64,
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
                agent_id=AGENT,
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
                created_by=USER,
            )
        )
        await uow.commit()

    recovered_revision = await TaskBudgetService(factory).recover_task_scoped_execution(
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
    return recovered


async def _recovery_plan_and_claim(
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


def _connection_plan(
    *,
    execution_id: str,
    checkpoint_id: str,
    revision: int,
    session_id: str,
    connection_id: str,
) -> ResumePlan:
    plan = ResumePlan(
        execution_id=execution_id,
        checkpoint_id=checkpoint_id,
        expected_execution_revision=revision,
        plan_fingerprint="",
        agent_id=AGENT,
        session_id=session_id,
        task_id=None,
        branch_id=None,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id=f"corr-{execution_id}",
        trace_id="trace-r12-g0",
        request_id="request-r12-g0",
        iteration=0,
        ordered_tool_call_ids=(),
        transcript_snapshot=(),
        remaining_active_budget_seconds=30.0,
        wait_expires_at=None,
        target_user_id=USER,
        target_client_id=CLIENT,
        target_connection_id=connection_id,
        invocation_actions=(),
    )
    return replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))


async def _seed_connection_cut(
    factory,
    *,
    execution_id: str,
    revision: int = 2,
    checkpoint_id: str | None = None,
):
    checkpoint_id = checkpoint_id or f"{execution_id}:connection:{revision}"
    session_id = f"session-{execution_id}"
    async with factory() as uow:
        uow.session.add(
            AgentSessionRecord(
                id=session_id,
                owner_user_id=USER,
                status="ACTIVE",
            )
        )
        uow.session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=session_id,
                agent_id=AGENT,
                correlation_id=f"corr-{execution_id}",
                state="WAITING",
                wait_reason="CONNECTION",
                revision=revision,
                current_checkpoint_id=checkpoint_id,
                bound_client_id=CLIENT,
                bound_connection_id=None,
                remaining_active_budget_seconds=30.0,
                request={},
            )
        )
        uow.session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=checkpoint_id,
                execution_id=execution_id,
                execution_revision=revision,
                session_id=session_id,
                iteration=0,
                wait_reason="CONNECTION",
                remaining_active_budget_seconds=30.0,
                origin_client_id=CLIENT,
                origin_connection_id="conn-old-r12-g0",
                transcript_snapshot=[],
                metadata_json={},
            )
        )
        await uow.commit()
    return _connection_plan(
        execution_id=execution_id,
        checkpoint_id=checkpoint_id,
        revision=revision,
        session_id=session_id,
        connection_id="conn-new-r12-g0",
    )


async def _create_reconnect_claim(store, plan: ResumePlan, request_id: str):
    return await store.get_or_create_resume_claim(
        ResumeClaimIntent(
            resume_request_id=request_id,
            execution_id=plan.execution_id,
            checkpoint_id=plan.checkpoint_id,
            expected_execution_revision=plan.expected_execution_revision,
            plan_fingerprint=plan.plan_fingerprint,
            user_id=plan.target_user_id,
            client_id=plan.target_client_id,
            connection_id=plan.target_connection_id,
            wait_reason="CONNECTION",
            trigger_type=ResumeTriggerType.CLIENT_RECONNECT,
            claim_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
    )


async def _consume_reconnect(store, plan: ResumePlan, claim):
    return await store.consume_resume_claim(
        ResumeClaimConsumeSpec(
            plan=plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            now_utc=datetime.now(timezone.utc),
        )
    )


@pytest.mark.asyncio
async def test_r12_g_multi_worker_task_recovery_has_one_winner_and_one_budget_slot(
    tmp_path,
):
    engine, _, factory = await _setup(tmp_path, "r12_g_multi_worker.sqlite")
    store_a = _store(factory)
    store_b = _store(factory)
    takeover_at = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    execution_id = "exec-r12-g-multi"
    task_id = "task-r12-g-multi"
    branch_id = "branch-r12-g-multi"
    try:
        recovered = await _seed_task_recovery_cut(
            factory,
            store_a,
            task_id=task_id,
            execution_id=execution_id,
            branch_id=branch_id,
            takeover_at=takeover_at,
        )
        planner_a, plan, claim_a = await _recovery_plan_and_claim(
            factory,
            store_a,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="rr-r12-g-a",
        )
        planner_b = AgentRecoveryPlanningService(
            factory,
            _Capabilities(),
            now_utc=lambda: activation_now,
        )
        claim_b = await store_b.get_or_create_resume_claim(
            ResumeClaimIntent(
                resume_request_id="rr-r12-g-b",
                execution_id=plan.execution_id,
                checkpoint_id=plan.checkpoint_id,
                expected_execution_revision=plan.expected_execution_revision,
                plan_fingerprint=plan.plan_fingerprint,
                user_id=plan.resolved_recovery_principal,
                client_id=plan.target_client_id,
                connection_id=plan.target_connection_id,
                wait_reason="RECOVERY",
                trigger_type=ResumeTriggerType.SERVER_RECOVERY,
                claim_expires_at=activation_now + timedelta(minutes=2),
            )
        )

        async def attempt(service, claim, owner):
            try:
                return await service.activate(
                    plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    activation_owner_instance_id=owner,
                    activation_now_utc=activation_now,
                    activation_lease_expires_at=activation_now + timedelta(seconds=30),
                )
            except (RecoveryPlanRejected, ResumeClaimRejected, ResumeClaimDeferred) as exc:
                return exc

        outcomes = await asyncio.gather(
            attempt(
                AgentRecoveryActivationService(planner_a, store_a),
                claim_a,
                "worker-r12-g-a",
            ),
            attempt(
                AgentRecoveryActivationService(planner_b, store_b),
                claim_b,
                "worker-r12-g-b",
            ),
        )
        winners = [item for item in outcomes if not isinstance(item, BaseException)]
        assert len(winners) == 1

        async with factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            budget = await uow.agents.get_task_budget(task_id)
            row_a = await uow.agents.get_resume_claim(claim_a.claim_id)
            row_b = await uow.agents.get_resume_claim(claim_b.claim_id)
            assert execution.state == "RUNNING"
            assert execution.owner_instance_id in {"worker-r12-g-a", "worker-r12-g-b"}
            assert execution.lease_generation == recovered.lease_generation + 1
            assert execution.task_id == task_id
            assert execution.branch_id == branch_id
            assert execution.remaining_active_budget_seconds == 30.0
            assert budget.active_executions == 1
            assert budget.incarnation_generation == plan.task_budget_incarnation_generation
            assert sum(
                row.state == ResumeClaimState.CONSUMED.value
                for row in (row_a, row_b)
            ) == 1
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_g_restart_fresh_service_consumes_durable_created_claim(tmp_path):
    database_name = "r12_g_restart.sqlite"
    database_url = f"sqlite+aiosqlite:///{(tmp_path / database_name).as_posix()}"
    engine, _, factory = await _setup(tmp_path, database_name)
    takeover_at = datetime(2026, 10, 4, 1, 10, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    execution_id = "exec-r12-g-restart"
    try:
        store_before = _store(factory)
        await _seed_recovery_cut(
            factory,
            store_before,
            execution_id=execution_id,
            takeover_at=takeover_at,
        )
        _, _, claim = await _recovery_plan_and_claim(
            factory,
            store_before,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="rr-r12-g-restart",
        )

        # Simulate a real process restart: release the old pool, reconnect to the
        # same durable database, and reconstruct all activation inputs.
        await engine.dispose()
        engine = create_async_engine(database_url, connect_args={"timeout": 5})
        sessions_after = async_sessionmaker(engine, expire_on_commit=False)
        factory_after = lambda: _Uow(sessions_after)
        store_after = _store(factory_after)
        planner_after = AgentRecoveryPlanningService(
            factory_after,
            _Capabilities(),
            now_utc=lambda: activation_now,
        )
        plan_after = await planner_after.build_recovery_plan(execution_id)

        async with factory_after() as uow:
            durable_claim_before = await uow.agents.get_resume_claim(claim.claim_id)
            assert durable_claim_before is not None
            assert durable_claim_before.state == ResumeClaimState.CREATED.value
            assert durable_claim_before.plan_fingerprint == plan_after.plan_fingerprint
            await uow.commit()

        activated = await AgentRecoveryActivationService(
            planner_after,
            store_after,
        ).activate(
            plan_after,
            claim_id=durable_claim_before.claim_id,
            resume_request_id=durable_claim_before.resume_request_id,
            expected_claim_revision=durable_claim_before.revision,
            activation_owner_instance_id="worker-r12-g-restarted",
            activation_now_utc=activation_now,
            activation_lease_expires_at=activation_now + timedelta(seconds=30),
        )
        assert activated.already_consumed is False

        async with factory_after() as uow:
            execution = await uow.agents.get_execution(execution_id)
            durable_claim = await uow.agents.get_resume_claim(durable_claim_before.claim_id)
            assert execution.state == "RUNNING"
            assert execution.owner_instance_id == "worker-r12-g-restarted"
            assert durable_claim.state == ResumeClaimState.CONSUMED.value
            assert durable_claim.consumed_execution_revision == execution.revision
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_g_stale_reconnect_claim_cannot_activate_newer_recovery_cut(tmp_path):
    engine, _, factory = await _setup(tmp_path, "r12_g_reconnect_then_recovery.sqlite")
    store = _store(factory)
    execution_id = "exec-r12-g-reconnect-stale"
    lease_now = datetime(2026, 10, 4, 1, 20, tzinfo=timezone.utc)
    try:
        plan = await _seed_connection_cut(factory, execution_id=execution_id)
        stale_claim = await _create_reconnect_claim(
            store,
            plan,
            "rr-r12-g-stale-reconnect",
        )
        winner_claim = await _create_reconnect_claim(
            store,
            plan,
            "rr-r12-g-reconnect-winner",
        )
        consumed = await _consume_reconnect(store, plan, winner_claim)
        assert consumed.consumed_execution_revision == 3

        # A later recovery cut may freeze only durable user-run progress.  The
        # reconnect winner has resumed execution, so model the first persisted
        # iteration before its lease expires.  Without this row, production
        # correctly fails closed with SAFE_POINT_CHECKPOINT_ITERATION_MISSING.
        async with factory() as uow:
            uow.session.add(
                AgentIterationRecord(
                    id=f"{execution_id}:iteration:0",
                    execution_id=execution_id,
                    iteration=0,
                    state="THINKING",
                    tool_call_ids=[],
                )
            )
            await uow.commit()

        leased = await store.acquire_execution_lease(
            execution_id,
            owner_instance_id="worker-r12-g-user-run",
            now_utc=lease_now,
            lease_expires_at=lease_now + timedelta(seconds=30),
        )
        recovered = await store.commit_recovery_waiting_checkpoint(
            execution_id,
            observed_owner_instance_id=leased.owner_instance_id,
            observed_lease_generation=leased.lease_generation,
            observed_lease_expires_at=leased.lease_expires_at,
            takeover_now_utc=leased.lease_expires_at,
        )
        assert recovered.state == "WAITING"
        assert recovered.wait_reason == "RECOVERY"
        assert recovered.revision > plan.expected_execution_revision

        with pytest.raises(ResumeClaimRejected):
            await _consume_reconnect(store, plan, stale_claim)

        loaded = await store.load_execution(execution_id)
        assert loaded.state == "WAITING"
        assert loaded.wait_reason == "RECOVERY"
        assert loaded.current_checkpoint_id != plan.checkpoint_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_g_stale_recovery_claim_cannot_activate_after_valid_reconnect(tmp_path):
    engine, _, factory = await _setup(tmp_path, "r12_g_recovery_then_reconnect.sqlite")
    store = _store(factory)
    takeover_at = datetime(2026, 10, 4, 1, 30, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    execution_id = "exec-r12-g-recovery-stale"
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id=execution_id,
            takeover_at=takeover_at,
        )
        stale_planner, stale_plan, stale_claim = await _recovery_plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="rr-r12-g-stale-recovery",
        )
        _, winning_plan, winning_claim = await _recovery_plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="rr-r12-g-recovery-winner",
        )
        await AgentRecoveryActivationService(
            stale_planner,
            store,
        ).activate(
            winning_plan,
            claim_id=winning_claim.claim_id,
            resume_request_id=winning_claim.resume_request_id,
            expected_claim_revision=winning_claim.revision,
            activation_owner_instance_id="worker-r12-g-before-user",
            activation_now_utc=activation_now,
            activation_lease_expires_at=activation_now + timedelta(seconds=60),
        )

        running = await store.load_execution(execution_id)
        connection_revision = int(running.revision) + 1
        connection_checkpoint = f"{execution_id}:connection:{connection_revision}"
        await store.commit_waiting_checkpoint(
            execution_id,
            int(running.revision),
            {
                "state": "WAITING",
                "wait_reason": "CONNECTION",
                "wait_expires_at": None,
                "remaining_active_budget_seconds": 30.0,
                "bound_client_id": CLIENT,
                "bound_connection_id": None,
                "owner_instance_id": None,
                "lease_expires_at": None,
                "completed_at": None,
            },
            checkpoint_values={
                "checkpoint_id": connection_checkpoint,
                "execution_id": execution_id,
                "execution_revision": connection_revision,
                "session_id": running.session_id,
                "iteration": 0,
                "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
                "origin_client_id": CLIENT,
                "origin_connection_id": "conn-r12-g-before-user",
                "transcript_snapshot": [],
                "metadata_json": {},
            },
            pending_invocations=[],
        )
        reconnect_plan = _connection_plan(
            execution_id=execution_id,
            checkpoint_id=connection_checkpoint,
            revision=connection_revision,
            session_id=running.session_id,
            connection_id="conn-r12-g-user-winner",
        )
        reconnect_claim = await _create_reconnect_claim(
            store,
            reconnect_plan,
            "rr-r12-g-user-winner",
        )
        reconnect = await _consume_reconnect(store, reconnect_plan, reconnect_claim)
        assert reconnect.consumed_execution_revision == connection_revision + 1

        with pytest.raises((RecoveryPlanRejected, ResumeClaimRejected, ResumeClaimDeferred)):
            await AgentRecoveryActivationService(
                stale_planner,
                store,
            ).activate(
                stale_plan,
                claim_id=stale_claim.claim_id,
                resume_request_id=stale_claim.resume_request_id,
                expected_claim_revision=stale_claim.revision,
                activation_owner_instance_id="worker-r12-g-stale",
                activation_now_utc=activation_now + timedelta(seconds=2),
                activation_lease_expires_at=activation_now + timedelta(seconds=90),
            )

        loaded = await store.load_execution(execution_id)
        assert loaded.state == "RUNNING"
        assert loaded.bound_connection_id == "conn-r12-g-user-winner"
        assert loaded.revision == connection_revision + 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_g_terminal_execution_rejects_stale_recovery_activation(tmp_path):
    engine, _, factory = await _setup(tmp_path, "r12_g_terminal.sqlite")
    store = _store(factory)
    takeover_at = datetime(2026, 10, 4, 1, 40, tzinfo=timezone.utc)
    activation_now = takeover_at + timedelta(seconds=5)
    execution_id = "exec-r12-g-terminal"
    try:
        await _seed_recovery_cut(
            factory,
            store,
            execution_id=execution_id,
            takeover_at=takeover_at,
        )
        planner, plan, claim = await _recovery_plan_and_claim(
            factory,
            store,
            execution_id=execution_id,
            now_utc=activation_now,
            request_id="rr-r12-g-terminal",
        )
        await store.update_execution(
            execution_id,
            {
                "state": "COMPLETED",
                "revision": plan.expected_execution_revision + 1,
                "completed_at": activation_now,
            },
        )

        with pytest.raises((RecoveryPlanRejected, ResumeClaimRejected, ResumeClaimDeferred)):
            await AgentRecoveryActivationService(planner, store).activate(
                plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                activation_owner_instance_id="worker-r12-g-terminal-stale",
                activation_now_utc=activation_now + timedelta(seconds=1),
                activation_lease_expires_at=activation_now + timedelta(seconds=30),
            )

        loaded = await store.load_execution(execution_id)
        assert loaded.state == "COMPLETED"
        assert loaded.revision == plan.expected_execution_revision + 1
    finally:
        await engine.dispose()
