from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.models.sql.agent import AgentIterationRecord
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.fork import ForkAdmission
from se.src.runtimes.agent.fork_planning import AgentForkPlanningService
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import ForkConsumeError, TaskBudgetService


@dataclass
class _Metrics:
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

    def snapshot(self) -> dict[str, int]:
        return {
            "sql": sum(self.statements.values()),
            "select": self.statements["SELECT"],
            "insert": self.statements["INSERT"],
            "update": self.statements["UPDATE"],
            "delete": self.statements["DELETE"],
            "flushes": self.flushes,
            "commits": self.commits,
            "rollbacks": self.rollbacks,
            "begins": self.begins,
        }


class _Uow:
    def __init__(self, sessions, metrics: _Metrics):
        self._sessions = sessions
        self._metrics = metrics
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        event.listen(self.session.sync_session, "before_flush", self._before_flush)
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(self.session)
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


def _install_metrics(engine, metrics: _Metrics) -> None:
    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        metrics.statements[statement.lstrip().split(None, 1)[0].upper()] += 1

    def on_begin(_conn):
        metrics.begins += 1

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine.sync_engine, "begin", on_begin)


def _limits(**updates) -> TaskBudgetLimits:
    values = {
        "max_total_executions": 64,
        "max_active_executions": 32,
        "max_active_branches": 32,
        "max_parallel_agents": 32,
        "max_total_tool_calls": 256,
        "max_total_inference_calls": 256,
        "max_total_tokens": 1_000_000,
        "max_total_cost_usd": "100",
        "max_delegation_depth": 8,
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


def _runtime_context_state(tag: str) -> dict[str, object]:
    return {
        "request_id": f"req-{tag}",
        "workflow_id": "wf-r11-a",
        "metadata": {"tag": tag},
        "causation_id": f"cause-{tag}",
        "trace_id": f"trace-{tag}",
        "limits": AgentExecutionLimits().model_dump(mode="json"),
    }


async def _setup(tmp_path, *, name: str, limits=None):
    metrics = _Metrics()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    _install_metrics(engine, metrics)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions, metrics)
    service = TaskBudgetService(
        factory,
        default_limits=limits or _limits(),
        default_policy=TaskBudgetPolicy(version="r11-a-baseline"),
        max_conflict_retries=16,
    )
    store = DurableAgentStore(factory)
    planner = AgentForkPlanningService(store)
    return engine, sessions, service, planner, metrics


def _root_branch_id(task_id: str) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:40]
    return f"r8_root_{digest}"


async def _seed_fork_source(
    sessions,
    service,
    planner,
    metrics,
    *,
    task_id: str,
    fork_request_id: str,
):
    session_id = f"session-{task_id}"
    execution_id = f"exec-{task_id}"
    checkpoint_id = f"cp-{task_id}"

    await service.create_task_with_budget(
        {
            "id": task_id,
            "session_id": session_id,
            "created_by": "user-r11-a",
            "assigned_agent_id": "agent-r11-a",
            "revision": 0,
            "status": "ASSIGNED",
            "wait_reasons": [],
            "input": {"goal": "measure fork"},
        }
    )
    await service.transition_task(
        task_id,
        allowed_source_states=("ASSIGNED",),
        target_state="RUNNING",
        values={},
    )
    root = await service.start_root_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        execution_values={
            "id": execution_id,
            "session_id": session_id,
            "agent_id": "agent-r11-a",
            "task_id": task_id,
            "correlation_id": f"corr-{task_id}",
            "state": "RUNNING",
            "revision": 1,
            "remaining_active_budget_seconds": 30.0,
            "request": {"prompt": "source"},
            "context_state": _runtime_context_state(task_id),
            "started_at": datetime.now(timezone.utc),
        },
    )

    async with _Uow(sessions, metrics) as uow:
        uow.session.add(
            AgentIterationRecord(
                id=f"iter-{task_id}",
                execution_id=execution_id,
                iteration=1,
                state="WAITING",
                tool_call_ids=[],
            )
        )
        await uow.commit()

    assert root.branch_id == _root_branch_id(task_id)

    assert await service.finish_task_scoped_execution(
        task_id,
        execution_id=execution_id,
        source_revision=1,
        transition_values={
            "state": "WAITING",
            "wait_reason": "RESOURCE",
            "remaining_active_budget_seconds": 30.0,
            "wait_expires_at": None,
            "completed_at": None,
        },
        delegated=False,
        checkpoint_values={
            "checkpoint_id": checkpoint_id,
            "execution_id": execution_id,
            "execution_revision": 2,
            "session_id": session_id,
            "task_id": task_id,
            "branch_id": root.branch_id,
            "iteration": 1,
            "wait_reason": "RESOURCE",
            "remaining_active_budget_seconds": 30.0,
            "wait_expires_at": None,
            "transcript_snapshot": [{"role": "user", "content": "base"}],
            "metadata_json": {"phase": "r11-a"},
        },
        pending_invocations=(),
    ) == 2

    plan = await planner.build_fork_plan(
        fork_request_id=fork_request_id,
        task_id=task_id,
        source_branch_id=root.branch_id,
        source_execution_id=execution_id,
        source_checkpoint_id=checkpoint_id,
        target_user_id="user-r11-a",
        overlay_messages=({"role": "user", "content": "fork-local"},),
    )
    return {
        "task_id": task_id,
        "source_branch_id": root.branch_id,
        "source_execution_id": execution_id,
        "checkpoint_id": checkpoint_id,
        "plan": plan,
    }


