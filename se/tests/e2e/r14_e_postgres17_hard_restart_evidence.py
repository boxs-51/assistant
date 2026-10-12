"""Explicit-only PostgreSQL 17 hard-restart witness for AE-R14-E P1A.

The filename intentionally has no test_ prefix: normal full-suite/portable
Architecture jobs remain database-independent. Dedicated Linux and Windows
PostgreSQL CI MUST invoke this filename explicitly and cannot skip this test.
"""

from __future__ import annotations

import os
import asyncio
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from se.src.infrastructure.storage.models.sql.agent import (
    AgentTaskRecord, TaskBudgetRecord, TaskBudgetReservationRecord,
    AgentResumeClaimRecord,
)
from se.src.infrastructure.storage.models.sql.user_budget import (
    UserBudgetDualAccountingReceiptRecord, UserBudgetReservationRecord,
)
from se.tests.e2e.test_r14_e_sql_restart_agent_hitl_k1_k2 import (
    TASK_ID, _task_budget_service, _await_file, _atomic_json,
)

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from se.tests.e2e.test_r14_e_sql_restart_agent_hitl_k1_k2 import (
    run_physical_hard_restart_witness,
)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "approve_terminal",
    "deny_terminal",
    "effect_before_terminal",
])
async def test_postgres17_real_s1_kill_and_k2_rediscovery(tmp_path, scenario):
    url = os.environ.get("R14_E_P1A_POSTGRES_URL", "")
    assert url.startswith("postgresql+asyncpg://"), (
        "Real PostgreSQL 17 DSN is mandatory; no SQLite fallback/skip."
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            version = int((await conn.exec_driver_sql(
                "SHOW server_version_num"
            )).scalar_one())
            database = (await conn.exec_driver_sql(
                "SELECT current_database()"
            )).scalar_one()
            assert 170000 <= version < 180000, (
                f"Expected real PostgreSQL 17, got version_num={version}"
            )
            assert database == "r14e_p1a", (
                f"Refuse shared/unexpected test database {database!r}"
            )
    finally:
        await engine.dispose()

    # Each parameter has a distinct fresh database. Never let pre-existing
    # execution/claim rows from the previous hard-restart scenario pass a gate.
    database_names = {
        "approve_terminal": "r14e_p1a_approve",
        "deny_terminal": "r14e_p1a_deny",
        "effect_before_terminal": "r14e_p1a_unknown",
    }
    database_name = database_names[scenario]
    admin = await asyncpg.connect(
        url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        await admin.execute(f"CREATE DATABASE {database_name}")
    finally:
        await admin.close()

    scoped_url = url.rsplit("/", 1)[0] + "/" + database_name
    await run_physical_hard_restart_witness(
        tmp_path, postgres_url=scoped_url, scenario=scenario,
    )

# Dedicated real PostgreSQL multi-OS-process terminalization witness. This
# is a *Task terminalization ownership* race after S1/S2 recovery, not an
# assertion that a Task must spontaneously auto-close with its AgentExecution.
async def _terminalize_worker(worker: str, root: Path) -> None:
    url = os.environ.get("R14_E_P1A_CHILD_SQL_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise AssertionError("Worker must use the actual PostgreSQL engine")
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        _atomic_json(root / f"worker-{worker}-ready.json", {
            "pid": os.getpid(), "worker": worker,
        })
        await _await_file(root / "workers-release.json", timeout=45.0)
        service = _task_budget_service(sessions)
        changed = await service.terminalize_task(
            TASK_ID,
            allowed_source_states=("RUNNING",),
            target_state="COMPLETED",
        )
        _atomic_json(root / f"worker-{worker}-result.json", {
            "pid": os.getpid(), "state": changed.status,
            "revision": int(changed.revision),
        })
    finally:
        await engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_postgres17_two_os_workers_terminalize_one_task_once(tmp_path):
    """Two independent PIDs compete for the same Task/TaskBudget CAS."""
    url = os.environ.get("R14_E_P1A_POSTGRES_URL", "")
    assert url.startswith("postgresql+asyncpg://"), "PostgreSQL DSN required"
    # Never reuse the three preexisting fault-cut databases.
    admin = await asyncpg.connect(
        url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        await admin.execute("CREATE DATABASE r14e_p1a_terminal_race")
    finally:
        await admin.close()
    scoped_url = url.rsplit("/", 1)[0] + "/r14e_p1a_terminal_race"
    await run_physical_hard_restart_witness(
        tmp_path, postgres_url=scoped_url, scenario="approve_terminal",
    )

    engine = create_async_engine(scoped_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workers = []
    handles = []
    try:
        async with sessions() as session:
            original_task = await session.get(AgentTaskRecord, TASK_ID)
            original_budget = await session.get(TaskBudgetRecord, TASK_ID)
            assert original_task is not None and original_task.status == "RUNNING"
            assert original_budget is not None and original_budget.state == "OPEN"
            assert original_budget.active_executions == 0
            original_revision = int(original_task.revision)

        for worker in ("a", "b"):
            output = (tmp_path / f"worker-{worker}.log").open("ab", buffering=0)
            handles.append(output)
            env = dict(os.environ)
            env["R14_E_P1A_CHILD_SQL_URL"] = scoped_url
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()),
                 "--terminal-worker", worker, str(tmp_path)],
                cwd=str(Path(__file__).resolve().parents[3]),
                env=env, stdout=output, stderr=subprocess.STDOUT,
            )
            workers.append(process)
        try:
            for worker, process in zip(("a", "b"), workers):
                ready = await _await_file(
                    tmp_path / f"worker-{worker}-ready.json", timeout=45.0,
                )
                assert ready["pid"] == process.pid
                assert process.poll() is None

            _atomic_json(tmp_path / "workers-release.json", {"go": True})
            results = []
            for worker, process in zip(("a", "b"), workers):
                rc = await asyncio.to_thread(process.wait, 60)
                if rc != 0:
                    log = (tmp_path / f"worker-{worker}.log").read_text(
                        encoding="utf-8", errors="replace",
                    )
                    raise AssertionError(
                        f"terminal worker {worker} exited {rc}: {log[-3500:]}"
                    )
                results.append(await _await_file(
                    tmp_path / f"worker-{worker}-result.json", timeout=5.0,
                ))
            assert len({r["pid"] for r in results}) == 2
            assert all(r["state"] == "COMPLETED" for r in results)
            assert all(r["revision"] == original_revision + 1 for r in results)
        finally:
            for process in workers:
                if process.poll() is None:
                    process.kill()
                await asyncio.to_thread(process.wait, 15)

        async with sessions() as session:
            task = await session.get(AgentTaskRecord, TASK_ID)
            budget = await session.get(TaskBudgetRecord, TASK_ID)
            reservations = (await session.execute(
                select(TaskBudgetReservationRecord).where(
                    TaskBudgetReservationRecord.task_id == TASK_ID,
                )
            )).scalars().all()
            bridges = (await session.execute(
                select(UserBudgetDualAccountingReceiptRecord).where(
                    UserBudgetDualAccountingReceiptRecord.task_id == TASK_ID,
                )
            )).scalars().all()
            ubq = (await session.execute(
                select(UserBudgetReservationRecord).where(
                    UserBudgetReservationRecord.owner_user_id == "r7j-user",
                )
            )).scalars().all()
            claims = (await session.execute(
                select(AgentResumeClaimRecord).where(
                    AgentResumeClaimRecord.task_id == TASK_ID,
                )
            )).scalars().all()
            assert task.status == "COMPLETED"
            assert task.revision == original_revision + 1
            assert budget.state == "CLOSED" and budget.closed_at is not None
            assert budget.used_executions == 1
            assert budget.used_tool_calls == 1
            assert budget.active_executions == 0
            assert sum(x.kind == "NEW_EXECUTION" for x in reservations) == 1
            assert sum(x.kind == "TOOL_CALL" for x in reservations) == 1
            tool_bridges = [
                x for x in bridges
                if x.task_budget_kind == "TOOL_CALL"
                and x.task_budget_reservation_key == "call-r7j-1"
            ]
            assert len(tool_bridges) == 1
            settled = [
                x for x in ubq
                if x.reservation_id == tool_bridges[0].ubq_reservation_id
            ]
            assert len(settled) == 1
            assert settled[0].state == "SETTLED"
            assert settled[0].settled_amount_atomic == 1
            assert all(x.state != "CREATED" for x in claims)
        # Stale opposing terminal request must not rewrite the winner.
        stale = await _task_budget_service(sessions).terminalize_task(
            TASK_ID, allowed_source_states=("RUNNING",),
            target_state="FAILED",
        )
        assert stale.status == "COMPLETED"
        assert stale.revision == original_revision + 1
        _atomic_json(tmp_path / "multiworker-terminal-result.json", {
            "database": "r14e_p1a_terminal_race",
            "version_class": 17,
            "worker_pids": [r["pid"] for r in results],
            "worker_revisions": [r["revision"] for r in results],
            "task_terminal": task.status,
            "task_revision": task.revision,
            "budget_terminal": budget.state,
            "used_executions": budget.used_executions,
            "used_tool_calls": budget.used_tool_calls,
            "tool_bridge_count": len(tool_bridges),
            "ubq_settled": settled[0].settled_amount_atomic,
            "orphan_created_claims": sum(x.state == "CREATED" for x in claims),
        })
    finally:
        for output in handles:
            output.close()
        await engine.dispose()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--terminal-worker":
    asyncio.run(_terminalize_worker(sys.argv[2], Path(sys.argv[3])))
