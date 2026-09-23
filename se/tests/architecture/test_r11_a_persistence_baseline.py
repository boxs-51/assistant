from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import time

import pytest
from sqlalchemy import event
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
from se.src.runtimes.agent.persistence import DurableAgentStore


@dataclass
class _SqlMetrics:
    statements: Counter[str] = field(default_factory=Counter)
    flushes: int = 0
    commits: int = 0
    rollbacks: int = 0
    begins: int = 0

    def reset(self) -> None:
        self.statements.clear()
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0

    @property
    def total_sql(self) -> int:
        return sum(self.statements.values())

    def snapshot(self) -> dict[str, int]:
        return {
            "sql": self.total_sql,
            "select": self.statements["SELECT"],
            "insert": self.statements["INSERT"],
            "update": self.statements["UPDATE"],
            "delete": self.statements["DELETE"],
            "flushes": self.flushes,
            "commits": self.commits,
            "rollbacks": self.rollbacks,
            "begins": self.begins,
        }


class _MeasuredUow:
    def __init__(self, sessions, metrics: _SqlMetrics):
        self._sessions = sessions
        self._metrics = metrics
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        event.listen(self.session.sync_session, "before_flush", self._before_flush)
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(
            self.session
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    def _before_flush(self, *_args) -> None:
        self._metrics.flushes += 1

    async def commit(self):
        self._metrics.commits += 1
        await self.session.commit()

    async def rollback(self):
        self._metrics.rollbacks += 1
        await self.session.rollback()


def _install_sql_metrics(engine, metrics: _SqlMetrics) -> None:
    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        token = statement.lstrip().split(None, 1)[0].upper()
        metrics.statements[token] += 1

    def on_begin(_conn):
        metrics.begins += 1

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine.sync_engine, "begin", on_begin)


def _message(index: int, *, content_size: int = 128) -> dict[str, object]:
    return {
        "role": "user",
        "content": f"{index:06d}:" + ("x" * content_size),
        "tool_calls": [],
        "name": None,
        "tool_call_id": None,
        "metadata": {},
    }


def _json_bytes(value) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _snapshot_growth(iterations: int) -> dict[str, int | float]:
    transcript: list[dict[str, object]] = []
    cumulative = 0
    for index in range(iterations):
        transcript.append(_message(index))
        cumulative += _json_bytes(transcript)
    final_bytes = _json_bytes(transcript)
    return {
        "iterations": iterations,
        "final_transcript_bytes": final_bytes,
        "cumulative_snapshot_bytes": cumulative,
        "copy_amplification": round(cumulative / final_bytes, 3),
    }


async def _new_database(path, metrics: _SqlMetrics):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    _install_sql_metrics(engine, metrics)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _seed_execution_and_invocations(
    sessions,
    *,
    execution_id: str,
    pending_count: int,
) -> list[dict[str, object]]:
    pending: list[dict[str, object]] = []
    async with sessions() as session:
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id="agent-r11-a",
                correlation_id=f"corr-{execution_id}",
                state="RUNNING",
                revision=1,
                request={},
            )
        )
        for ordinal in range(pending_count):
            invocation_id = f"{execution_id}-inv-{ordinal}"
            tool_call_id = f"{execution_id}-call-{ordinal}"
            session.add(
                CapabilityInvocationRecord(
                    invocation_id=invocation_id,
                    capability_id="tool.remote",
                    capability_version="1",
                    kind="TOOL",
                    execution_mode="ONE_SHOT",
                    idempotency="DEDUPLICATED",
                    request_fingerprint=f"fp-{ordinal}",
                    owner_user_id="user-r11-a",
                    origin_client_id="client-r11-a",
                    remote_outcome_state="OUTCOME_UNKNOWN",
                    state="RUNNING",
                    session_id=f"session-{execution_id}",
                    execution_id=execution_id,
                    tool_call_id=tool_call_id,
                    connection_id="conn-r11-a",
                    arguments={},
                    revision=1,
                )
            )
            pending.append(
                {
                    "ordinal": ordinal,
                    "invocation_id": invocation_id,
                    "tool_call_id": tool_call_id,
                    "capability_id": "tool.remote",
                }
            )
        await session.commit()
    return pending


async def _measure_waiting_commit(tmp_path, pending_count: int) -> dict[str, int]:
    metrics = _SqlMetrics()
    engine, sessions = await _new_database(
        tmp_path / f"r11-a-wait-{pending_count}.sqlite",
        metrics,
    )
    execution_id = f"exec-r11-a-{pending_count}"
    try:
        pending = await _seed_execution_and_invocations(
            sessions,
            execution_id=execution_id,
            pending_count=pending_count,
        )
        metrics.reset()
        store = DurableAgentStore(lambda: _MeasuredUow(sessions, metrics))
        await store.commit_waiting_checkpoint(
            execution_id,
            1,
            {
                "state": "WAITING",
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
            },
            checkpoint_values={
                "checkpoint_id": f"{execution_id}:checkpoint:2",
                "execution_id": execution_id,
                "execution_revision": 2,
                "session_id": f"session-{execution_id}",
                "task_id": None,
                "branch_id": None,
                "iteration": 1,
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
                "origin_client_id": "client-r11-a",
                "origin_connection_id": "conn-r11-a",
                "transcript_snapshot": [_message(0)],
                "metadata_json": {"phase": "r11-a"},
            },
            pending_invocations=pending,
        )
        return metrics.snapshot()
    finally:
        await engine.dispose()


