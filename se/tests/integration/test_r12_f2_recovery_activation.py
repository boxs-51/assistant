from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentSessionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
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
            assert execution.owner_instance_id == "worker-r12-f2"
            assert execution.lease_generation == activated.lease_generation
            assert execution.lease_expires_at == lease_expiry.replace(tzinfo=None)
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
