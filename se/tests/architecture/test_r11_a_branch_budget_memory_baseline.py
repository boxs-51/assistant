from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import math
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text as sql_text
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


@dataclass
class _CasTiming:
    task_ns: list[int] = field(default_factory=list)
    budget_ns: list[int] = field(default_factory=list)


@dataclass
class _BudgetCasTiming:
    elapsed_ns: list[int] = field(default_factory=list)
    outcomes: Counter[str] = field(default_factory=Counter)


class _OneShotBarrier:
    def __init__(self, parties: int = 2):
        self.parties = parties
        self.arrivals = 0
        self._released = False
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            if self._released:
                return
            self.arrivals += 1
            if self.arrivals >= self.parties:
                self._released = True
                self._event.set()
                return
        await self._event.wait()


class _BarrierAgentRepository(AgentRepository):
    def __init__(self, session, barrier: _OneShotBarrier, timing: _CasTiming):
        super().__init__(session)
        self._barrier = barrier
        self._timing = timing

    async def compare_and_set_task(
        self,
        task_id,
        expected_revision,
        values,
    ):
        await self._barrier.wait()
        started_ns = time.perf_counter_ns()
        try:
            return await super().compare_and_set_task(
                task_id,
                expected_revision,
                values,
            )
        finally:
            self._timing.task_ns.append(time.perf_counter_ns() - started_ns)

    async def compare_and_set_task_budget(
        self,
        task_id,
        expected_revision,
        values,
    ):
        started_ns = time.perf_counter_ns()
        try:
            return await super().compare_and_set_task_budget(
                task_id,
                expected_revision,
                values,
            )
        finally:
            self._timing.budget_ns.append(time.perf_counter_ns() - started_ns)


