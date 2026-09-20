from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import (
    AgentExecutionLimits,
    AgentExecutionWaitReason,
)
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionContext,
    AgentExecutionResult,
    AgentLoopState,
)
from se.src.runtimes.agent.persistence import ExecutionConflictError
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.wait_policy import ConfiguredExecutionWaitPolicy


class _FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = datetime(
            2026, 9, 20, 7, 0, tzinfo=timezone.utc
        )

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_utc(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


class _MemoryStore:
    def __init__(self, record=None) -> None:
        self.record = record

    async def load_execution(self, execution_id):
        if self.record is None or self.record.id != execution_id:
            return None
        return self.record

    async def save_execution(self, values):
        if self.record is not None:
            raise ExecutionConflictError("duplicate")
        self.record = SimpleNamespace(**values)
        return self.record

    async def compare_and_set_execution(
        self,
        execution_id,
        expected_revision,
        values,
    ):
        if (
            self.record is None
            or self.record.id != execution_id
            or self.record.revision != expected_revision
        ):
            raise ExecutionConflictError("stale")
        for key, value in values.items():
            setattr(self.record, key, value)
        self.record.revision += 1
        return self.record


def _context(
    clock: _FakeClock,
    *,
    execution_id: str = "exec-r4-b1",
    budget: float = 60.0,
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=budget),
        clock=clock,
    )


def _runtime(
    store,
    *,
    ttl_by_reason=None,
) -> AgentRuntime:
    return AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
        wait_policy=ConfiguredExecutionWaitPolicy(ttl_by_reason),
    )


def _waiting_result(execution_id="exec-r4-b1"):
    return AgentExecutionResult(
        execution_id=execution_id,
        agent_id="agent-r4",
        state=AgentLoopState.WAITING,
        wait_reason=AgentExecutionWaitReason.CONNECTION,
        error_code="WAITING_FOR_CONNECTION",
        error_message="waiting",
    )


def test_r4_b1_wait_policy_is_configurable_without_inventing_defaults():
    clock = _FakeClock()
    context = _context(clock)

    default_policy = ConfiguredExecutionWaitPolicy()
    assert (
        default_policy.wait_ttl_seconds(
            reason=AgentExecutionWaitReason.CONNECTION,
            context=context,
        )
        is None
    )

    policy = ConfiguredExecutionWaitPolicy(
        {
            AgentExecutionWaitReason.CONNECTION: 3600,
            "HUMAN_APPROVAL": None,
        }
    )
    assert policy.wait_ttl_seconds(
        reason=AgentExecutionWaitReason.CONNECTION,
        context=context,
    ) == 3600.0
    assert policy.wait_ttl_seconds(
        reason=AgentExecutionWaitReason.HUMAN_APPROVAL,
        context=context,
    ) is None

    with pytest.raises(ValueError, match="finite positive"):
        ConfiguredExecutionWaitPolicy(
            {AgentExecutionWaitReason.CONNECTION: 0}
        )


@pytest.mark.asyncio
async def test_r4_b1_new_durable_execution_persists_initial_active_budget():
    clock = _FakeClock()
    context = _context(clock)
    store = _MemoryStore()
    runtime = _runtime(store)

    revision = await runtime._begin_durable_execution(context)

    assert revision == 1
    assert store.record.state == "RUNNING"
    assert store.record.revision == 1
    assert store.record.remaining_active_budget_seconds == 60.0
    assert store.record.wait_expires_at is None


@pytest.mark.asyncio
async def test_r4_b1_waiting_freezes_budget_and_persists_ttl_in_same_cas():
    clock = _FakeClock()
    context = _context(clock)
    store = _MemoryStore()
    runtime = _runtime(
        store,
        ttl_by_reason={AgentExecutionWaitReason.CONNECTION: 3600},
    )
    revision = await runtime._begin_durable_execution(context)

    clock.advance(20)
    expected_expiry = clock.now_utc() + timedelta(seconds=3600)
    result = await runtime._finish_durable_execution(
        context,
        _waiting_result(),
        revision,
    )

    assert result.state is AgentLoopState.WAITING
    assert store.record.state == "WAITING"
    assert store.record.wait_reason == "CONNECTION"
    assert store.record.revision == 2
    assert store.record.remaining_active_budget_seconds == 40.0
    assert store.record.wait_expires_at == expected_expiry
    assert store.record.completed_at is None

    assert context.active_budget_running is False
    assert context.remaining_active_budget_seconds == 40.0
    assert context.wait_expires_at == expected_expiry

    clock.advance(600)
    assert context.remaining_active_seconds == 40.0


@pytest.mark.asyncio
async def test_r4_b1_missing_ttl_policy_keeps_wait_expiry_null():
    clock = _FakeClock()
    context = _context(clock)
    store = _MemoryStore()
    runtime = _runtime(store)
    revision = await runtime._begin_durable_execution(context)

    clock.advance(10)
    await runtime._finish_durable_execution(
        context,
        _waiting_result(),
        revision,
    )

    assert store.record.state == "WAITING"
    assert store.record.remaining_active_budget_seconds == 50.0
    assert store.record.wait_expires_at is None
    assert context.wait_expires_at is None


@pytest.mark.asyncio
async def test_r4_b1_exhausted_budget_cannot_become_resumable_waiting():
    clock = _FakeClock()
    context = AgentExecutionContext.create(
        execution_id="exec-r4-b1",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=60),
        remaining_active_budget_seconds=0.0,
        clock=clock,
    )
    store = _MemoryStore()
    runtime = _runtime(
        store,
        ttl_by_reason={AgentExecutionWaitReason.CONNECTION: 3600},
    )
    revision = await runtime._begin_durable_execution(context)

    result = await runtime._finish_durable_execution(
        context,
        _waiting_result(),
        revision,
    )

    assert result.state is AgentLoopState.TIMEOUT
    assert result.wait_reason is None
    assert result.error_code == "AGENT_EXECUTION_TIMEOUT"
    assert result.continuation_state is None
    assert result.checkpoint_id is None
    assert store.record.state == "TIMEOUT"
    assert store.record.wait_reason is None
    assert store.record.wait_expires_at is None
    assert store.record.remaining_active_budget_seconds == 0.0
    assert store.record.completed_at == clock.now_utc()


@pytest.mark.asyncio
async def test_r4_b1_running_claim_clears_stale_wait_expiry_only():
    clock = _FakeClock()
    stale_expiry = clock.now_utc() + timedelta(hours=1)
    store = _MemoryStore(
        SimpleNamespace(
            id="exec-r4-b1-resume",
            state="WAITING",
            wait_reason="CONNECTION",
            revision=7,
            wait_expires_at=stale_expiry,
        )
    )
    context = _context(
        clock,
        execution_id="exec-r4-b1-resume",
    )
    context.resume_revision = 7
    runtime = _runtime(store)

    revision = await runtime._begin_durable_execution(context)

    assert revision == 8
    assert store.record.state == "RUNNING"
    assert store.record.wait_reason is None
    assert store.record.wait_expires_at is None
    assert context.wait_expires_at is None
