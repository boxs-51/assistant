from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import AgentExecutionContext
from se.src.runtimes.agent.persistence import ExecutionConflictError
from se.src.runtimes.agent.runtime import (
    AgentRuntime,
    ExecutionResumeBudgetError,
    ExecutionWaitExpiredError,
)
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.transport.gateway.api.v1.events_router import _resume_execution


class _FakeClock:
    def __init__(
        self,
        *,
        monotonic: float = 100.0,
        wall: datetime | None = None,
    ) -> None:
        self.monotonic_value = monotonic
        self.wall_value = wall or datetime(
            2026, 9, 20, 8, 0, tzinfo=timezone.utc
        )

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_utc(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


class _MemoryStore:
    def __init__(self, record) -> None:
        self.record = record
        self._lock = asyncio.Lock()

    async def load_execution(self, execution_id):
        await asyncio.sleep(0)
        if self.record.id != execution_id:
            return None
        return SimpleNamespace(**vars(self.record))

    async def save_execution(self, values):
        raise AssertionError("resume test must not create a new execution")

    async def compare_and_set_execution(
        self,
        execution_id,
        expected_revision,
        values,
    ):
        async with self._lock:
            if (
                self.record.id != execution_id
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
    execution_id: str = "exec-r4-b2",
    revision: int = 7,
    remaining: float | None = 40.0,
    expiry: datetime | None = None,
) -> AgentExecutionContext:
    context = AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=60),
        remaining_active_budget_seconds=remaining,
        wait_expires_at=expiry,
        clock=clock,
        activate_budget=False,
    )
    context.resume_revision = revision
    return context


def _record(
    clock: _FakeClock,
    *,
    execution_id: str = "exec-r4-b2",
    revision: int = 7,
    remaining: float | None = 40.0,
    expiry: datetime | None = None,
):
    del clock
    return SimpleNamespace(
        id=execution_id,
        state="WAITING",
        wait_reason="CONNECTION",
        revision=revision,
        remaining_active_budget_seconds=remaining,
        wait_expires_at=expiry,
    )


def _runtime(store) -> AgentRuntime:
    return AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )


@pytest.mark.asyncio
async def test_r4_b2_resume_restores_exact_persisted_budget_after_claim():
    clock = _FakeClock(monotonic=500.0)
    expiry = clock.now_utc() + timedelta(hours=1)
    store = _MemoryStore(_record(clock, remaining=40.0, expiry=expiry))
    context = _context(clock, remaining=40.0, expiry=expiry)
    runtime = _runtime(store)

    assert context.active_budget_running is False
    clock.advance(300)
    assert context.remaining_active_seconds == 40.0

    revision = await runtime._begin_durable_execution(context)

    assert revision == 8
    assert store.record.state == "RUNNING"
    assert store.record.wait_reason is None
    assert store.record.wait_expires_at is None
    assert context.active_budget_running is True
    assert context.remaining_seconds == 40.0
    assert context.active_deadline_monotonic == clock.monotonic() + 40.0


@pytest.mark.asyncio
async def test_r4_b2_legacy_null_budget_fails_closed_without_mutation():
    clock = _FakeClock()
    store = _MemoryStore(_record(clock, remaining=None))
    context = _context(clock, remaining=None)
    runtime = _runtime(store)

    with pytest.raises(
        ExecutionResumeBudgetError,
        match="UNKNOWN_ACTIVE_BUDGET",
    ):
        await runtime._begin_durable_execution(context)

    assert store.record.state == "WAITING"
    assert store.record.revision == 7
    assert store.record.wait_reason == "CONNECTION"


@pytest.mark.asyncio
async def test_r4_b2_wait_ttl_allows_before_expiry_and_rejects_at_expiry():
    before_clock = _FakeClock()
    expiry = before_clock.now_utc() + timedelta(seconds=10)
    before_store = _MemoryStore(_record(before_clock, expiry=expiry))
    before_context = _context(before_clock, expiry=expiry)
    before_clock.advance(9.999)

    revision = await _runtime(before_store)._begin_durable_execution(
        before_context
    )
    assert revision == 8
    assert before_store.record.state == "RUNNING"

    at_clock = _FakeClock(wall=expiry)
    at_store = _MemoryStore(_record(at_clock, expiry=expiry))
    at_context = _context(at_clock, expiry=expiry)

    with pytest.raises(
        ExecutionWaitExpiredError,
        match="WAIT_TTL_EXPIRED",
    ):
        await _runtime(at_store)._begin_durable_execution(at_context)

    assert at_store.record.state == "TIMEOUT"
    assert at_store.record.revision == 8
    assert at_store.record.wait_reason is None
    assert at_store.record.wait_expires_at is None
    assert at_store.record.error == "WAIT_TTL_EXPIRED"


