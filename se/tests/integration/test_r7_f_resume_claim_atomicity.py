from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
    ResumeTriggerType,
    resume_plan_fingerprint,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_claim import ResumeClaimDeferred, ResumeClaimRejected
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import TaskBudgetService
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)


USER = "user-r7f"
CLIENT = "client-r7f"
K1 = "conn-r7f-k1"
K2 = "conn-r7f-k2"
SESSION = "session-r7f"
AGENT = "agent-r7f"
EXECUTION = "exec-r7f"
CHECKPOINT = "cp-r7f"
INVOCATION = "inv-r7f"
TOOL_CALL = "call-r7f"
CAPABILITY = "tool.r7f"
FINGERPRINT = "f" * 64


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

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


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=2,
        max_total_tool_calls=8,
        max_total_inference_calls=8,
        max_total_tokens=1000,
        max_total_cost_usd="10",
        max_delegation_depth=4,
    )


async def _setup(tmp_path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    return engine, sessions, factory, DurableAgentStore(factory)


def _invocation_values(*, execution_id=EXECUTION, revision=5):
    return {
        "invocation_id": INVOCATION,
        "capability_id": CAPABILITY,
        "capability_version": "1.0",
        "kind": "TOOL",
        "execution_mode": "ONE_SHOT",
        "idempotency": "IDEMPOTENT",
        "request_fingerprint": FINGERPRINT,
        "owner_user_id": USER,
        "origin_client_id": CLIENT,
        "remote_outcome_state": "OUTCOME_UNKNOWN",
        "implementation_id": f"{K1}:{CAPABILITY}",
        "driver_kind": "REMOTE_CLIENT",
        "state": "WAITING",
        "wait_reason": "CONNECTION",
        "session_id": SESSION,
        "execution_id": execution_id,
        "tool_call_id": TOOL_CALL,
        "connection_id": K1,
        "attempt": 1,
        "max_attempts": 1,
        "arguments": {"value": "x"},
        "revision": revision,
    }


def _plan(
    *,
    task_id=None,
    branch_id=None,
    revision=2,
    wait_expires_at=None,
) -> ResumePlan:
    action = ResumeInvocationAction(
        invocation_id=INVOCATION,
        tool_call_id=TOOL_CALL,
        ordinal=0,
        capability_id=CAPABILITY,
        capability_version="1.0",
        request_fingerprint=FINGERPRINT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        expected_invocation_revision=5,
        expected_invocation_state=CapabilityInvocationState.WAITING,
        expected_remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
        action=ResumeInvocationActionKind.REPLAY_SAFE,
    )
    plan = ResumePlan(
        execution_id=EXECUTION,
        checkpoint_id=CHECKPOINT,
        expected_execution_revision=revision,
        plan_fingerprint="",
        agent_id=AGENT,
        session_id=SESSION,
        task_id=task_id,
        branch_id=branch_id,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id="corr-r7f",
        trace_id="trace-r7f",
        request_id="request-r7f",
        iteration=1,
        ordered_tool_call_ids=(TOOL_CALL,),
        transcript_snapshot=(InferenceMessage(role="assistant", content=""),),
        remaining_active_budget_seconds=20.0,
        wait_expires_at=wait_expires_at,
        target_user_id=USER,
        target_client_id=CLIENT,
        target_connection_id=K2,
        invocation_actions=(action,),
    )
    return replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))


def _intent(plan: ResumePlan, request_id: str, *, expires_at=None):
    return ResumeClaimIntent(
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
        claim_expires_at=expires_at
        or datetime.now(timezone.utc) + timedelta(minutes=1),
    )


async def _seed_non_task(sessions, *, wait_expires_at=None):
    async with sessions() as session:
        session.add(
            AgentExecutionRecord(
                id=EXECUTION,
                session_id=SESSION,
                agent_id=AGENT,
                correlation_id="corr-r7f",
                state="WAITING",
                wait_reason="CONNECTION",
                revision=2,
                current_checkpoint_id=CHECKPOINT,
                bound_client_id=CLIENT,
                bound_connection_id=None,
                remaining_active_budget_seconds=20.0,
                wait_expires_at=wait_expires_at,
                request={},
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=CHECKPOINT,
                execution_id=EXECUTION,
                execution_revision=2,
                session_id=SESSION,
                iteration=1,
                wait_reason="CONNECTION",
                remaining_active_budget_seconds=20.0,
                wait_expires_at=wait_expires_at,
                origin_client_id=CLIENT,
                origin_connection_id=K1,
                transcript_snapshot=[{"role": "assistant", "content": ""}],
                metadata_json={},
            )
        )
        session.add(CapabilityInvocationRecord(**_invocation_values()))
        await session.flush()
        repo = AgentRepository(session)
        await repo.save_checkpoint_pending_invocation(
            {
                "checkpoint_id": CHECKPOINT,
                "ordinal": 0,
                "invocation_id": INVOCATION,
                "invocation_revision": 5,
                "tool_call_id": TOOL_CALL,
                "capability_id": CAPABILITY,
                "capability_version": "1.0",
                "request_fingerprint": FINGERPRINT,
                "idempotency": "IDEMPOTENT",
                "observed_remote_outcome_state": "OUTCOME_UNKNOWN",
                "origin_client_id": CLIENT,
                "origin_connection_id": K1,
            }
        )
        await session.commit()


