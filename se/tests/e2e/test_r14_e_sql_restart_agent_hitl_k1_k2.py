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
from se.src.infrastructure.storage.models.sql.agent import AgentResumeClaimRecord
from se.src.infrastructure.storage.models.sql.chat_data.session import Session as ChatSessionRecord
from se.src.infrastructure.storage.models.sql.user_data.user import User
from se.src.infrastructure.storage.repositories.capability_invocations import SqlCapabilityInvocationStore
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.persistence import DurableAgentStore
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
):
    """Verify real S1 hard kill/S2 recovery using SQLite or actual PostgreSQL."""
    db_path = tmp_path / "server.sqlite3"
    sink_path = tmp_path / "independent_effects.sqlite3"
    ledger_path = tmp_path / "client.sqlite3"
    _sink_init(sink_path)
    terminal_ready = threading.Event()
    release_terminal = threading.Event()
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

        def approve_high_risk(request):
            approvals_in_memory.append(request)
            with sqlite3.connect(sink_path) as db:
                db.execute(
                    "INSERT INTO approvals(risk_level) VALUES (?)",
                    (request["risk_level"],),
                )
            return True

        hitl.set_approval_callback(approve_high_risk)

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
            return {"source": "k1-durable", "value": value, "effect_count": count}

        k1 = await _connect_held_k1(
            s1_port, _registry(one_external_effect), hitl,
            ClientInvocationLedger(ledger_path), terminal_ready,
            release_terminal,
        )
        _atomic_json(tmp_path / "s1-start.json", {"begin": True})
        assert await asyncio.to_thread(terminal_ready.wait, 20.0), (
            "K1 never reached the after-effect/before-wire cut"
        )
        assert _sink_count(sink_path, "effects") == 1
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
        assert record.state is ClientInvocationLedgerState.TERMINAL
        killed_pid = await _hard_kill(s1)
        release_terminal.set()
        k1.dispatcher.shutdown()
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

        async def completed():
            row = await store.load_execution(r7j.EXECUTION_ID)
            return row if row is not None and row.state == "COMPLETED" else None

        row = await r7j._wait_async(completed, timeout=35.0)
        assert row.id == r7j.EXECUTION_ID
        result = await store.load_committed_tool_result(
            r7j.EXECUTION_ID, "call-r7j-1"
        )
        assert result is not None and result.commit_state == "COMMITTED"
        assert result.invocation_id == original_invocation
        assert result.success is True
        assert result.output == {
            "source": "k1-durable", "value": "r7-j", "effect_count": 1,
        }
        invocation = await invocation_store.get(original_invocation)
        attempts = await invocation_store.list_attempts(original_invocation)
        assert invocation is not None and invocation.state.value == "COMPLETED"
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

        assert _sink_count(sink_path, "effects") == 1
        assert _sink_count(sink_path, "approvals") == 1
        assert resumed_approvals == []
        assert k2.pending_resume_tickets == ()
    finally:
        release_terminal.set()
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


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--r14e-child":
    asyncio.run(_serve_child(sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])))
