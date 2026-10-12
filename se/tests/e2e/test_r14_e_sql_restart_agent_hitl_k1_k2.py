"""R14-E P1A: physical S1 kill, durable SQLite, and real K1/K2 recovery.

A server PID really dies without FastAPI/SQLAlchemy shutdown. The surviving
client has already committed a HIGH-risk, approved, NON_IDEMPOTENT result to
its own SQLite ledger; the result wire frame is withheld. S2 is a different
OS process with a fresh AgentRuntime and the SAME server SQL database.

This bounded witness does NOT certify pending consent, PostgreSQL parity,
UBQ quotas, native SBX-3, or a production-wide R14-E exit.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import CapabilityRuntime as ClientCapabilityRuntime
from cl.src.core.client_invocation_ledger import ClientInvocationLedger, ClientInvocationLedgerState
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.realtime_client import GatewayRealtimeClient
from cl.src.hitl.hitl_manager import HITLManager
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.application.user_budget import (
    DualAccountingSettings, UserBudgetDualAccountingService,
)
from se.src.infrastructure.storage.models.sql.user_budget import (
    UserBudgetTaskBindingRecord, UserBudgetReservationRecord,
    UserBudgetDualAccountingReceiptRecord,
)
from se.src.infrastructure.storage.repositories.user_budget import UserBudgetRepository
from se.src.infrastructure.storage.repositories.user_data.users import UserRepository
from se.src.infrastructure.storage.models.sql.agent import (
    AgentResumeClaimRecord, AgentTaskRecord, TaskBudgetRecord,
    TaskBudgetReservationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import Session as ChatSessionRecord
from se.src.infrastructure.storage.models.sql.user_data.user import User
from se.src.infrastructure.storage.repositories.capability_invocations import SqlCapabilityInvocationStore
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import TaskBudgetService

TASK_ID = "r14e-p1a-task"

class _R14BudgetUow(r7j._SqliteUow):
    async def __aenter__(self):
        await super().__aenter__()
        self.user_budgets = UserBudgetRepository(self.session)
        self.users = UserRepository(self.session)
        return self


def _task_budget_service(sessions):
    factory = lambda: _R14BudgetUow(sessions)
    dual = UserBudgetDualAccountingService(
        factory, DualAccountingSettings(enabled=True),
    )
    return TaskBudgetService(
        factory,
        user_budget_dual_accounting=dual,
        default_limits=TaskBudgetLimits(
            max_total_executions=4,
            max_active_executions=2,
            max_active_branches=2,
            max_parallel_agents=2,
            max_total_tool_calls=8,
            max_total_inference_calls=8,
            max_total_tokens=1000,
            max_total_cost_usd="10",
            max_delegation_depth=4,
        ),
        default_policy=TaskBudgetPolicy(version="r14-e-p1a"),
    )
from se.tests.e2e import test_r7_j_real_network_exit_gate as r7j


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


async def _await_file(path: Path, *, timeout: float = 25.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for process evidence: {path.name}")


async def _serve_child(role: str, root: Path, port: int) -> None:
    """Separate interpreter/process. Do not fake S1 death with graceful stop."""
    db_path = root / "server.sqlite3"
    postgres_url = os.environ.get("R14_E_P1A_CHILD_SQL_URL")
    if postgres_url:
        # Inject a REAL PostgreSQL/asyncpg engine into the existing full
        # R7-J Agent/WS/SQL harness; do not emulate SQL or modify runtime.
        with patch.object(
            r7j, "create_async_engine",
            side_effect=lambda *_args, **_kwargs: create_async_engine(
                postgres_url, pool_pre_ping=True,
            ),
        ):
            state = await r7j._build_server(
                root, db_path=db_path,
                initialize_schema=(role == "s1"), seed_session=False,
            )
        if role == "s1":
            # SQLite's original fixture skips FK enforcement, but PostgreSQL
            # requires the real owner user before the session foreign key.
            async with state.sessions() as session:
                session.add(User(
                    id=r7j.USER_ID,
                    email="r14e-pg17-user@example.invalid",
                    password_hash="test-only-not-an-account",
                ))
                # Flush FK parent before inserting the session in the same
                # transaction; PostgreSQL enforces the sessions.user_id FK.
                await session.flush()
                session.add(ChatSessionRecord(
                    id=r7j.SESSION_ID, user_id=r7j.USER_ID,
                    organization_id=None, status="active",
                ))
                await session.commit()
    else:
        state = await r7j._build_server(
            root, db_path=db_path,
            initialize_schema=(role == "s1"), seed_session=(role == "s1"),
        )
    # Exercise production TaskBudget accounting as part of the SAME SQL
    # execution/recovery, rather than infer a TaskBudget from a taskless R7J.
    budget_service = _task_budget_service(state.sessions)
    state.agent_runtime._task_budget_service = budget_service
    if role == "s1":
        # UBQ2 enrollment resolves a real owner row. The PostgreSQL fixture
        # already seeds the FK user; SQLite's legacy fixture does not.
        async with state.sessions() as session:
            if await session.get(User, r7j.USER_ID) is None:
                session.add(User(
                    id=r7j.USER_ID,
                    email="r14e-p1a-owner@example.invalid",
                    password_hash="test-only",
                ))
                await session.commit()
        await budget_service.create_task_with_budget({
            "id": TASK_ID,
            "session_id": r7j.SESSION_ID,
            "created_by": r7j.USER_ID,
            "assigned_agent_id": r7j.AGENT_ID,
            "status": "ASSIGNED",
            "wait_reasons": [],
            "input": {"prompt": "r14e-p1a"},
        }, identity=state.identity)
        await budget_service.transition_task(
            TASK_ID,
            allowed_source_states=("ASSIGNED",),
            target_state="RUNNING",
        )
    server, server_task, _ = await _start_fixed_gateway(state.app, port)
    _atomic_json(root / f"{role}-ready.json", {
        "pid": os.getpid(), "port": port, "role": role,
        "db_path": str(db_path) if not postgres_url else "postgresql://redacted",
        "backend": "postgresql" if postgres_url else "sqlite",
    })
    if role == "s1":
        await _await_file(root / "s1-start.json")
        context = AgentExecutionContext.create(
            execution_id=r7j.EXECUTION_ID,
            task_id=TASK_ID,
            agent_id=r7j.AGENT_ID,
            session_id=r7j.SESSION_ID,
            correlation_id="r14e-physical-kill",
            identity=state.identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
                timeout_seconds=30,
            ),
            connection_id=r7j.K1,
            agent=state.agent,
            metadata={"client_id": r7j.CLIENT_ID, "model": "r7j-test"},
        )
        result = await state.supervisor.run(
            context, lambda: state.agent_runtime.execute(context),
        )
        if result.state.value != "WAITING" or not result.checkpoint_id:
            raise AssertionError(
                f"S1 did not durably reach WAITING: {result.state}"
            )
        checkpoint = await state.durable_store.load_current_checkpoint(
            r7j.EXECUTION_ID
        )
        pending = await state.durable_store.load_checkpoint_pending_invocations(
            checkpoint.checkpoint_id
        )
        if len(pending) != 1:
            raise AssertionError("Expected exactly one canonical pending call")
        _atomic_json(root / "s1-waiting.json", {
            "pid": os.getpid(), "execution_id": result.execution_id,
            "checkpoint_id": result.checkpoint_id,
            "invocation_id": pending[0].invocation_id,
        })
    # Hard-kill testing: S1 must remain alive until the parent calls kill().
    # S2 stays up until the parent inspects committed SQL truth.
    await asyncio.Event().wait()


async def _start_fixed_gateway(app, port: int):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="error", access_log=False,
    ))
    task = asyncio.create_task(server.serve())
    await r7j._wait_async(lambda: server.started or task.done(), timeout=15.0)
    if task.done():
        await task
        raise AssertionError("Child Uvicorn terminated before ready")
    return server, task, port


class _HoldK1TerminalFrame(GatewayRealtimeClient):
    """Signal only after dispatcher has durably written TERMINAL to CL SQLite."""

    def __init__(self, *args, terminal_ready, release_terminal, **kwargs):
        super().__init__(*args, **kwargs)
        self.terminal_ready = terminal_ready
        self.release_terminal = release_terminal

    def send_error(self, invocation_id, **error):
        self.terminal_ready.set()
        if not self.release_terminal.wait(60):
            raise TimeoutError("K1 denial send was not released")
        return super().send_error(invocation_id, **error)

    def send_result(self, invocation_id, result, **correlation):
        self.terminal_ready.set()
        if not self.release_terminal.wait(60):
            raise TimeoutError("K1 result was not released by parent test")
        return super().send_result(invocation_id, result, **correlation)


async def _connect_held_k1(
    port: int, registry, hitl: HITLManager, ledger: ClientInvocationLedger,
    ready: threading.Event, release: threading.Event,
):
    holder = {}
    realtime = _HoldK1TerminalFrame(
        f"http://127.0.0.1:{port}",
        {"Authorization": "Bearer r7-j"},
        connection_id=r7j.K1,
        session_id=r7j.SESSION_ID,
        client_id=r7j.CLIENT_ID,
        heartbeat_interval=60.0,
        on_message=lambda envelope: holder["capabilities"].handle_message(envelope),
        terminal_ready=ready,
        release_terminal=release,
    )
    dispatcher = CapabilityDispatcher(
        registry, realtime, hitl=hitl, invocation_ledger=ledger,
        client_id=r7j.CLIENT_ID, principal_id=r7j.USER_ID,
    )
    capabilities = ClientCapabilityRuntime(
        registry, realtime, client_id=r7j.CLIENT_ID,
        owner_id=r7j.USER_ID, dispatcher=dispatcher,
    )
    holder["capabilities"] = capabilities
    registered = await asyncio.to_thread(realtime.connect, wait_timeout=8.0)
    assert registered["type"] == "connection.registered"
    registered_tool = await asyncio.to_thread(capabilities.register, timeout=8.0)
    assert registered_tool["type"] == "capability.registered"
    return SimpleNamespace(realtime=realtime, dispatcher=dispatcher, ledger=ledger)


def _registry(tool):
    registry = r7j._client_registry(tool)
    meta = registry.tools[r7j.CAPABILITY_ID]["metadata"]
    meta["idempotency"] = "NON_IDEMPOTENT"
    meta["base_risk"] = "HIGH"
    return registry


def _sink_init(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE effects (id INTEGER PRIMARY KEY, invocation_id TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE approvals (id INTEGER PRIMARY KEY, risk_level TEXT NOT NULL)"
        )


def _sink_count(path: Path, table: str) -> int:
    if table not in {"effects", "approvals"}:
        raise ValueError("Invalid evidence table")
    with sqlite3.connect(path) as db:
        return int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


async def _start_child(
    root: Path, role: str, port: int, logs: list, *, postgres_url=None,
):
    path = root / f"{role}.log"
    log = path.open("ab", buffering=0)
    logs.append(log)
    env = dict(os.environ)
    if postgres_url is None:
        env.pop("R14_E_P1A_CHILD_SQL_URL", None)
    else:
        env["R14_E_P1A_CHILD_SQL_URL"] = postgres_url
    repo_root = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(repo_root), env.get("PYTHONPATH", "")) if part
    )
    # Popen works with the Windows Selector and Proactor event loop policies.
    # asyncio.create_subprocess_exec can be unsupported under Selector policy.
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()),
         "--r14e-child", role, str(root), str(port)],
        cwd=str(repo_root), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        receipt = await _await_file(root / f"{role}-ready.json", timeout=45.0)
    except Exception as exc:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, 15)
        raise AssertionError(
            f"Child {role} failed before ready: "
            + path.read_text(encoding="utf-8", errors="replace")[-5000:]
        ) from exc
    assert receipt["pid"] == process.pid
    assert process.poll() is None
    return process


async def _hard_kill(process) -> int:
    assert process.poll() is None, "S1 already exited before the hard kill"
    pid = process.pid
    process.kill()  # SIGKILL on POSIX; TerminateProcess on Windows
    code = await asyncio.to_thread(process.wait, 15)
    assert code != 0
    return pid


async def run_physical_hard_restart_witness(
    tmp_path, *, postgres_url: str | None = None,
    scenario: str = "approve_terminal",
):
    """Reproduce real K1-to-K2 hard restart for a selected consent fault cut."""
    assert scenario in {"approve_terminal", "deny_terminal", "effect_before_terminal"}
    db_path = tmp_path / "server.sqlite3"
    sink_path = tmp_path / "independent_effects.sqlite3"
    ledger_path = tmp_path / "client.sqlite3"
    _sink_init(sink_path)
    terminal_ready = threading.Event()
    release_terminal = threading.Event()
    effect_ready = threading.Event()
    release_effect = threading.Event()
    s1 = s2 = k1 = k2 = None
    logs = []
    engine = None
    try:
        s1_port = r7j._free_port()
        s1 = await _start_child(
            tmp_path, "s1", s1_port, logs, postgres_url=postgres_url,
        )

        approvals_in_memory = []
        hitl = HITLManager()

        def decide_high_risk(request):
            approvals_in_memory.append(request)
            with sqlite3.connect(sink_path) as db:
                db.execute(
                    "INSERT INTO approvals(risk_level) VALUES (?)",
                    (request["risk_level"],),
                )
            return scenario != "deny_terminal"

        hitl.set_approval_callback(decide_high_risk)

        def one_external_effect(value: str, **kwargs):
            invocation_id = kwargs.get("invocation_id")
            assert invocation_id
            with sqlite3.connect(sink_path) as db:
                db.execute(
                    "INSERT INTO effects(invocation_id) VALUES (?)",
                    (invocation_id,),
                )
                count = int(db.execute(
                    "SELECT count(*) FROM effects"
                ).fetchone()[0])
            if scenario == "effect_before_terminal":
                # Side effect durable, client record still RUNNING.
                effect_ready.set()
                if not release_effect.wait(60):
                    raise TimeoutError("External-effect cut not released")
            return {"source": "k1-durable", "value": value, "effect_count": count}

        k1 = await _connect_held_k1(
            s1_port, _registry(one_external_effect), hitl,
            ClientInvocationLedger(ledger_path), terminal_ready,
            release_terminal,
        )
        _atomic_json(tmp_path / "s1-start.json", {"begin": True})
        if scenario == "effect_before_terminal":
            assert await asyncio.to_thread(effect_ready.wait, 20.0), (
                "K1 did not reach the post-effect/pre-terminal cut"
            )
        else:
            assert await asyncio.to_thread(terminal_ready.wait, 20.0), (
                "K1 did not commit the terminal result before the wire cut"
            )
        assert _sink_count(sink_path, "effects") == (
            0 if scenario == "deny_terminal" else 1
        )
        assert _sink_count(sink_path, "approvals") == 1
        assert len(approvals_in_memory) == 1
        assert approvals_in_memory[0]["risk_level"] == "HIGH"

        # S1 observes K1 disconnect, persists WAITING/checkpoint, yet K1's
        # result has *never* traversed the wire. Kill S1, not a graceful stop.
        await asyncio.to_thread(k1.realtime.close)
        waiting = await _await_file(tmp_path / "s1-waiting.json", timeout=25.0)
        assert waiting["pid"] == s1.pid
        assert waiting["execution_id"] == r7j.EXECUTION_ID
        original_invocation = waiting["invocation_id"]

        # The persisted client TERMINAL record is distinct from server SQL.
        record = k1.ledger.get(
            client_id=r7j.CLIENT_ID, principal_id=r7j.USER_ID,
            invocation_id=original_invocation,
        )
        assert record is not None
        if scenario == "effect_before_terminal":
            assert record.state is ClientInvocationLedgerState.RUNNING
            assert record.terminal_payload is None
        else:
            assert record.state is ClientInvocationLedgerState.TERMINAL
            assert record.terminal_type == (
                "error" if scenario == "deny_terminal" else "result"
            )
            if scenario == "deny_terminal":
                assert record.terminal_payload["code"] == "HITL_DENIED"
        killed_pid = await _hard_kill(s1)
        # Invalidate generation before allowing the blocked worker to finish.
        k1.dispatcher.shutdown()
        release_terminal.set()
        release_effect.set()
        k1 = None

        s2_port = r7j._free_port()
        s2 = await _start_child(
            tmp_path, "s2", s2_port, logs, postgres_url=postgres_url,
        )
        assert s2.pid != killed_pid

        def forbidden_second_effect(value: str, **kwargs):
            with sqlite3.connect(sink_path) as db:
                db.execute(
                    "INSERT INTO effects(invocation_id) VALUES (?)",
                    (kwargs.get("invocation_id") or "unexpected",),
                )
            return {"source": "UNSAFE_K2_REPLAY"}

        resumed_approvals = []
        resumed_hitl = HITLManager()
        resumed_hitl.set_approval_callback(
            lambda request: resumed_approvals.append(request) or False
        )
        k2 = ClientRuntime(
            f"http://127.0.0.1:{s2_port}",
            _registry(forbidden_second_effect),
            api_key="r7-j", client_id=r7j.CLIENT_ID,
            owner_id=r7j.USER_ID,
            invocation_ledger=ClientInvocationLedger(ledger_path),
            hitl=resumed_hitl,
        )
        await asyncio.to_thread(k2.start)

        # Read the SAME server SQL database from outside both processes.
        engine = (
            create_async_engine(postgres_url, pool_pre_ping=True)
            if postgres_url else
            create_async_engine(
                f"sqlite+aiosqlite:///{db_path.as_posix()}",
                connect_args={"timeout": 5},
            )
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        uow_factory = lambda: r7j._SqliteUow(sessions)
        store = DurableAgentStore(uow_factory)
        invocation_store = SqlCapabilityInvocationStore(uow_factory)

        async def verify_budget_snapshot():
            async with sessions() as session:
                task = await session.get(AgentTaskRecord, TASK_ID)
                budget = await session.get(TaskBudgetRecord, TASK_ID)
                reservations = (await session.execute(
                    select(TaskBudgetReservationRecord).where(
                        TaskBudgetReservationRecord.task_id == TASK_ID
                    )
                )).scalars().all()
                binding = await session.get(UserBudgetTaskBindingRecord, TASK_ID)
                bridges = (await session.execute(
                    select(UserBudgetDualAccountingReceiptRecord).where(
                        UserBudgetDualAccountingReceiptRecord.task_id == TASK_ID
                    )
                )).scalars().all()
                ubq_reservations = (await session.execute(
                    select(UserBudgetReservationRecord).where(
                        UserBudgetReservationRecord.owner_user_id == r7j.USER_ID
                    )
                )).scalars().all()
            assert task is not None and task.id == TASK_ID
            assert budget is not None and budget.state in {"OPEN", "CLOSED"}
            assert budget.used_executions == 1
            assert budget.used_tool_calls == 1
            assert budget.active_executions == 0
            assert budget.used_executions <= budget.max_total_executions
            assert budget.used_tool_calls <= budget.max_total_tool_calls
            kind_keys = [(item.kind, item.reservation_key) for item in reservations]
            assert len(kind_keys) == len(set(kind_keys))
            assert sum(item.kind == "NEW_EXECUTION" for item in reservations) == 1
            assert sum(item.kind == "TOOL_CALL" for item in reservations) == 1
            assert any(
                item.kind == "TOOL_CALL" and item.reservation_key == "call-r7j-1"
                for item in reservations
            )
            assert binding is not None and binding.owner_user_id == r7j.USER_ID
            tool_bridges = [
                receipt for receipt in bridges
                if receipt.task_budget_kind == "TOOL_CALL"
                and receipt.task_budget_reservation_key == "call-r7j-1"
            ]
            assert len(tool_bridges) == 1
            assert tool_bridges[0].mirror_dimension == "TOOL_CALL"
            assert len({
                item.bridge_receipt_id for item in bridges
            }) == len(bridges)
            matching_ubq = [
                row for row in ubq_reservations
                if row.reservation_id == tool_bridges[0].ubq_reservation_id
            ]
            assert len(matching_ubq) == 1
            assert matching_ubq[0].state == "SETTLED"
            assert matching_ubq[0].settled_amount_atomic == 1
            return {
                "task_status": task.status,
                "task_revision": task.revision,
                "budget_state": budget.state,
                "budget_revision": budget.revision,
                "used_executions": budget.used_executions,
                "active_executions": budget.active_executions,
                "used_tool_calls": budget.used_tool_calls,
                "reservations": sorted(kind_keys),
                "ubq_enrollment": binding.enrollment_version,
                "ubq_tool_bridge_count": len(tool_bridges),
                "ubq_tool_settled": matching_ubq[0].settled_amount_atomic,
            }

        async def completed():
            row = await store.load_execution(r7j.EXECUTION_ID)
            return row if row is not None and row.state == "COMPLETED" else None

        if scenario == "effect_before_terminal":
            budget_snapshot = await verify_budget_snapshot()
            # Actual WebSocket K2 must return reconciliation UNKNOWN.
            # A fail-closed planner MAY defer before creating a ResumeClaim;
            # requiring a claim here would punish the correct safe path.
            async def unknown_wire_seen():
                trace = (tmp_path / "s2.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                return (
                    '"type": "capability.reconciliation"' in trace
                    and '"status": "UNKNOWN"' in trace
                    and original_invocation in trace
                )

            await r7j._wait_async(unknown_wire_seen, timeout=35.0)
            async with sessions() as session:
                claims = (await session.execute(
                    select(AgentResumeClaimRecord).where(
                        AgentResumeClaimRecord.execution_id == r7j.EXECUTION_ID
                    )
                )).scalars().all()
            assert all(claim.state != "CREATED" for claim in claims)
            await asyncio.sleep(0.5)
            invocation = await invocation_store.get(original_invocation)
            attempts = await invocation_store.list_attempts(original_invocation)
            assert invocation is not None
            assert invocation.remote_outcome_state.value == "OUTCOME_UNKNOWN"
            assert invocation.state.value != "COMPLETED"
            assert [entry.attempt_number for entry in attempts] == [1]
            assert await store.load_committed_tool_result(
                r7j.EXECUTION_ID, "call-r7j-1",
            ) is None
            ledger = ClientInvocationLedger(ledger_path).get(
                client_id=r7j.CLIENT_ID, principal_id=r7j.USER_ID,
                invocation_id=original_invocation,
            )
            assert ledger is not None
            assert ledger.state is ClientInvocationLedgerState.RUNNING
            assert _sink_count(sink_path, "effects") == 1
            assert _sink_count(sink_path, "approvals") == 1
            assert resumed_approvals == []
            _atomic_json(tmp_path / "outcome-unknown.json", {
                "task_budget": budget_snapshot,
                "scenario": scenario,
                "backend": "postgresql" if postgres_url else "sqlite",
                "s1_pid": killed_pid, "s2_pid": s2.pid,
                "remote_state": invocation.remote_outcome_state.value,
                "ledger_state": ledger.state.value,
                "claim_states": [claim.state for claim in claims],
                "attempts": [entry.attempt_number for entry in attempts],
                "effects": _sink_count(sink_path, "effects"),
            })
            return

        row = await r7j._wait_async(completed, timeout=35.0)
        assert row.id == r7j.EXECUTION_ID
        budget_snapshot = await verify_budget_snapshot()
        result = await store.load_committed_tool_result(
            r7j.EXECUTION_ID, "call-r7j-1"
        )
        assert result is not None and result.commit_state == "COMMITTED"
        assert result.invocation_id == original_invocation
        if scenario == "deny_terminal":
            assert result.success is False
            assert result.error_code == "HITL_DENIED"
        else:
            assert result.success is True
            assert result.output == {
                "source": "k1-durable", "value": "r7-j", "effect_count": 1,
            }
        invocation = await invocation_store.get(original_invocation)
        attempts = await invocation_store.list_attempts(original_invocation)
        assert invocation is not None
        assert invocation.state.value == (
            "FAILED" if scenario == "deny_terminal" else "COMPLETED"
        )
        # A K2 reconciliation of an already TERMINAL client ledger does NOT
        # create a second execution attempt. K2 owns the resume binding,
        # while the one and only dispatch attempt still belongs to K1.
        assert [item.attempt_number for item in attempts] == [1]
        assert attempts[0].connection_id == r7j.K1
        assert row.bound_connection_id == k2.connection_id
        assert row.bound_client_id == r7j.CLIENT_ID
        async with sessions() as session:
            claims = (await session.execute(
                select(AgentResumeClaimRecord).where(
                    AgentResumeClaimRecord.execution_id == r7j.EXECUTION_ID
                )
            )).scalars().all()
        assert len(claims) == 1 and claims[0].state == "CONSUMED"
        assert claims[0].connection_id == k2.connection_id

        if postgres_url:
            async with engine.connect() as connection:
                version = int((await connection.exec_driver_sql(
                    "SHOW server_version_num"
                )).scalar_one())
                assert 170000 <= version < 180000, version
                database = (await connection.exec_driver_sql(
                    "SELECT current_database()"
                )).scalar_one()
            _atomic_json(tmp_path / "postgres17-result.json", {
                "backend": "postgresql",
                "scenario": scenario,
                "task_budget": budget_snapshot,
                "version_num": version,
                "database": database,
                "s1_pid": killed_pid,
                "s2_pid": s2.pid,
                "agent_state": row.state,
                "claim_state": claims[0].state,
                "attempts": [a.attempt_number for a in attempts],
                "effects": _sink_count(sink_path, "effects"),
                "approvals": _sink_count(sink_path, "approvals"),
                "k2_connection_id": k2.connection_id,
            })

        assert _sink_count(sink_path, "effects") == (
            0 if scenario == "deny_terminal" else 1
        )
        assert _sink_count(sink_path, "approvals") == 1
        assert resumed_approvals == []
        assert k2.pending_resume_tickets == ()
    finally:
        release_terminal.set()
        release_effect.set()
        if k2 is not None:
            await asyncio.to_thread(k2.stop)
        if k1 is not None:
            try:
                await asyncio.to_thread(k1.realtime.close)
            finally:
                k1.dispatcher.shutdown()
        if engine is not None:
            await engine.dispose()
        for process in (s1, s2):
            if process is not None and process.poll() is None:
                await _hard_kill(process)
        for handle in logs:
            handle.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r14_e_decided_high_risk_k1_terminal_recovers_after_real_s1_kill(
    tmp_path,
):
    """Preserve the original portable SQLite evidence unchanged."""
    await run_physical_hard_restart_witness(tmp_path)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r14_e_denied_terminal_reconciles_after_real_s1_kill(tmp_path):
    await run_physical_hard_restart_witness(tmp_path, scenario="deny_terminal")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_r14_e_effect_before_terminal_is_unknown_after_s1_kill(tmp_path):
    await run_physical_hard_restart_witness(
        tmp_path, scenario="effect_before_terminal",
    )


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--r14e-child":
    asyncio.run(_serve_child(sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])))
