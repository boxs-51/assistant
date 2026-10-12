"""Explicit PostgreSQL 17 A1 receipt negative gate, native Linux/Windows only.

Not named test_*.py: ordinary Architecture full suite has no PG dependency.
The dedicated PG17 CI must supply a real database and must not skip/xfail.
No Agent-native process is launched.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord, AgentIterationRecord, AgentToolResultRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.native_receipt_verifier import NativeReceiptUnavailable
from se.src.runtimes.agent.persistence import DurableAgentStore


class _Uow:
    """Native-test-only UoW deliberately lacks R6: formerly COMMITTED bypass."""

    def __init__(self, sessions):
        self.sessions = sessions
        self.session = None

    async def __aenter__(self):
        self.session = self.sessions()
        self.agents = AgentRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()


def _url() -> str:
    value = os.environ.get("AE_A1_POSTGRES_URL", "")
    assert value.startswith("postgresql+asyncpg://"), (
        "Real PostgreSQL+asyncpg DSN required, no SQLite fallback"
    )
    assert value.endswith("/ae_a1_receipt"), "Unexpected shared database"
    return value


@pytest.mark.asyncio
async def test_real_pg17_blocks_native_commit_and_fresh_process_readthrough(tmp_path):
    url = _url()
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            version = int((await conn.exec_driver_sql("SHOW server_version_num")).scalar_one())
            assert 170000 <= version < 180000, version
            for model in (AgentExecutionRecord, AgentIterationRecord, AgentToolResultRecord):
                await conn.run_sync(model.__table__.create, checkfirst=True)

        async with _Uow(sessions) as uow:
            uow.session.add(AgentExecutionRecord(
                id="exec-a1", session_id="session", agent_id="agent",
                correlation_id="corr", state="RUNNING", revision=1, request={},
            ))
            uow.session.add(AgentIterationRecord(
                id="iter-a1", execution_id="exec-a1", iteration=1,
                state="COMPLETED", tool_call_ids=["call-a1"],
            ))
            await uow.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        forged = {
            "id": "forged-native", "execution_id": "exec-a1",
            "iteration_id": "iter-a1", "tool_call_id": "call-a1",
            "invocation_id": "inv-a1", "capability_id": "terminal.run",
            "success": True, "output": {"ok": True},
            "extra_metadata": {
                "verified_empty": True, "native_receipt": {"signed": "FAKE"},
                "r7_commit_authority": "AGENT_PRE_DISPATCH",
            },
        }
        with pytest.raises(NativeReceiptUnavailable):
            await store.save_tool_result(forged)
        async with _Uow(sessions) as uow:
            assert await uow.agents.get_tool_result("exec-a1", "call-a1") is None
            uow.session.add(AgentToolResultRecord(
                id="legacy-native", execution_id="exec-a1",
                iteration_id="iter-a1", tool_call_id="call-a1",
                invocation_id="inv-a1", capability_id="terminal.run",
                success=True, output={"ok": True}, commit_state="COMMITTED",
                extra_metadata={"verified_empty": True},
            ))
            await uow.commit()

        with pytest.raises(NativeReceiptUnavailable):
            await store.load_committed_tool_result("exec-a1", "call-a1")
        with pytest.raises(NativeReceiptUnavailable):
            await store.load_tool_result("exec-a1", "call-a1")

        # Hard restart of the AE reader: a DIFFERENT process, connection pool
        # and SQL transaction must reject exactly the original COMMITTED row.
        repo_root = Path(__file__).resolve().parents[3]
        script = """
import asyncio,os
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.native_receipt_verifier import NativeReceiptUnavailable
from se.src.runtimes.agent.persistence import DurableAgentStore
class U:
    def __init__(self,s): self.s=s
    async def __aenter__(self):
        self.session=self.s()
        self.agents=AgentRepository(self.session)
        return self
    async def __aexit__(self,t,e,b):
        if t is not None: await self.session.rollback()
        await self.session.close()
    async def commit(self): await self.session.commit()
async def main():
    engine=create_async_engine(os.environ['AE_A1_POSTGRES_URL'],pool_pre_ping=True)
    try:
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        store=DurableAgentStore(lambda:U(sessions))
        try: await store.load_committed_tool_result('exec-a1','call-a1')
        except NativeReceiptUnavailable: return 0
        return 75
    finally: await engine.dispose()
raise SystemExit(asyncio.run(main()))
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            x for x in (str(repo_root), env.get("PYTHONPATH", "")) if x
        )
        # Popen exposes the real PID; CompletedProcess from subprocess.run
        # does not. Preserve OS PID in the artifact as crash/readthrough proof.
        with subprocess.Popen(
            [sys.executable, "-c", script], env=env, cwd=repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as child:
            reader_pid = child.pid
            assert isinstance(reader_pid, int) and reader_pid > 0
            assert reader_pid != os.getpid(), "Reader must use a new OS process"
            try:
                _, child_stderr = child.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                _, child_stderr = child.communicate()
                raise AssertionError(
                    f"AE reader subprocess timed out: {child_stderr[-2500:]}"
                )
            assert child.returncode == 0, child_stderr[-2500:]
        async with _Uow(sessions) as uow:
            preserved = await uow.agents.get_tool_result("exec-a1", "call-a1")
            assert preserved is not None and preserved.commit_state == "COMMITTED"
        (tmp_path / "ae-a1-pg17-negative-receipt.json").write_text(
            json.dumps({
                "server_version_num": version,
                "database": "ae_a1_receipt",
                "reader_os_pid": reader_pid,
                "native_success_before_commit": "DENIED",
                "legacy_committed_recovered": "DENIED",
                "legacy_sql_row_preserved": True,
                "native_command_activated": False,
            }, sort_keys=True),
            encoding="utf-8",
        )
    finally:
        await engine.dispose()