async def _seed_task_waiting(sessions, factory):
    budget = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r7-f"),
    )
    await budget.create_task_with_budget(
        {
            "id": "task-r7f",
            "session_id": SESSION,
            "created_by": USER,
            "assigned_agent_id": AGENT,
            "revision": 0,
            "status": "RUNNING",
            "wait_reasons": [],
            "input": {},
        }
    )
    await budget.start_task_scoped_execution(
        "task-r7f",
        execution_id=EXECUTION,
        execution_values={
            "id": EXECUTION,
            "session_id": SESSION,
            "agent_id": AGENT,
            "task_id": "task-r7f",
            "correlation_id": "corr-r7f",
            "state": "RUNNING",
            "revision": 1,
            "request": {},
        },
    )
    async with factory() as uow:
        execution = await uow.agents.get_execution(EXECUTION)
        branch_id = execution.branch_id
        assert branch_id is not None
        uow.session.add(CapabilityInvocationRecord(**_invocation_values()))
        await uow.commit()

    await budget.finish_task_scoped_execution(
        "task-r7f",
        execution_id=EXECUTION,
        source_revision=1,
        transition_values={
            "state": "WAITING",
            "wait_reason": "CONNECTION",
            "remaining_active_budget_seconds": 20.0,
            "wait_expires_at": None,
            "completed_at": None,
        },
        delegated=False,
        checkpoint_values={
            "checkpoint_id": CHECKPOINT,
            "execution_id": EXECUTION,
            "execution_revision": 2,
            "session_id": SESSION,
            "task_id": "task-r7f",
            "branch_id": branch_id,
            "iteration": 1,
            "wait_reason": "CONNECTION",
            "remaining_active_budget_seconds": 20.0,
            "wait_expires_at": None,
            "origin_client_id": CLIENT,
            "origin_connection_id": K1,
            "transcript_snapshot": [{"role": "assistant", "content": ""}],
            "metadata_json": {},
        },
        pending_invocations=[
            {
                "ordinal": 0,
                "invocation_id": INVOCATION,
                "tool_call_id": TOOL_CALL,
                "capability_id": CAPABILITY,
            }
        ],
    )
    return branch_id


