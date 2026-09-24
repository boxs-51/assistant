from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import math
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
    checkpoint_insert_parameter_bytes: int = 0

    def reset(self) -> None:
        self.statements.clear()
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0
        self.checkpoint_insert_parameter_bytes = 0

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


def _bound_parameter_bytes(value) -> int:
    if value is None:
        return 0
    if isinstance(value, memoryview):
        return value.nbytes
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(
            _bound_parameter_bytes(key) + _bound_parameter_bytes(item)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list)):
        return sum(_bound_parameter_bytes(item) for item in value)
    return len(str(value).encode("utf-8"))


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
        if token == "INSERT" and "agent_execution_checkpoints" in statement:
            metrics.checkpoint_insert_parameter_bytes += (
                _bound_parameter_bytes(_parameters)
            )

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


async def _measure_waiting_commit(
    tmp_path,
    pending_count: int,
    *,
    message_count: int = 1,
    capture_checkpoint_bytes: bool = False,
) -> dict[str, int]:
    metrics = _SqlMetrics()
    engine, sessions = await _new_database(
        tmp_path / (
            f"r11-a-wait-{pending_count}-messages-{message_count}.sqlite"
        ),
        metrics,
    )
    execution_id = f"exec-r11-a-{pending_count}-m{message_count}"
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
                "transcript_snapshot": [
                    _message(index) for index in range(message_count)
                ],
                "metadata_json": {"phase": "r11-a"},
            },
            pending_invocations=pending,
        )
        result = metrics.snapshot()
        if capture_checkpoint_bytes:
            result["checkpoint_insert_parameter_bytes"] = (
                metrics.checkpoint_insert_parameter_bytes
            )
        return result
    finally:
        await engine.dispose()


def _nearest_rank_percentiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    assert ordered

    def at(percent: int) -> int:
        index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
        return ordered[index]

    return {"p50_ns": at(50), "p95_ns": at(95), "p99_ns": at(99)}


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


async def _measure_reconstruction_distribution(
    tmp_path,
    message_count: int,
    *,
    samples: int = 20,
) -> dict[str, int]:
    metrics = _SqlMetrics()
    engine, sessions = await _new_database(
        tmp_path / f"r11-a-reconstruct-dist-{message_count}.sqlite",
        metrics,
    )
    execution_id = f"exec-r11-a-reconstruct-dist-{message_count}"
    try:
        await _seed_reconstruction_source(
            sessions,
            execution_id=execution_id,
            message_count=message_count,
        )
        store = DurableAgentStore(lambda: _MeasuredUow(sessions, metrics))
        elapsed: list[int] = []
        for _ in range(samples):
            metrics.reset()
            started_ns = time.perf_counter_ns()
            transcript = await store.load_fork_safe_checkpoint_transcript(
                execution_id,
                f"{execution_id}:checkpoint:7",
            )
            elapsed.append(time.perf_counter_ns() - started_ns)
            assert len(transcript) == message_count
            snapshot = metrics.snapshot()
            assert snapshot["select"] == snapshot["sql"] == 3
            assert snapshot["insert"] == snapshot["update"] == 0
            assert snapshot["delete"] == 0
        result = {"samples": samples}
        result.update(_nearest_rank_percentiles(elapsed))
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

    # R11-D DUAL cutover now writes one content-addressed transcript
    # representation graph in the same UoW as the checkpoint. Keep the
    # baseline exact so later performance stages measure changes deliberately.
    expected_pending = {
        0: {"begins": 1, "commits": 1, "delete": 0, "flushes": 1,
            "insert": 4, "rollbacks": 0, "select": 15, "sql": 20, "update": 1},
        1: {"begins": 1, "commits": 1, "delete": 0, "flushes": 2,
            "insert": 5, "rollbacks": 0, "select": 16, "sql": 22, "update": 1},
        8: {"begins": 1, "commits": 1, "delete": 0, "flushes": 9,
            "insert": 12, "rollbacks": 0, "select": 23, "sql": 36, "update": 1},
        32: {"begins": 1, "commits": 1, "delete": 0, "flushes": 33,
             "insert": 36, "rollbacks": 0, "select": 47, "sql": 84, "update": 1},
    }
    assert pending == expected_pending

    assert growth == {
        10: {
            "iterations": 10,
            "final_transcript_bytes": 2261,
            "cumulative_snapshot_bytes": 12440,
            "copy_amplification": 5.502,
        },
        100: {
            "iterations": 100,
            "final_transcript_bytes": 22601,
            "cumulative_snapshot_bytes": 1141400,
            "copy_amplification": 50.502,
        },
        1000: {
            "iterations": 1000,
            "final_transcript_bytes": 226001,
            "cumulative_snapshot_bytes": 113114000,
            "copy_amplification": 500.502,
        },
    }

    for count, sample in reconstruction.items():
        assert sample["begins"] == 1
        assert sample["commits"] == 1
        assert sample["rollbacks"] == 0
        assert sample["select"] == sample["sql"] == 3
        assert sample["insert"] == sample["update"] == sample["delete"] == 0
        assert sample["flushes"] == 0
        assert sample["messages"] == count
        assert sample["logical_bytes"] == growth[count]["final_transcript_bytes"]
        assert sample["elapsed_ns"] > 0



@pytest.mark.asyncio
async def test_r11_a_real_writer_bytes_and_reconstruction_percentile_red_probe(
    tmp_path,
):
    writer = {
        count: await _measure_waiting_commit(
            tmp_path,
            0,
            message_count=count,
            capture_checkpoint_bytes=True,
        )
        for count in (10, 100, 1000)
    }
    reconstruction = {
        count: await _measure_reconstruction_distribution(
            tmp_path,
            count,
            samples=20,
        )
        for count in (10, 100, 1000)
    }

    for count, sample in writer.items():
        logical = _snapshot_growth(count)["final_transcript_bytes"]
        assert sample["checkpoint_insert_parameter_bytes"] > logical
    assert (
        writer[10]["checkpoint_insert_parameter_bytes"]
        < writer[100]["checkpoint_insert_parameter_bytes"]
        < writer[1000]["checkpoint_insert_parameter_bytes"]
    )

    for sample in reconstruction.values():
        assert sample["samples"] == 20
        assert 0 < sample["p50_ns"] <= sample["p95_ns"] <= sample["p99_ns"]

    # Exact numeric baseline is preserved durably on Issue #31 / CI #951/#953.
    # CI gates only structural properties; shared-runner timings remain evidence.
    assert writer[1000]["checkpoint_insert_parameter_bytes"] > 200_000
