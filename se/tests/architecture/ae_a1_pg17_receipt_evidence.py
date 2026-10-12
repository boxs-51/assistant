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
    AgentExecutionCheckpointRecord, AgentCheckpointPendingInvocationRecord,
    AgentToolCallRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.native_receipt_verifier import (
    NativeReceiptUnavailable, NATIVE_AGENT_CAPABILITIES,
)
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


@pytest.mark.asyncio
async def test_pg17_r7c_historical_committed_prefix_fresh_process(tmp_path):
    """Real PG17: old native COMMITTED + different active PENDING checkpoint."""
    engine = create_async_engine(_url(), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    eid, cid = "pg-r7-prefix-exec", "pg-r7-prefix-checkpoint"
    try:
        async with engine.begin() as conn:
            version = int((await conn.exec_driver_sql("SHOW server_version_num")).scalar_one())
            assert 170000 <= version < 180000
            for model in (
                AgentExecutionRecord, AgentIterationRecord,
                AgentExecutionCheckpointRecord, AgentCheckpointPendingInvocationRecord,
                AgentToolCallRecord, AgentToolResultRecord,
            ):
                await conn.run_sync(model.__table__.create, checkfirst=True)
        async with _Uow(sessions) as u:
            u.session.add(AgentExecutionRecord(
                id=eid, session_id="session-pg-r7", agent_id="agent",
                correlation_id="corr-pg-r7", state="WAITING", revision=2,
                wait_reason="CONNECTION", current_checkpoint_id=cid, request={},
            ))
            # All FK-bearing rows refer to this execution. ORM values use
            # raw string IDs, not relationship links: flush parent first.
            await u.session.flush()
            u.session.add(AgentIterationRecord(
                id="pg-prefix-old-iter", execution_id=eid, iteration=1,
                state="COMPLETED", tool_call_ids=["old-call"],
            ))
            u.session.add(AgentIterationRecord(
                id="pg-prefix-active-iter", execution_id=eid, iteration=2,
                state="WAITING_TOOL", tool_call_ids=["pending-call"],
            ))
            u.session.add(AgentExecutionCheckpointRecord(
                checkpoint_id=cid, execution_id=eid, execution_revision=2,
                session_id="session-pg-r7", iteration=2, wait_reason="CONNECTION",
                transcript_snapshot=[
                    {"role": "user", "content": "resume"},
                    {"role": "tool", "tool_call_id": "old-call", "content": "FORGED"},
                    {"role": "tool", "tool_call_id": "pending-call", "content": "ACTIVE"},
                ], metadata_json={},
            ))
            # Raw FK identity has no ORM relationship: force parent checkpoint
            # INSERT before the pending child, on both Linux/Windows PG17.
            await u.session.flush()
            u.session.add(AgentCheckpointPendingInvocationRecord(
                checkpoint_id=cid, ordinal=0, invocation_id="pending-inv",
                invocation_revision=1, tool_call_id="pending-call",
                capability_id="tool.remote", idempotency="IDEMPOTENT",
                observed_remote_outcome_state="OUTCOME_UNKNOWN",
            ))
            u.session.add(AgentToolCallRecord(
                id="pg-old-call-record", execution_id=eid,
                iteration_id="pg-prefix-old-iter", tool_call_id="old-call",
                invocation_id="old-inv", capability_id="terminal.run",
                arguments={}, status="COMPLETED",
            ))
            u.session.add(AgentToolResultRecord(
                id="pg-old-result", execution_id=eid,
                iteration_id="pg-prefix-old-iter", tool_call_id="old-call",
                invocation_id="old-inv", capability_id="terminal.run",
                success=True, output={"legacy_native_success": True},
                extra_metadata={"verified_empty": True,
                                "native_receipt": {"signed": "FORGED"}},
                retryable=False, commit_state="COMMITTED",
            ))
            await u.commit()
        store = DurableAgentStore(lambda: _Uow(sessions))
        for capability in sorted(NATIVE_AGENT_CAPABILITIES):
            async with _Uow(sessions) as u:
                old = await u.agents.get_tool_result(eid, "old-call")
                old.capability_id = capability
                await u.commit()
            with pytest.raises(NativeReceiptUnavailable):
                await store.load_committed_checkpoint_transcript(
                    eid, cid, active_tool_call_ids=("pending-call",),
                )

        # Fresh OS reader and connection must reject the same persisted row.
        root = Path(__file__).resolve().parents[3]
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
        if t: await self.session.rollback()
        await self.session.close()
    async def commit(self): await self.session.commit()
async def main():
    e=create_async_engine(os.environ['AE_A1_POSTGRES_URL'])
    try:
        store=DurableAgentStore(lambda:U(async_sessionmaker(e,expire_on_commit=False)))
        try:
            await store.load_committed_checkpoint_transcript(
                'pg-r7-prefix-exec','pg-r7-prefix-checkpoint',
                active_tool_call_ids=('pending-call',))
        except NativeReceiptUnavailable: return 0
        return 77
    finally: await e.dispose()
raise SystemExit(asyncio.run(main()))
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            x for x in (str(root), env.get("PYTHONPATH", "")) if x
        )
        with subprocess.Popen(
            [sys.executable, "-c", script], env=env, cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as child:
            reader_pid = child.pid
            assert isinstance(reader_pid, int) and reader_pid != os.getpid()
            try:
                _, stderr = child.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                _, stderr = child.communicate()
                raise AssertionError("PG17 reader timeout: " + stderr[-2500:])
            assert child.returncode == 0, stderr[-2500:]

        async with _Uow(sessions) as u:
            old = await u.agents.get_tool_result(eid, "old-call")
            assert old.commit_state == "COMMITTED"
            assert old.output == {"legacy_native_success": True}
            old.capability_id = "tool.remote"
            await u.commit()
        prefix = await store.load_committed_checkpoint_transcript(
            eid, cid, active_tool_call_ids=("pending-call",))
        assert [m["role"] for m in prefix] == ["user", "tool"]
        assert prefix[1]["content"] == {"legacy_native_success": True}
        assert prefix[1]["metadata"]["success"] is True
        assert "ACTIVE" not in repr(prefix)

        async with _Uow(sessions) as u:
            old = await u.agents.get_tool_result(eid, "old-call")
            old.capability_id = "terminal.run"
            old.success = False
            old.output = None
            old.error_code = "OUTCOME_UNKNOWN"
            await u.commit()
        prefix = await store.load_committed_checkpoint_transcript(
            eid, cid, active_tool_call_ids=("pending-call",))
        assert prefix[1]["metadata"]["success"] is False
        assert prefix[1]["content"]["error_code"] == "OUTCOME_UNKNOWN"

        (tmp_path / "ae-a1-pg17-r7c-prefix.json").write_text(
            json.dumps({
                "server_version_num": version,
                "database": "ae_a1_receipt",
                "fresh_prefix_reader_os_pid": reader_pid,
                "historical_native_capabilities_denied": sorted(NATIVE_AGENT_CAPABILITIES),
                "prefix_native_success_after_restart": "DENIED",
                "active_pending_remained_excluded": True,
                "nonnative_historical_success": "PASS",
                "native_unknown_remained_failure": True,
                "legacy_sql_row_preserved": True,
                "native_command_activated": False,
            }, sort_keys=True), encoding="utf-8",
        )
    finally:
        await engine.dispose()