async def _measure_branch_create(tmp_path) -> dict[str, int]:
    engine, sessions, service, planner, metrics = await _setup(
        tmp_path,
        name="r11-a-branch-create.sqlite",
    )
    try:
        source = await _seed_fork_source(
            sessions,
            service,
            planner,
            metrics,
            task_id="task-r11-a-branch",
            fork_request_id="fork-r11-a-one",
        )
        metrics.reset()
        started_ns = time.perf_counter_ns()
        admission = await service.consume_fork_plan(source["plan"])
        elapsed_ns = time.perf_counter_ns() - started_ns
        assert isinstance(admission, ForkAdmission)

        snapshot = metrics.snapshot()
        snapshot["elapsed_ns"] = elapsed_ns

        async with _Uow(sessions, metrics) as uow:
            branches = await uow.agents.list_task_branches(source["task_id"])
            execution = await uow.agents.get_execution(admission.execution_id)
            context = await uow.agents.get_task_branch_context(
                admission.branch_id
            )
        snapshot["branch_rows"] = len(branches)
        snapshot["created_execution"] = int(execution is not None)
        snapshot["created_context"] = int(context is not None)
        return snapshot
    finally:
        await engine.dispose()


async def _measure_task_budget_contention(tmp_path) -> dict[str, int]:
    engine, sessions, service, planner, metrics = await _setup(
        tmp_path,
        name="r11-a-budget-contention.sqlite",
        limits=_limits(max_active_branches=2),
    )
    try:
        source = await _seed_fork_source(
            sessions,
            service,
            planner,
            metrics,
            task_id="task-r11-a-contention",
            fork_request_id="fork-r11-a-a",
        )
        second = await planner.build_fork_plan(
            fork_request_id="fork-r11-a-b",
            task_id=source["task_id"],
            source_branch_id=source["source_branch_id"],
            source_execution_id=source["source_execution_id"],
            source_checkpoint_id=source["checkpoint_id"],
            target_user_id="user-r11-a",
            overlay_messages=({"role": "user", "content": "fork-local-b"},),
        )

        metrics.reset()
        started_ns = time.perf_counter_ns()
        results = await asyncio.gather(
            service.consume_fork_plan(source["plan"]),
            service.consume_fork_plan(second),
            return_exceptions=True,
        )
        elapsed_ns = time.perf_counter_ns() - started_ns

        winners = [item for item in results if isinstance(item, ForkAdmission)]
        conflicts = [item for item in results if isinstance(item, ForkConsumeError)]
        assert len(winners) == 1
        assert len(conflicts) == 1

        budget = await service.get_budget(source["task_id"])
        snapshot = metrics.snapshot()
        snapshot.update(
            {
                "elapsed_ns": elapsed_ns,
                "attempts": 2,
                "winners": len(winners),
                "conflicts": len(conflicts),
                "final_budget_revision": int(budget.revision),
                "final_active_branches": int(budget.active_branches),
            }
        )
        return snapshot
    finally:
        await engine.dispose()


def _new_context(index: int) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=f"exec-memory-{index}",
        agent_id="agent-r11-a",
        session_id=f"session-memory-{index}",
        correlation_id=f"corr-memory-{index}",
        identity=Identity(
            user_id=f"user-{index}",
            auth_type="guest",
            roles=["user"],
            permissions=["chat"],
            scopes={"agent.execute"},
        ),
        limits=AgentExecutionLimits(),
        task_id=f"task-memory-{index}",
        branch_id=f"branch-memory-{index}",
        input={"prompt": "x" * 512},
        metadata={"phase": "r11-a", "index": index},
        branch_base_transcript=[
            {
                "role": "user",
                "content": "base-" + ("y" * 128),
                "metadata": {},
            }
            for _ in range(4)
        ],
        branch_runtime_seed_fingerprint="f" * 64,
        activate_budget=False,
    )


def _measure_active_execution_memory(count: int = 128) -> dict[str, int]:
    # Warm imports/Pydantic caches before the measured allocation window.
    _new_context(-1)
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        contexts = [_new_context(index) for index in range(count)]
        after = tracemalloc.take_snapshot()
        allocated = sum(
            stat.size_diff
            for stat in after.compare_to(before, "filename")
            if stat.size_diff > 0
        )
        assert len(contexts) == count
        return {
            "contexts": count,
            "allocated_bytes": allocated,
            "approx_bytes_per_execution": allocated // count,
        }
    finally:
        tracemalloc.stop()


@pytest.mark.asyncio
async def test_r11_a_branch_create_measurement_uses_real_fork_consume(tmp_path):
    sample = await _measure_branch_create(tmp_path)

    assert sample["commits"] >= 1
    assert sample["insert"] >= 1
    assert sample["update"] >= 1
    assert sample["branch_rows"] == 2
    assert sample["created_execution"] == 1
    assert sample["created_context"] == 1
    assert sample["elapsed_ns"] > 0


@pytest.mark.asyncio
async def test_r11_a_task_budget_contention_measurement_preserves_one_winner(
    tmp_path,
):
    sample = await _measure_task_budget_contention(tmp_path)

    assert sample["attempts"] == 2
    assert sample["winners"] == 1
    assert sample["conflicts"] == 1
    assert sample["final_active_branches"] == 2
    assert sample["elapsed_ns"] > 0


def test_r11_a_memory_per_active_execution_measurement_method():
    sample = _measure_active_execution_memory()

    assert sample["contexts"] == 128
    assert sample["allocated_bytes"] > 0
    assert sample["approx_bytes_per_execution"] > 0


@pytest.mark.asyncio
async def test_r11_a_branch_budget_memory_probe_report(tmp_path):
    report = {
        "branch_create": await _measure_branch_create(tmp_path),
        "task_budget_contention": await _measure_task_budget_contention(tmp_path),
        "memory": _measure_active_execution_memory(),
    }

    pytest.fail(
        "R11_A_BRANCH_MEMORY_PROBE="
        + json.dumps(report, sort_keys=True, separators=(",", ":"))
    )