@pytest.mark.asyncio
async def test_r7_f_same_resume_request_is_idempotent_without_extending_ttl(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "intent.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        first_intent = _intent(plan, "rr-r7f")
        first = await store.get_or_create_resume_claim(first_intent)
        later = replace(
            first_intent,
            claim_expires_at=first_intent.claim_expires_at + timedelta(hours=1),
        )
        second = await store.get_or_create_resume_claim(later)
        assert second.claim_id == first.claim_id
        assert second.claim_expires_at == first.claim_expires_at

        with pytest.raises(ResumeClaimRejected) as raised:
            await store.get_or_create_resume_claim(
                replace(later, connection_id="conn-r7f-k3")
            )
        assert raised.value.code == "RESUME_REQUEST_CONFLICT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_non_task_consume_is_atomic_and_binds_k2(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "consume.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-consume"))
        result = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )
        assert result.consumed_execution_revision == 3
        assert result.already_consumed is False

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "RUNNING"
            assert execution.revision == 3
            assert execution.current_checkpoint_id == CHECKPOINT
            assert execution.bound_client_id == CLIENT
            assert execution.bound_connection_id == K2
            assert execution.remaining_active_budget_seconds == 20.0
            assert durable_claim.state == "CONSUMED"
            assert durable_claim.consumed_execution_revision == 3
            await uow.commit()

        duplicate = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )
        assert duplicate.already_consumed is True
        assert duplicate.consumed_execution_revision == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_late_invocation_revision_rejects_plan_before_execution_claim(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "stale.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-stale"))
        async with factory() as uow:
            invocation = await uow.capability_invocations.get_record(INVOCATION)
            invocation.revision = 6
            invocation.remote_outcome_state = "TERMINAL_COMMITTED"
            invocation.state = "COMPLETED"
            invocation.wait_reason = None
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
        assert raised.value.code == "STALE_RECONCILIATION_SNAPSHOT"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert durable_claim.state == "REJECTED"
            assert durable_claim.rejection_code == "STALE_RECONCILIATION_SNAPSHOT"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_claim_expiry_does_not_change_waiting_execution(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "claim-expiry.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        now = datetime.now(timezone.utc)
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-expired", expires_at=now - timedelta(seconds=1))
        )
        with pytest.raises(ResumeClaimRejected) as raised:
            await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=now,
                )
            )
        assert raised.value.code == "CLAIM_EXPIRED"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert durable_claim.state == "EXPIRED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_wait_expiry_terminalizes_execution_and_rejects_claim_atomically(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "wait-expiry.sqlite")
    try:
        now = datetime.now(timezone.utc)
        wait_expiry = now - timedelta(seconds=1)
        await _seed_non_task(sessions, wait_expires_at=wait_expiry)
        plan = _plan(wait_expires_at=wait_expiry)
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-wait-expired"))
        with pytest.raises(ResumeClaimRejected) as raised:
            await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=now,
                )
            )
        assert raised.value.code == "WAIT_EXPIRED"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "TIMEOUT"
            assert execution.revision == 3
            assert durable_claim.state == "REJECTED"
            assert durable_claim.rejection_code == "WAIT_EXPIRED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_task_budget_reacquire_rolls_back_when_execution_cas_loses(
    tmp_path,
    monkeypatch,
):
    engine, sessions, factory, store = await _setup(tmp_path, "budget-rollback.sqlite")
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-budget-rollback"))
        async with factory() as uow:
            task_before = await uow.agents.get_task("task-r7f")
            task_revision_before = int(task_before.revision)
            await uow.commit()

        async def lose_execution_cas(self, *args, **kwargs):
            return None

        monkeypatch.setattr(
            AgentRepository,
            "compare_and_set_waiting_execution",
            lose_execution_cas,
        )

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
        assert raised.value.code == "RESUME_CONFLICT"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            budget = await uow.agents.get_task_budget("task-r7f")
            reservation = await uow.agents.get_task_budget_reservation(
                "task-r7f",
                "RESUME_EXECUTION",
                f"{EXECUTION}:2",
            )
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            task_after = await uow.agents.get_task("task-r7f")
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert budget.active_executions == 0
            assert reservation is None
            assert durable_claim.state == "CREATED"
            assert int(task_after.revision) == task_revision_before
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_concurrent_same_request_creates_one_durable_claim(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "intent-race.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        intent = _intent(plan, "rr-create-race")
        first, second = await asyncio.gather(
            store.get_or_create_resume_claim(intent),
            store.get_or_create_resume_claim(intent),
        )
        assert first.claim_id == second.claim_id

        async with factory() as uow:
            row = await uow.agents.get_resume_claim_by_request_id("rr-create-race")
            assert row is not None
            assert row.claim_id == first.claim_id
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_task_scoped_consume_reacquires_capacity_once(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "budget-success.sqlite")
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-budget-success"))

        result = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )
        assert result.consumed_execution_revision == 3

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            budget = await uow.agents.get_task_budget("task-r7f")
            reservation = await uow.agents.get_task_budget_reservation(
                "task-r7f",
                "RESUME_EXECUTION",
                f"{EXECUTION}:2",
            )
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "RUNNING"
            assert execution.revision == 3
            assert budget.active_executions == 1
            assert budget.used_executions == 1
            assert reservation is not None
            assert durable_claim.state == "CONSUMED"
            await uow.commit()

        duplicate = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )
        assert duplicate.already_consumed is True
        async with factory() as uow:
            budget = await uow.agents.get_task_budget("task-r7f")
            assert budget.active_executions == 1
            assert budget.used_executions == 1
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation(
    tmp_path,
    monkeypatch,
):
    engine, sessions, factory, store = await _setup(tmp_path, "claim-cas-rollback.sqlite")
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(_intent(plan, "rr-claim-cas-loss"))
        original = AgentRepository.compare_and_set_resume_claim

        async def lose_consumption(self, *args, **kwargs):
            values = kwargs.get("values")
            if values is None and len(args) >= 4:
                values = args[3]
            if values and values.get("state") == "CONSUMED":
                return None
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(
            AgentRepository,
            "compare_and_set_resume_claim",
            lose_consumption,
        )

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
        assert raised.value.code == "STALE_RESUME_CLAIM"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            budget = await uow.agents.get_task_budget("task-r7f")
            reservation = await uow.agents.get_task_budget_reservation(
                "task-r7f",
                "RESUME_EXECUTION",
                f"{EXECUTION}:2",
            )
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert budget.active_executions == 0
            assert reservation is None
            assert durable_claim.state == "CREATED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f_two_distinct_claims_have_at_most_one_consumed_winner(tmp_path):
    engine, sessions, factory, store = await _setup(tmp_path, "claim-race.sqlite")
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        first = await store.get_or_create_resume_claim(_intent(plan, "rr-race-a"))
        second = await store.get_or_create_resume_claim(_intent(plan, "rr-race-b"))

        def spec(claim):
            return ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )

        outcomes = await asyncio.gather(
            store.consume_resume_claim(spec(first)),
            store.consume_resume_claim(spec(second)),
            return_exceptions=True,
        )
        successes = [item for item in outcomes if not isinstance(item, BaseException)]
        failures = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ResumeClaimRejected)
        assert failures[0].code in {"RESUME_CONFLICT", "STALE_RESUME_CLAIM"}

        async with factory() as uow:
            first_row = await uow.agents.get_resume_claim(first.claim_id)
            second_row = await uow.agents.get_resume_claim(second.claim_id)
            consumed = [
                row for row in (first_row, second_row)
                if row.state == "CONSUMED"
            ]
            execution = await uow.agents.get_execution(EXECUTION)
            assert len(consumed) == 1
            assert execution.state == "RUNNING"
            assert execution.revision == 3
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f4_prepare_resume_plan_context_is_read_only_and_checkpoint_directed(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(tmp_path, "f4-context.sqlite")
    try:
        await _seed_non_task(sessions)
        async with factory() as uow:
            iteration_id = f"{EXECUTION}:iteration:1"
            uow.session.add(
                AgentIterationRecord(
                    id=iteration_id,
                    execution_id=EXECUTION,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[TOOL_CALL],
                    inference_response={
                        "request_id": "inf-r7f4",
                        "execution_id": EXECUTION,
                        "iteration": 1,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [],
                            "metadata": {},
                        },
                        "finish_reason": "tool_calls",
                        "usage": {
                            "prompt_tokens": 11,
                            "completion_tokens": 7,
                            "total_tokens": 18,
                            "tool_invocations": 1,
                            "estimated_cost_usd": 0.25,
                        },
                        "provider": "test",
                        "model": "test",
                        "metadata": {},
                    },
                )
            )
            uow.session.add(
                AgentToolCallRecord(
                    id=TOOL_CALL,
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id=INVOCATION,
                    tool_call_id=TOOL_CALL,
                    capability_id=CAPABILITY,
                    arguments={"value": "r7f"},
                    status="PENDING",
                    extra_metadata={},
                )
            )
            uow.session.add(
                AgentToolResultRecord(
                    id=f"{EXECUTION}:{TOOL_CALL}",
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id=INVOCATION,
                    tool_call_id=TOOL_CALL,
                    capability_id=CAPABILITY,
                    success=False,
                    output=None,
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    error_message="disconnect",
                    retryable=True,
                    extra_metadata={"attempt": 3},
                    commit_state="PROVISIONAL",
                    attempt=3,
                )
            )
            await uow.commit()

        plan = _plan()
        identity = Identity(
            user_id=USER,
            session_id=SESSION,
            auth_type="api_key",
            scopes={"*"},
        )

        context = await store.prepare_resume_plan_context(
            plan,
            identity=identity,
        )

        assert context.execution_id == plan.execution_id
        assert context.connection_id == K2
        assert context.iteration == plan.iteration
        assert context.resume_revision == plan.expected_execution_revision
        assert context.resume_pending_tool_calls == []
        assert context.active_budget_running is False
        assert context.remaining_active_budget_seconds == 20.0
        assert context.tool_calls_used == 1
        assert context.retry_attempts_used == 2
        assert context.usage.prompt_tokens == 11
        assert context.usage.completion_tokens == 7
        assert context.usage.total_tokens == 18
        assert context.usage.tool_invocations == 1
        assert context.usage.estimated_cost_usd == 0.25
        assert context.metadata["client_id"] == CLIENT
        assert context.metadata["r7_resume_plan_fingerprint"] == plan.plan_fingerprint
        assert tuple(
            item["role"] for item in context.resume_transcript
        ) == tuple(item.role for item in plan.transcript_snapshot)

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert execution.bound_connection_id is None
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f3_revalidates_committed_active_slot_without_pending_action(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(
        tmp_path, "committed-active-slot.sqlite"
    )
    try:
        await _seed_non_task(sessions)
        async with factory() as uow:
            iteration_id = f"{EXECUTION}:iteration:1"
            uow.session.add(
                AgentIterationRecord(
                    id=iteration_id,
                    execution_id=EXECUTION,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[TOOL_CALL, "call-already-committed"],
                )
            )
            uow.session.add(
                AgentToolCallRecord(
                    id="tool-row-already-committed",
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id="inv-already-committed",
                    tool_call_id="call-already-committed",
                    capability_id="tool.already.committed",
                    arguments={},
                    status="COMPLETED",
                    extra_metadata={},
                )
            )
            uow.session.add(
                AgentToolResultRecord(
                    id="result-already-committed",
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id="inv-already-committed",
                    tool_call_id="call-already-committed",
                    capability_id="tool.already.committed",
                    success=True,
                    output={"value": "committed"},
                    retryable=False,
                    extra_metadata={},
                    commit_state="COMMITTED",
                    attempt=1,
                )
            )
            await uow.commit()

        base = _plan()
        plan = replace(
            base,
            ordered_tool_call_ids=(
                TOOL_CALL,
                "call-already-committed",
            ),
            plan_fingerprint="",
        )
        plan = replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-committed-active-slot")
        )

        result = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )
        assert result.consumed_execution_revision == 3

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "RUNNING"
            assert durable_claim.state == "CONSUMED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_f3_provisional_no_action_active_slot_blocks_claim(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(
        tmp_path, "provisional-active-slot.sqlite"
    )
    try:
        await _seed_non_task(sessions)
        async with factory() as uow:
            iteration_id = f"{EXECUTION}:iteration:1"
            uow.session.add(
                AgentIterationRecord(
                    id=iteration_id,
                    execution_id=EXECUTION,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[TOOL_CALL, "call-provisional"],
                )
            )
            uow.session.add(
                AgentToolCallRecord(
                    id="tool-row-provisional",
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id="inv-provisional",
                    tool_call_id="call-provisional",
                    capability_id="tool.provisional",
                    arguments={},
                    status="PENDING",
                    extra_metadata={},
                )
            )
            uow.session.add(
                AgentToolResultRecord(
                    id="result-provisional",
                    execution_id=EXECUTION,
                    iteration_id=iteration_id,
                    invocation_id="inv-provisional",
                    tool_call_id="call-provisional",
                    capability_id="tool.provisional",
                    success=False,
                    output=None,
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    error_message="unknown",
                    retryable=True,
                    extra_metadata={},
                    commit_state="PROVISIONAL",
                    attempt=1,
                )
            )
            await uow.commit()

        base = _plan()
        plan = replace(
            base,
            ordered_tool_call_ids=(TOOL_CALL, "call-provisional"),
            plan_fingerprint="",
        )
        plan = replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-provisional-active-slot")
        )

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
        assert raised.value.code == "STALE_RECONCILIATION_SNAPSHOT"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "WAITING"
            assert execution.revision == 2
            assert durable_claim.state == "REJECTED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_g_real_recovery_checkpoint_releases_task_budget_and_keeps_claim_consumed(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r7-g-real-recovery.sqlite",
    )
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        identity = Identity(
            user_id=USER,
            session_id=SESSION,
            auth_type="api_key",
            scopes={"*"},
        )
        context = await store.prepare_resume_plan_context(
            plan,
            identity=identity,
        )
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r7g-real-recovery")
        )
        consumed = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            budget_before = await uow.agents.get_task_budget("task-r7f")
            assert execution.state == "RUNNING"
            assert execution.revision == 3
            assert execution.bound_connection_id == K2
            assert budget_before.active_executions == 1
            await uow.commit()

        budget_service = TaskBudgetService(
            factory,
            default_limits=_limits(),
            default_policy=TaskBudgetPolicy(version="r7-f"),
        )
        runtime = AgentRuntime(
            context_builder=object(),
            inference=object(),
            tool_execution=object(),
            execution_policy=object(),
            durable_store=store,
            task_budget_service=budget_service,
        )

        recovery_checkpoint_id, recovery_revision = (
            await runtime.recover_claimed_resume(
                context,
                plan=plan,
                consumed=consumed,
                error_message="RUNTIME_HANDOFF_FAILED: activation barrier failed",
            )
        )

        assert recovery_checkpoint_id == f"{EXECUTION}:checkpoint:4"
        assert recovery_revision == 4

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            checkpoint = await uow.agents.get_execution_checkpoint(
                recovery_checkpoint_id
            )
            pending = await uow.agents.list_checkpoint_pending_invocations(
                recovery_checkpoint_id
            )
            budget_after = await uow.agents.get_task_budget("task-r7f")
            release = await uow.agents.get_task_budget_reservation(
                "task-r7f",
                "RELEASE_EXECUTION",
                f"{EXECUTION}:4",
            )
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)

            assert execution.state == "WAITING"
            assert execution.wait_reason == "RECOVERY"
            assert execution.revision == 4
            assert execution.current_checkpoint_id == recovery_checkpoint_id
            assert execution.bound_client_id == CLIENT
            assert execution.bound_connection_id is None
            assert 0.0 <= execution.remaining_active_budget_seconds <= 20.0

            assert checkpoint is not None
            assert checkpoint.execution_revision == 4
            assert checkpoint.wait_reason == "RECOVERY"
            assert checkpoint.parent_checkpoint_id == CHECKPOINT
            assert checkpoint.origin_client_id == CLIENT
            assert checkpoint.origin_connection_id == K2
            assert len(pending) == 1
            assert pending[0].invocation_id == INVOCATION
            assert pending[0].tool_call_id == TOOL_CALL

            assert budget_after.active_executions == 0
            assert budget_after.used_executions == 1
            assert release is not None
            assert durable_claim.state == "CONSUMED"
            assert durable_claim.consumed_execution_revision == 3
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_g_resume_handoff_outcome_is_durable_idempotent_and_immutable(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r7-g-handoff.sqlite",
    )
    try:
        await _seed_non_task(sessions)
        plan = _plan()
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r7g-handoff")
        )
        consumed = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )

        accepted_payload = {
            "execution_id": EXECUTION,
            "checkpoint_id": CHECKPOINT,
            "resume_request_id": claim.resume_request_id,
            "claim_id": claim.claim_id,
            "accepted_revision": consumed.consumed_execution_revision,
            "state_at_accept": "RUNNING",
        }
        first = await store.record_resume_claim_handoff(
            claim.claim_id,
            status="ACCEPTED",
            payload=accepted_payload,
        )
        second = await store.record_resume_claim_handoff(
            claim.claim_id,
            status="ACCEPTED",
            payload=accepted_payload,
        )

        assert first.state.value == "CONSUMED"
        assert second.state.value == "CONSUMED"
        assert first.metadata["r7_g_handoff"] == {
            "status": "ACCEPTED",
            **accepted_payload,
        }
        assert second.metadata["r7_g_handoff"] == first.metadata["r7_g_handoff"]

        reloaded = await store.load_resume_claim_by_request_id(
            claim.resume_request_id
        )
        assert reloaded is not None
        assert reloaded.metadata["r7_g_handoff"] == first.metadata["r7_g_handoff"]

        with pytest.raises(ResumeClaimRejected) as raised:
            await store.record_resume_claim_handoff(
                claim.claim_id,
                status="FAILED",
                payload={
                    "execution_id": EXECUTION,
                    "checkpoint_id": CHECKPOINT,
                    "resume_request_id": claim.resume_request_id,
                    "claim_id": claim.claim_id,
                    "code": "RUNTIME_HANDOFF_FAILED",
                    "state": "WAITING",
                },
            )
        assert raised.value.code == "RESUME_REQUEST_CONFLICT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_activity_reconcile_race_with_r7_resume_finishes_task_running(
    tmp_path,
):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r8-f-r7-resume-activity-race.sqlite",
    )
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        budget_service = TaskBudgetService(
            factory,
            default_limits=_limits(),
            default_policy=TaskBudgetPolicy(version="r7-f"),
            max_conflict_retries=16,
        )

        sibling_branch_id = "branch-r8-f-terminal-sibling"
        sibling_execution_id = "exec-r8-f-terminal-sibling"
        async with factory() as uow:
            sibling_branch = await uow.agents.save_task_branch(
                {
                    "branch_id": sibling_branch_id,
                    "task_id": "task-r7f",
                    "parent_branch_id": branch_id,
                    "base_execution_id": EXECUTION,
                    "base_checkpoint_id": CHECKPOINT,
                    "current_execution_id": None,
                    "resolution_state": "OPEN",
                    "revision": 0,
                    "created_by": USER,
                    "reason": "R8_FORK",
                }
            )
            assert sibling_branch is not None
            sibling_execution = await uow.agents.save_execution(
                {
                    "id": sibling_execution_id,
                    "session_id": SESSION,
                    "agent_id": AGENT,
                    "task_id": "task-r7f",
                    "branch_id": sibling_branch_id,
                    "parent_execution_id": None,
                    "retry_of_execution_id": None,
                    "base_execution_id": EXECUTION,
                    "base_checkpoint_id": CHECKPOINT,
                    "correlation_id": "corr-r8-f-terminal-sibling",
                    "state": "FAILED",
                    "revision": 1,
                    "request": {},
                    "error": "terminal sibling",
                    "completed_at": datetime.now(timezone.utc),
                }
            )
            assert sibling_execution is not None
            sibling_branch = await uow.agents.compare_and_set_task_branch(
                sibling_branch_id,
                0,
                {"current_execution_id": sibling_execution_id},
            )
            assert sibling_branch is not None
            budget = await uow.agents.get_task_budget("task-r7f")
            updated_budget = await uow.agents.compare_and_set_task_budget(
                "task-r7f",
                int(budget.revision),
                {"active_branches": int(budget.active_branches) + 1},
            )
            assert updated_budget is not None
            await uow.commit()

        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r8-f-activity-race")
        )
        spec = ResumeClaimConsumeSpec(
            plan=plan,
            claim_id=claim.claim_id,
            resume_request_id=claim.resume_request_id,
            expected_claim_revision=claim.revision,
            now_utc=datetime.now(timezone.utc),
        )

        resume_outcome, reconcile_outcome = await asyncio.gather(
            store.consume_resume_claim(spec),
            budget_service.reconcile_multibranch_task_activity("task-r7f"),
            return_exceptions=True,
        )

        assert not isinstance(reconcile_outcome, BaseException)

        # Two serialized authority orders are valid:
        # A) resume epoch wins -> execution + Task become RUNNING;
        # B) reconciler epoch wins -> stale resume returns RESUME_CONFLICT,
        #    leaving WAITING/WAITING and the CREATED claim retryable.
        if isinstance(resume_outcome, BaseException):
            assert isinstance(resume_outcome, ResumeClaimRejected)
            assert resume_outcome.code == "RESUME_CONFLICT"
            async with factory() as uow:
                task = await uow.agents.get_task("task-r7f")
                execution = await uow.agents.get_execution(EXECUTION)
                budget = await uow.agents.get_task_budget("task-r7f")
                durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
                assert str(execution.state) == "WAITING"
                assert int(execution.revision) == 2
                assert str(task.status) == "WAITING"
                assert int(budget.active_executions) == 0
                assert str(durable_claim.state) == "CREATED"
                await uow.commit()

            # Same durable claim/plan remains live after the stale activity
            # epoch loss; a clean retry must converge to RUNNING.
            resume_outcome = await store.consume_resume_claim(spec)

        assert resume_outcome.consumed_execution_revision == 3
        async with factory() as uow:
            task = await uow.agents.get_task("task-r7f")
            execution = await uow.agents.get_execution(EXECUTION)
            budget = await uow.agents.get_task_budget("task-r7f")
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            await uow.commit()

        assert str(execution.state) == "RUNNING"
        assert int(execution.revision) == 3
        assert str(task.status) == "RUNNING"
        assert list(task.wait_reasons or []) == []
        assert int(budget.active_executions) == 1
        assert int(budget.active_branches) == 2
        assert str(durable_claim.state) == "CONSUMED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_task_scoped_wait_expiry_rederives_sibling_wait_reason(tmp_path):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r8-f-task-wait-expiry-activity.sqlite",
    )
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        budget_service = TaskBudgetService(
            factory,
            default_limits=_limits(),
            default_policy=TaskBudgetPolicy(version="r7-f"),
            max_conflict_retries=16,
        )
        sibling_branch_id = "branch-r8-f-wait-expiry-sibling"
        sibling_execution_id = "exec-r8-f-wait-expiry-sibling"
        now = datetime.now(timezone.utc)
        expired_at = now - timedelta(seconds=1)

        async with factory() as uow:
            sibling_branch = await uow.agents.save_task_branch(
                {
                    "branch_id": sibling_branch_id,
                    "task_id": "task-r7f",
                    "parent_branch_id": branch_id,
                    "base_execution_id": EXECUTION,
                    "base_checkpoint_id": CHECKPOINT,
                    "current_execution_id": None,
                    "resolution_state": "OPEN",
                    "revision": 0,
                    "created_by": USER,
                    "reason": "R8_FORK",
                }
            )
            assert sibling_branch is not None
            sibling_execution = await uow.agents.save_execution(
                {
                    "id": sibling_execution_id,
                    "session_id": SESSION,
                    "agent_id": AGENT,
                    "task_id": "task-r7f",
                    "branch_id": sibling_branch_id,
                    "parent_execution_id": None,
                    "retry_of_execution_id": None,
                    "base_execution_id": EXECUTION,
                    "base_checkpoint_id": CHECKPOINT,
                    "correlation_id": "corr-r8-f-wait-expiry-sibling",
                    "state": "WAITING",
                    "wait_reason": "RESOURCE",
                    "revision": 2,
                    "current_checkpoint_id": None,
                    "remaining_active_budget_seconds": 15.0,
                    "request": {},
                }
            )
            assert sibling_execution is not None
            sibling_branch = await uow.agents.compare_and_set_task_branch(
                sibling_branch_id,
                0,
                {"current_execution_id": sibling_execution_id},
            )
            assert sibling_branch is not None

            execution = await uow.agents.get_execution(EXECUTION)
            execution.wait_expires_at = expired_at
            checkpoint = await uow.agents.get_execution_checkpoint(CHECKPOINT)
            checkpoint.wait_expires_at = expired_at

            budget = await uow.agents.get_task_budget("task-r7f")
            updated_budget = await uow.agents.compare_and_set_task_budget(
                "task-r7f",
                int(budget.revision),
                {"active_branches": int(budget.active_branches) + 1},
            )
            assert updated_budget is not None
            await uow.commit()

        task_waiting = await budget_service.reconcile_multibranch_task_activity(
            "task-r7f"
        )
        assert str(task_waiting.status) == "WAITING"
        assert list(task_waiting.wait_reasons or []) == [
            "CONNECTION",
            "RESOURCE",
        ]

        plan = _plan(
            task_id="task-r7f",
            branch_id=branch_id,
            wait_expires_at=expired_at,
        )
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r8-f-task-wait-expired")
        )
        with pytest.raises(ResumeClaimRejected) as raised:
            await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=now,
                )
            )
        assert raised.value.code == "WAIT_EXPIRED"

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            task = await uow.agents.get_task("task-r7f")
            budget = await uow.agents.get_task_budget("task-r7f")
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            assert execution.state == "TIMEOUT"
            assert execution.revision == 3
            assert str(task.status) == "WAITING"
            assert list(task.wait_reasons or []) == ["RESOURCE"]
            assert int(budget.active_executions) == 0
            assert int(budget.active_branches) == 2
            assert durable_claim.state == "REJECTED"
            assert durable_claim.rejection_code == "WAIT_EXPIRED"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_transient_resume_activity_epoch_conflict_retries_internally(
    tmp_path,
    monkeypatch,
):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r8-f-resume-activity-internal-retry.sqlite",
    )
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r8-f-activity-internal-retry")
        )

        original = AgentRepository.compare_and_set_task
        losses = 0

        async def lose_first_task_epoch(self, task_id, revision, values):
            nonlocal losses
            if task_id == "task-r7f" and losses == 0:
                losses += 1
                return None
            return await original(self, task_id, revision, values)

        monkeypatch.setattr(
            AgentRepository,
            "compare_and_set_task",
            lose_first_task_epoch,
        )

        result = await store.consume_resume_claim(
            ResumeClaimConsumeSpec(
                plan=plan,
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                expected_claim_revision=claim.revision,
                now_utc=datetime.now(timezone.utc),
            )
        )

        assert losses == 1
        assert result.claim_id == claim.claim_id
        assert result.resume_request_id == claim.resume_request_id
        assert result.consumed_execution_revision == 3

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            task = await uow.agents.get_task("task-r7f")
            assert str(execution.state) == "RUNNING"
            assert int(execution.revision) == 3
            assert str(durable_claim.state) == "CONSUMED"
            assert str(task.status) == "RUNNING"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_exhausted_resume_activity_epoch_conflict_is_retryable_deferred(
    tmp_path,
    monkeypatch,
):
    engine, sessions, factory, store = await _setup(
        tmp_path,
        "r8-f-resume-activity-deferred.sqlite",
    )
    try:
        branch_id = await _seed_task_waiting(sessions, factory)
        plan = _plan(task_id="task-r7f", branch_id=branch_id)
        claim = await store.get_or_create_resume_claim(
            _intent(plan, "rr-r8-f-activity-deferred")
        )

        async with factory() as uow:
            task_before = await uow.agents.get_task("task-r7f")
            task_revision_before = int(task_before.revision)
            await uow.commit()

        async def always_lose_task_epoch(self, task_id, revision, values):
            if task_id == "task-r7f":
                return None
            raise AssertionError("unexpected Task CAS")

        monkeypatch.setattr(
            AgentRepository,
            "compare_and_set_task",
            always_lose_task_epoch,
        )

        with pytest.raises(ResumeClaimDeferred) as raised:
            await store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=datetime.now(timezone.utc),
                )
            )

        assert raised.value.code == "RESUME_CONFLICT"
        assert raised.value.retryable is True

        async with factory() as uow:
            execution = await uow.agents.get_execution(EXECUTION)
            durable_claim = await uow.agents.get_resume_claim(claim.claim_id)
            budget = await uow.agents.get_task_budget("task-r7f")
            task_after = await uow.agents.get_task("task-r7f")
            assert str(execution.state) == "WAITING"
            assert int(execution.revision) == 2
            assert str(durable_claim.state) == "CREATED"
            assert int(budget.active_executions) == 0
            assert int(task_after.revision) == task_revision_before
            await uow.commit()
    finally:
        await engine.dispose()