class _BudgetBarrierAgentRepository(AgentRepository):
    def __init__(
        self,
        session,
        barrier: _OneShotBarrier,
        timing: _BudgetCasTiming,
    ):
        super().__init__(session)
        self._barrier = barrier
        self._timing = timing

    async def compare_and_set_task_budget(
        self,
        task_id,
        expected_revision,
        values,
    ):
        await self._barrier.wait()
        started_ns = time.perf_counter_ns()
        try:
            result = await super().compare_and_set_task_budget(
                task_id,
                expected_revision,
                values,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                self._timing.outcomes["locked"] += 1
            else:
                self._timing.outcomes[
                    f"error:{type(exc).__name__}"
                ] += 1
            raise
        else:
            self._timing.outcomes[
                "success" if result is not None else "stale"
            ] += 1
            return result
        finally:
            self._timing.elapsed_ns.append(
                time.perf_counter_ns() - started_ns
            )


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


class _BarrierUow(_Uow):
    def __init__(
        self,
        sessions,
        metrics: _Metrics,
        barrier: _OneShotBarrier,
        timing: _CasTiming,
    ):
        super().__init__(sessions, metrics)
        self._barrier = barrier
        self._timing = timing

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        event.listen(self.session.sync_session, "before_flush", self._before_flush)
        self.agents = _BarrierAgentRepository(
            self.session,
            self._barrier,
            self._timing,
        )
        self.capability_invocations = CapabilityInvocationRepository(self.session)
        return self


class _BudgetBarrierUow(_Uow):
    def __init__(
        self,
        sessions,
        metrics: _Metrics,
        barrier: _OneShotBarrier,
        timing: _BudgetCasTiming,
    ):
        super().__init__(sessions, metrics)
        self._barrier = barrier
        self._timing = timing

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        event.listen(self.session.sync_session, "before_flush", self._before_flush)
        self.agents = _BudgetBarrierAgentRepository(
            self.session,
            self._barrier,
            self._timing,
        )
        self.capability_invocations = CapabilityInvocationRepository(self.session)
        return self


def _percentiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    assert ordered

    def at(percent: int) -> int:
        index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
        return ordered[index]

    return {"p50_ns": at(50), "p95_ns": at(95), "p99_ns": at(99)}


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



async def _measure_branch_create_distribution(
    tmp_path,
    *,
    samples: int = 10,
) -> dict[str, int]:
    engine, sessions, service, planner, metrics = await _setup(
        tmp_path,
        name="r11-a-branch-create-distribution.sqlite",
    )
    elapsed: list[int] = []
    try:
        for index in range(samples):
            source = await _seed_fork_source(
                sessions,
                service,
                planner,
                metrics,
                task_id=f"task-r11-a-branch-dist-{index}",
                fork_request_id=f"fork-r11-a-dist-{index}",
            )
            started_ns = time.perf_counter_ns()
            admission = await service.consume_fork_plan(source["plan"])
            elapsed.append(time.perf_counter_ns() - started_ns)
            assert isinstance(admission, ForkAdmission)
        result = {"samples": samples}
        result.update(_percentiles(elapsed))
        return result
    finally:
        await engine.dispose()


async def _count_task_rows(session) -> dict[str, int]:
    tables = (
        "agent_tasks",
        "agent_task_budgets",
        "agent_task_branches",
        "agent_task_branch_contexts",
        "agent_executions",
        "agent_execution_checkpoints",
        "agent_iterations",
        "agent_task_budget_reservations",
        "agent_task_fork_admissions",
    )
    result: dict[str, int] = {}
    for table in tables:
        count = await session.scalar(
            sql_text(f"SELECT COUNT(*) FROM {table}")
        )
        result[table] = int(count or 0)
    result["total_rows"] = sum(result.values())
    return result


async def _measure_rows_per_task(tmp_path) -> dict[str, dict[str, int]]:
    engine, sessions, service, planner, metrics = await _setup(
        tmp_path,
        name="r11-a-rows-per-task.sqlite",
    )
    try:
        source = await _seed_fork_source(
            sessions,
            service,
            planner,
            metrics,
            task_id="task-r11-a-rows",
            fork_request_id="fork-r11-a-rows",
        )
        async with sessions() as session:
            root_waiting = await _count_task_rows(session)

        admission = await service.consume_fork_plan(source["plan"])
        assert isinstance(admission, ForkAdmission)
        async with sessions() as session:
            one_fork = await _count_task_rows(session)

        return {"root_waiting": root_waiting, "one_fork": one_fork}
    finally:
        await engine.dispose()


async def _measure_synchronized_task_budget_contention(
    tmp_path,
) -> dict[str, object]:
    engine, sessions, service, planner, metrics = await _setup(
        tmp_path,
        name="r11-a-budget-contention-synchronized.sqlite",
        limits=_limits(max_active_branches=2),
    )
    try:
        source = await _seed_fork_source(
            sessions,
            service,
            planner,
            metrics,
            task_id="task-r11-a-contention-sync",
            fork_request_id="fork-r11-a-sync-a",
        )
        second = await planner.build_fork_plan(
            fork_request_id="fork-r11-a-sync-b",
            task_id=source["task_id"],
            source_branch_id=source["source_branch_id"],
            source_execution_id=source["source_execution_id"],
            source_checkpoint_id=source["checkpoint_id"],
            target_user_id="user-r11-a",
            overlay_messages=(
                {"role": "user", "content": "fork-local-sync-b"},
            ),
        )

        barrier = _OneShotBarrier(2)
        timing = _CasTiming()
        race_factory = lambda: _BarrierUow(
            sessions,
            metrics,
            barrier,
            timing,
        )
        race_service = TaskBudgetService(
            race_factory,
            default_limits=_limits(max_active_branches=2),
            default_policy=TaskBudgetPolicy(version="r11-a-sync"),
            max_conflict_retries=16,
        )

        metrics.reset()
        started_ns = time.perf_counter_ns()
        results = await asyncio.gather(
            race_service.consume_fork_plan(source["plan"]),
            race_service.consume_fork_plan(second),
            return_exceptions=True,
        )
        elapsed_ns = time.perf_counter_ns() - started_ns

        winners = [item for item in results if isinstance(item, ForkAdmission)]
        conflicts = [item for item in results if isinstance(item, ForkConsumeError)]
        assert len(winners) == 1
        assert len(conflicts) == 1

        budget = await service.get_budget(source["task_id"])
        return {
            "attempts": 2,
            "barrier_arrivals": barrier.arrivals,
            "winners": len(winners),
            "conflicts": len(conflicts),
            "final_active_branches": int(budget.active_branches),
            "final_budget_revision": int(budget.revision),
            "elapsed_ns": elapsed_ns,
            "task_cas_samples": len(timing.task_ns),
            "budget_cas_samples": len(timing.budget_ns),
            "task_cas": _percentiles(timing.task_ns),
            "budget_cas": _percentiles(timing.budget_ns),
        }
    finally:
        await engine.dispose()

async def _measure_direct_task_budget_cas_contention_distribution(
    tmp_path,
    *,
    samples: int = 10,
) -> dict[str, object]:
    engine, sessions, seed_service, _planner, metrics = await _setup(
        tmp_path,
        name="r11-a-direct-budget-cas-contention.sqlite",
        limits=_limits(max_active_branches=8),
    )
    all_cas_ns: list[int] = []
    race_elapsed_ns: list[int] = []
    outcomes: Counter[str] = Counter()
    total_barrier_arrivals = 0
    service_successes = 0
    final_active_branches_total = 0
    reservation_rows_total = 0

    try:
        for index in range(samples):
            task_id = f"task-r11-a-direct-budget-cas-{index}"
            await seed_service.create_task_with_budget(
                {
                    "id": task_id,
                    "session_id": f"session-{task_id}",
                    "created_by": "user-r11-a",
                    "assigned_agent_id": "agent-r11-a",
                    "revision": 0,
                    "status": "ASSIGNED",
                    "wait_reasons": [],
                    "input": {"goal": "measure direct TaskBudget CAS"},
                }
            )

            barrier = _OneShotBarrier(2)
            timing = _BudgetCasTiming()
            race_factory = lambda: _BudgetBarrierUow(
                sessions,
                metrics,
                barrier,
                timing,
            )
            race_service = TaskBudgetService(
                race_factory,
                default_limits=_limits(max_active_branches=8),
                default_policy=TaskBudgetPolicy(
                    version="r11-a-direct-budget-cas"
                ),
                max_conflict_retries=16,
            )

            started_ns = time.perf_counter_ns()
            results = await asyncio.gather(
                race_service.reserve_branch_slot(
                    task_id,
                    reservation_key=f"{task_id}:branch:a",
                ),
                race_service.reserve_branch_slot(
                    task_id,
                    reservation_key=f"{task_id}:branch:b",
                ),
                return_exceptions=True,
            )
            race_elapsed_ns.append(time.perf_counter_ns() - started_ns)

            failures = [
                item for item in results if isinstance(item, BaseException)
            ]
            assert failures == []
            service_successes += len(results)

            assert barrier.arrivals == 2
            total_barrier_arrivals += barrier.arrivals

            retry_signals = (
                timing.outcomes["stale"] + timing.outcomes["locked"]
            )
            assert len(timing.elapsed_ns) >= 3
            assert timing.outcomes["success"] == 2
            assert retry_signals >= 1
            assert not any(
                key.startswith("error:")
                for key in timing.outcomes
            )

            all_cas_ns.extend(timing.elapsed_ns)
            outcomes.update(timing.outcomes)

            budget = await seed_service.get_budget(task_id)
            assert int(budget.active_branches) == 2
            final_active_branches_total += int(budget.active_branches)

            async with sessions() as session:
                reservation_rows = await session.scalar(
                    sql_text(
                        "SELECT COUNT(*) "
                        "FROM agent_task_budget_reservations "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                )
            assert int(reservation_rows or 0) == 2
            reservation_rows_total += int(reservation_rows or 0)

        other_errors = sum(
            value
            for key, value in outcomes.items()
            if key.startswith("error:")
        )
        return {
            "samples": samples,
            "service_calls": samples * 2,
            "service_successes": service_successes,
            "barrier_arrivals": total_barrier_arrivals,
            "cas_attempts": len(all_cas_ns),
            "cas_successes": outcomes["success"],
            "cas_stale": outcomes["stale"],
            "cas_locked": outcomes["locked"],
            "cas_other_errors": other_errors,
            "retry_signals": outcomes["stale"] + outcomes["locked"],
            "final_active_branches_total": final_active_branches_total,
            "reservation_rows_total": reservation_rows_total,
            "cas_latency": _percentiles(all_cas_ns),
            "race_latency": _percentiles(race_elapsed_ns),
        }
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

    branch = report["branch_create"]
    assert branch["begins"] == 1
    assert branch["commits"] == 1
    assert branch["rollbacks"] == 0
    assert branch["delete"] == 0
    assert branch["branch_rows"] == 2
    assert branch["created_execution"] == 1
    assert branch["created_context"] == 1
    assert branch["sql"] > 0
    assert branch["select"] > 0
    assert branch["insert"] > 0
    assert branch["update"] > 0
    assert branch["flushes"] > 0
    assert branch["elapsed_ns"] > 0

    contention = report["task_budget_contention"]
    assert contention["attempts"] == 2
    assert contention["winners"] == 1
    assert contention["conflicts"] == 1
    assert contention["final_active_branches"] == 2
    assert contention["final_budget_revision"] >= 1
    assert contention["sql"] > 0
    assert contention["select"] > 0
    assert contention["insert"] > 0
    assert contention["update"] > 0
    assert contention["flushes"] > 0
    assert contention["elapsed_ns"] > 0

    memory = report["memory"]
    assert memory["contexts"] == 128
    assert memory["allocated_bytes"] > 0
    assert memory["approx_bytes_per_execution"] > 0



@pytest.mark.asyncio
async def test_r11_a_branch_rows_and_synchronized_contention_red_probe(tmp_path):
    report = {
        "branch_latency": await _measure_branch_create_distribution(
            tmp_path,
            samples=10,
        ),
        "rows_per_task": await _measure_rows_per_task(tmp_path),
        "synchronized_contention": (
            await _measure_synchronized_task_budget_contention(tmp_path)
        ),
    }

    branch = report["branch_latency"]
    assert branch["samples"] == 10
    assert 0 < branch["p50_ns"] <= branch["p95_ns"] <= branch["p99_ns"]

    rows = report["rows_per_task"]
    assert rows["root_waiting"]["agent_tasks"] == 1
    assert rows["one_fork"]["agent_tasks"] == 1
    assert rows["one_fork"]["total_rows"] > rows["root_waiting"]["total_rows"]
    assert rows["one_fork"]["agent_task_branches"] == 2
    assert rows["one_fork"]["agent_executions"] == 2
    assert rows["one_fork"]["agent_task_fork_admissions"] == 1

    contention = report["synchronized_contention"]
    assert contention["attempts"] == 2
    assert contention["barrier_arrivals"] == 2
    assert contention["winners"] == 1
    assert contention["conflicts"] == 1
    assert contention["final_active_branches"] == 2
    assert contention["task_cas_samples"] >= 2
    assert contention["budget_cas_samples"] >= 1
    for key in ("task_cas", "budget_cas"):
        percentiles = contention[key]
        assert (
            0
            < percentiles["p50_ns"]
            <= percentiles["p95_ns"]
            <= percentiles["p99_ns"]
        )

    # Exact timing baseline is retained on Issue #31 / CI #952/#953.
    # Keep only deterministic durable-shape and race-authority assertions here.
    assert rows["root_waiting"]["total_rows"] == 10
    assert rows["one_fork"]["total_rows"] == 16


@pytest.mark.asyncio
async def test_r11_a_direct_task_budget_cas_contention_red_probe(tmp_path):
    report = await _measure_direct_task_budget_cas_contention_distribution(
        tmp_path,
        samples=10,
    )

    assert report["samples"] == 10
    assert report["service_calls"] == 20
    assert report["service_successes"] == 20
    assert report["barrier_arrivals"] == 20
    assert report["cas_successes"] == 20
    assert report["cas_attempts"] >= 30
    assert report["retry_signals"] >= 10
    assert report["cas_other_errors"] == 0
    assert report["final_active_branches_total"] == 20
    assert report["reservation_rows_total"] == 20

    for key in ("cas_latency", "race_latency"):
        percentiles = report[key]
        assert (
            0
            < percentiles["p50_ns"]
            <= percentiles["p95_ns"]
            <= percentiles["p99_ns"]
        )

    pytest.fail(
        "R11_A_DIRECT_TASK_BUDGET_CAS_CONTENTION_BASELINE="
        + json.dumps(report, sort_keys=True, separators=(",", ":"))
    )