@pytest.mark.asyncio
async def test_r4_b2_two_valid_resume_claims_have_one_winner():
    clock = _FakeClock()
    expiry = clock.now_utc() + timedelta(hours=1)
    store = _MemoryStore(_record(clock, expiry=expiry))
    runtime = _runtime(store)
    first = _context(clock, expiry=expiry)
    second = _context(clock, expiry=expiry)

    outcomes = await asyncio.gather(
        runtime._begin_durable_execution(first),
        runtime._begin_durable_execution(second),
        return_exceptions=True,
    )

    assert sum(isinstance(item, int) for item in outcomes) == 1
    assert sum(
        isinstance(item, ExecutionConflictError)
        for item in outcomes
    ) == 1
    assert store.record.state == "RUNNING"
    assert store.record.revision == 8


class _Socket:
    def __init__(self, order):
        self.messages = []
        self.order = order

    async def send_json(self, payload):
        self.order.append("ack")
        self.messages.append(payload)


class _ResumeService:
    def __init__(self, order):
        self.order = order
        self.merged = False

    async def ensure_loaded(self, execution_id):
        return SimpleNamespace(
            checkpoint_id="checkpoint-1",
            pending_capability_id="desktop.echo",
        )

    async def reconnect(self, **kwargs):
        self.order.append("reconnect")
        return SimpleNamespace(branch_id="branch-1")

    async def confirm_merge(self, **kwargs):
        self.order.append("merge")
        self.merged = True
        return SimpleNamespace(checkpoint_id="checkpoint-running")


class _RuntimeStub:
    def __init__(self, order, *, fail_claim=False):
        self.order = order
        self.fail_claim = fail_claim
        self.execute_revision = None

    async def claim_resume(self, context):
        self.order.append("claim")
        if self.fail_claim:
            raise ExecutionConflictError("stale")
        context.restore_active_budget(40.0)
        return 8

    async def execute(self, context, *, durable_revision=None):
        self.order.append("execute")
        self.execute_revision = durable_revision
        return SimpleNamespace(state=SimpleNamespace(value="COMPLETED"))


def _ws_container(order, context, service, runtime):
    class _Durable:
        async def resume_execution(self, execution_id, *, identity=None):
            order.append("rehydrate")
            return context

    snapshot = SimpleNamespace(
        is_usable=True,
        user_id="user-r4",
        metadata={"client_id": "client-r4"},
    )
    return SimpleNamespace(
        connection_runtime=SimpleNamespace(
            registry=SimpleNamespace(get=lambda _: snapshot)
        ),
        continuation_service=service,
        capability_runtime=SimpleNamespace(
            catalog=SimpleNamespace(
                list_implementations_for_connection=lambda _: [
                    SimpleNamespace(
                        capability_id="desktop.echo",
                        state=SimpleNamespace(value="ENABLED"),
                    )
                ]
            )
        ),
        agent_durable_store=_Durable(),
        agent_registry=SimpleNamespace(
            get=lambda _: SimpleNamespace(name="agent-r4")
        ),
        agent_runtime=runtime,
    )


def _resume_envelope():
    return RealtimeEnvelope(
        type="execution.resume",
        message_id="resume-request-1",
        connection_id="conn-new",
        execution_id="exec-r4-b2",
        payload={
            "execution_id": "exec-r4-b2",
            "checkpoint_id": "checkpoint-1",
        },
    )


@pytest.mark.asyncio
async def test_r4_b2_websocket_claims_before_merge_ack_and_reuses_revision():
    order = []
    context = _context(_FakeClock())
    socket = _Socket(order)
    service = _ResumeService(order)
    runtime = _RuntimeStub(order)
    container = _ws_container(order, context, service, runtime)

    await _resume_execution(
        socket,
        Identity(user_id="user-r4", auth_type="jwt"),
        container,
        "conn-new",
        _resume_envelope(),
    )
    await asyncio.sleep(0)

    assert order == [
        "rehydrate",
        "reconnect",
        "claim",
        "merge",
        "ack",
        "execute",
    ]
    assert socket.messages[0]["type"] == "execution.resume.accepted"
    assert runtime.execute_revision == 8


@pytest.mark.asyncio
async def test_r4_b2_websocket_race_loser_never_merges_or_receives_ack():
    order = []
    context = _context(_FakeClock())
    socket = _Socket(order)
    service = _ResumeService(order)
    runtime = _RuntimeStub(order, fail_claim=True)
    container = _ws_container(order, context, service, runtime)

    with pytest.raises(ExecutionConflictError, match="stale"):
        await _resume_execution(
            socket,
            Identity(user_id="user-r4", auth_type="jwt"),
            container,
            "conn-new",
            _resume_envelope(),
        )

    assert order == ["rehydrate", "reconnect", "claim"]
    assert service.merged is False
    assert socket.messages == []
