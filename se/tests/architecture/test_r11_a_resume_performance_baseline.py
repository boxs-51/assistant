from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeTriggerType,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityImplementationState,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)


USER = "user-r11-a-resume"
CLIENT = "client-r11-a-resume"
OLD_CONNECTION = "conn-r11-a-old"
NEW_CONNECTION = "conn-r11-a-new"
CAPABILITY = "tool.r11.resume"
VERSION = "1"
FINGERPRINT = "a" * 64


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
                await self.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


class _InvocationStore:
    def __init__(self, invocations: dict[str, CapabilityInvocation]):
        self._invocations = invocations

    async def get(self, invocation_id: str):
        invocation = self._invocations.get(invocation_id)
        return invocation.model_copy(deep=True) if invocation is not None else None


class _ConnectionRegistry:
    def get(self, connection_id: str):
        assert connection_id == NEW_CONNECTION
        return SimpleNamespace(
            is_usable=True,
            user_id=USER,
            metadata={"client_id": CLIENT},
        )


class _Catalog:
    def list_implementations_for_connection(self, connection_id: str):
        assert connection_id == NEW_CONNECTION
        return [
            SimpleNamespace(
                capability_id=CAPABILITY,
                version=VERSION,
                state=CapabilityImplementationState.ENABLED,
            )
        ]


class _CapabilityRuntime:
    def __init__(self, invocations: dict[str, CapabilityInvocation]):
        self.invocation_lifecycle = SimpleNamespace(
            store=_InvocationStore(invocations)
        )
        self.connection_registry = _ConnectionRegistry()
        self.catalog = _Catalog()

    async def reconcile_remote_invocation(self, *_args, **_kwargs):
        raise AssertionError(
            "NOT_DISPATCHED resume baseline must not reconcile remote state"
        )


def _message(index: int) -> dict[str, object]:
    return {
        "role": "user",
        "content": f"{index:06d}:" + ("r" * 128),
        "tool_calls": [],
        "name": None,
        "tool_call_id": None,
        "metadata": {},
    }


def _percentiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    assert ordered

    def at(percent: int) -> int:
        index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
        return ordered[index]

    return {"p50_ns": at(50), "p95_ns": at(95), "p99_ns": at(99)}


def _invocation(
    *,
    invocation_id: str,
    execution_id: str,
    tool_call_id: str,
    session_id: str,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id=CAPABILITY,
        capability_version=VERSION,
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint=FINGERPRINT,
        owner_user_id=USER,
        origin_client_id=CLIENT,
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        session_id=session_id,
        execution_id=execution_id,
        tool_call_id=tool_call_id,
        connection_id=OLD_CONNECTION,
        revision=5,
    )