async def _seed_reconstruction_source(
    sessions,
    *,
    execution_id: str,
    message_count: int,
) -> None:
    async with sessions() as session:
        checkpoint_id = f"{execution_id}:checkpoint:7"
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id="agent-r11-a",
                correlation_id=f"corr-{execution_id}",
                state="WAITING",
                revision=7,
                current_checkpoint_id=checkpoint_id,
                request={},
            )
        )
        session.add(
            AgentIterationRecord(
                id=f"{execution_id}:iteration:1",
                execution_id=execution_id,
                iteration=1,
                state="WAITING",
                tool_call_ids=[],
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=checkpoint_id,
                execution_id=execution_id,
                execution_revision=7,
                session_id=f"session-{execution_id}",
                task_id=None,
                branch_id=None,
                iteration=1,
                wait_reason="RESOURCE",
                transcript_snapshot=[
                    _message(index) for index in range(message_count)
                ],
                metadata_json={},
            )
        )
        await session.commit()


async def _measure_reconstruction(
    tmp_path,
    message_count: int,
) -> dict[str, int]:
    metrics = _SqlMetrics()
    engine, sessions = await _new_database(
        tmp_path / f"r11-a-reconstruct-{message_count}.sqlite",
        metrics,
    )
    execution_id = f"exec-r11-a-reconstruct-{message_count}"
    try:
        await _seed_reconstruction_source(
            sessions,
            execution_id=execution_id,
            message_count=message_count,
        )
        metrics.reset()
        store = DurableAgentStore(lambda: _MeasuredUow(sessions, metrics))
        started_ns = time.perf_counter_ns()
        transcript = await store.load_fork_safe_checkpoint_transcript(
            execution_id,
            f"{execution_id}:checkpoint:7",
        )
        elapsed_ns = time.perf_counter_ns() - started_ns
        result = metrics.snapshot()
        result.update(
            {
                "messages": len(transcript),
                "logical_bytes": _json_bytes(
                    [item.model_dump(mode="json") for item in transcript]
                ),
                "elapsed_ns": elapsed_ns,
            }
        )
        return result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_a_current_waiting_checkpoint_write_amplification_baseline(
    tmp_path,
):
    samples = {
        count: await _measure_waiting_commit(tmp_path, count)
        for count in (0, 1, 8, 32)
    }

    baseline = samples[0]
    assert all(sample["commits"] == 1 for sample in samples.values())
    assert all(sample["rollbacks"] == 0 for sample in samples.values())

    # Current production path performs one additional invocation lookup,
    # pending-row INSERT and ORM flush for every pending invocation.
    for count, sample in samples.items():
        assert sample["select"] - baseline["select"] == count
        assert sample["insert"] - baseline["insert"] == count
        assert sample["flushes"] - baseline["flushes"] == count


def test_r11_a_full_snapshot_growth_exposes_quadratic_copy_baseline():
    samples = {
        count: _snapshot_growth(count)
        for count in (10, 100, 1000)
    }

    assert samples[10]["copy_amplification"] > 5
    assert samples[100]["copy_amplification"] > 45
    assert samples[1000]["copy_amplification"] > 450
    assert (
        samples[1000]["cumulative_snapshot_bytes"]
        > samples[1000]["final_transcript_bytes"] * 450
    )


@pytest.mark.asyncio
async def test_r11_a_fork_reconstruction_query_count_is_transcript_size_independent(
    tmp_path,
):
    samples = {
        count: await _measure_reconstruction(tmp_path, count)
        for count in (10, 100, 1000)
    }

    query_counts = {sample["select"] for sample in samples.values()}
    assert len(query_counts) == 1
    assert all(sample["messages"] == count for count, sample in samples.items())
    assert samples[1000]["logical_bytes"] > samples[100]["logical_bytes"]

    # Wall-clock time is intentionally measured but not thresholded in default
    # CI because host variance would make a correctness gate flaky.
    assert all(sample["elapsed_ns"] > 0 for sample in samples.values())


@pytest.mark.asyncio
async def test_r11_a_baseline_probe_is_reproducible_and_reports_all_dimensions(
    tmp_path,
):
    pending = {
        count: await _measure_waiting_commit(tmp_path, count)
        for count in (0, 1, 8, 32)
    }
    reconstruction = {
        count: await _measure_reconstruction(tmp_path, count)
        for count in (10, 100, 1000)
    }
    growth = {
        count: _snapshot_growth(count)
        for count in (10, 100, 1000)
    }

    report = {
        "pending": pending,
        "reconstruction": reconstruction,
        "growth": growth,
    }

    # Intentional first-run probe. The red CI output becomes immutable baseline
    # evidence on Issue #31; the follow-up commit replaces this sentinel with
    # structural assertions over the observed report.
    pytest.fail(
        "R11_A_BASELINE_PROBE="
        + json.dumps(report, sort_keys=True, separators=(",", ":"))
    )