async def _setup(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r11-a-resume.sqlite').as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))
    return engine, sessions, store


async def _seed_resume_samples(
    sessions,
    *,
    samples: int,
    message_count: int,
) -> dict[str, CapabilityInvocation]:
    invocations: dict[str, CapabilityInvocation] = {}
    async with sessions() as session:
        repo = AgentRepository(session)
        for index in range(samples):
            execution_id = f"exec-r11-a-resume-{index}"
            session_id = f"session-r11-a-resume-{index}"
            checkpoint_id = f"cp-r11-a-resume-{index}"
            invocation_id = f"inv-r11-a-resume-{index}"
            tool_call_id = f"call-r11-a-resume-{index}"

            session.add(
                AgentExecutionRecord(
                    id=execution_id,
                    session_id=session_id,
                    agent_id="agent-r11-a",
                    correlation_id=f"corr-r11-a-resume-{index}",
                    state="WAITING",
                    wait_reason="CONNECTION",
                    revision=2,
                    current_checkpoint_id=checkpoint_id,
                    bound_client_id=CLIENT,
                    bound_connection_id=None,
                    remaining_active_budget_seconds=20.0,
                    wait_expires_at=None,
                    request={"prompt": "resume baseline"},
                    context_state={
                        "request_id": f"request-r11-a-resume-{index}",
                        "trace_id": f"trace-r11-a-resume-{index}",
                    },
                )
            )
            session.add(
                AgentIterationRecord(
                    id=f"iter-r11-a-resume-{index}",
                    execution_id=execution_id,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[tool_call_id],
                )
            )
            session.add(
                AgentExecutionCheckpointRecord(
                    checkpoint_id=checkpoint_id,
                    execution_id=execution_id,
                    execution_revision=2,
                    session_id=session_id,
                    iteration=1,
                    wait_reason="CONNECTION",
                    remaining_active_budget_seconds=20.0,
                    wait_expires_at=None,
                    origin_client_id=CLIENT,
                    origin_connection_id=OLD_CONNECTION,
                    transcript_snapshot=[
                        _message(message_index)
                        for message_index in range(message_count)
                    ],
                    metadata_json={
                        "request_id": f"request-r11-a-resume-{index}",
                        "trace_id": f"trace-r11-a-resume-{index}",
                    },
                )
            )
            session.add(
                CapabilityInvocationRecord(
                    invocation_id=invocation_id,
                    capability_id=CAPABILITY,
                    capability_version=VERSION,
                    kind="TOOL",
                    execution_mode="ONE_SHOT",
                    idempotency="IDEMPOTENT",
                    request_fingerprint=FINGERPRINT,
                    owner_user_id=USER,
                    origin_client_id=CLIENT,
                    remote_outcome_state="NOT_DISPATCHED",
                    implementation_id=f"{OLD_CONNECTION}:{CAPABILITY}",
                    driver_kind="REMOTE_CLIENT",
                    state="WAITING",
                    wait_reason="CONNECTION",
                    session_id=session_id,
                    execution_id=execution_id,
                    tool_call_id=tool_call_id,
                    connection_id=OLD_CONNECTION,
                    attempt=1,
                    max_attempts=1,
                    arguments={},
                    revision=5,
                )
            )
            await session.flush()
            await repo.save_checkpoint_pending_invocation(
                {
                    "checkpoint_id": checkpoint_id,
                    "ordinal": 0,
                    "invocation_id": invocation_id,
                    "invocation_revision": 5,
                    "tool_call_id": tool_call_id,
                    "capability_id": CAPABILITY,
                    "capability_version": VERSION,
                    "request_fingerprint": FINGERPRINT,
                    "idempotency": "IDEMPOTENT",
                    "observed_remote_outcome_state": "NOT_DISPATCHED",
                    "origin_client_id": CLIENT,
                    "origin_connection_id": OLD_CONNECTION,
                }
            )
            invocations[invocation_id] = _invocation(
                invocation_id=invocation_id,
                execution_id=execution_id,
                tool_call_id=tool_call_id,
                session_id=session_id,
            )
        await session.commit()
    return invocations


async def _measure_resume_distribution(
    tmp_path,
    *,
    samples: int = 20,
    message_count: int = 100,
) -> dict[str, object]:
    engine, sessions, store = await _setup(tmp_path)
    try:
        invocations = await _seed_resume_samples(
            sessions,
            samples=samples,
            message_count=message_count,
        )
        planner = AgentResumePlanningService(
            store,
            _CapabilityRuntime(invocations),
            now_utc=lambda: datetime.now(timezone.utc),
        )

        planning_ns: list[int] = []
        claim_consume_ns: list[int] = []
        end_to_end_ns: list[int] = []

        for index in range(samples):
            execution_id = f"exec-r11-a-resume-{index}"
            checkpoint_id = f"cp-r11-a-resume-{index}"

            total_started = time.perf_counter_ns()
            planning_started = time.perf_counter_ns()
            plan = await planner.build_resume_plan(
                execution_id,
                checkpoint_id,
                target_user_id=USER,
                target_client_id=CLIENT,
                target_connection_id=NEW_CONNECTION,
            )
            planning_ns.append(time.perf_counter_ns() - planning_started)

            claim_started = time.perf_counter_ns()
            claim = await store.get_or_create_resume_claim(
                ResumeClaimIntent(
                    resume_request_id=f"resume-request-r11-a-{index}",
                    execution_id=plan.execution_id,
                    checkpoint_id=plan.checkpoint_id,
                    expected_execution_revision=plan.expected_execution_revision,
                    plan_fingerprint=plan.plan_fingerprint,
                    user_id=USER,
                    client_id=CLIENT,
                    connection_id=NEW_CONNECTION,
                    wait_reason="CONNECTION",
                    trigger_type=ResumeTriggerType.CLIENT_RECONNECT,
                    claim_expires_at=(
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ),
                )
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
            claim_consume_ns.append(time.perf_counter_ns() - claim_started)
            end_to_end_ns.append(time.perf_counter_ns() - total_started)

            assert consumed.already_consumed is False
            assert consumed.consumed_execution_revision == 3
            assert len(plan.transcript_snapshot) == message_count

        return {
            "samples": samples,
            "message_count": message_count,
            "planning": _percentiles(planning_ns),
            "claim_consume": _percentiles(claim_consume_ns),
            "end_to_end": _percentiles(end_to_end_ns),
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_a_resume_materialize_claim_percentile_red_probe(tmp_path):
    report = await _measure_resume_distribution(
        tmp_path,
        samples=20,
        message_count=100,
    )

    assert report["samples"] == 20
    assert report["message_count"] == 100
    for key in ("planning", "claim_consume", "end_to_end"):
        sample = report[key]
        assert 0 < sample["p50_ns"] <= sample["p95_ns"] <= sample["p99_ns"]

    # Exact p50/p95/p99 values are retained on Issue #31 / CI #953.
    # Runtime timing remains measurement evidence rather than a hard threshold.
    assert report["end_to_end"]["p50_ns"] > 0
