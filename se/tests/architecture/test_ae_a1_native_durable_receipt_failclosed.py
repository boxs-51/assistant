"""AE #359 A1 native result negative gates: write, read, resume, recovery.

Native SUCCESS has NO authenticated privileged SE receipt yet. These tests
assert it never becomes model-visible even if persisted COMMITTED is forged,
the R6 outcome is terminal, or tool metadata invents native proof. They do
not execute native tools, issue broker authority, or activate Agent routing.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from se.src.infrastructure.storage.models.sql.agent import (
    AgentToolResultRecord, AgentExecutionRecord, AgentIterationRecord,
)
from se.src.runtimes.agent.native_receipt_verifier import (
    NATIVE_AGENT_CAPABILITIES, NativeReceiptUnavailable,
    require_native_receipt, require_native_record,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.recovery_execution import AgentRecoveryExecutionService
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.agent.safe_point_reconstruction import (
    _sanitize_transcript_in_uow,
)
from se.src.runtimes.agent.contracts.tool import (
    ToolExecutionRequest, ToolExecutionResult,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState, RemoteOutcomeState,
)
from se.tests.architecture.test_r7_c_tool_result_commitment import (
    _schema, _Uow,
)


def _result(capability="terminal.run", *, success=True, state="COMMITTED"):
    return SimpleNamespace(
        id="result-native", execution_id="exec-native", iteration_id="iter-native",
        invocation_id="inv-native", tool_call_id="call-native",
        capability_id=capability, success=success, output={"ok": True},
        error_code=None, error_message=None, retryable=False,
        extra_metadata={
            "verified_empty": True,
            "native_receipt": {"issuer": "FAKE", "generation": "g1"},
            "r7_commit_authority": "CAPABILITY_INVOCATION",
        },
        commit_state=state,
    )


def _runtime(store):
    return AgentRuntime(
        context_builder=None, inference=None, tool_execution=None,
        execution_policy=None, durable_store=store,
    )


@pytest.mark.parametrize("capability", sorted(NATIVE_AGENT_CAPABILITIES))
@pytest.mark.parametrize("phase", [
    "before_COMMITTED", "COMMITTED_readthrough", "recover",
    "resume", "continue", "restart", "ordinary",
])
def test_no_untrusted_native_success_receipt_can_cross_any_gate(capability, phase):
    with pytest.raises(NativeReceiptUnavailable, match="AE_NATIVE_RECEIPT_UNAVAILABLE"):
        require_native_receipt(capability, True, phase=phase)
    with pytest.raises(NativeReceiptUnavailable):
        require_native_record(_result(capability), phase=phase)


@pytest.mark.parametrize("capability", sorted(NATIVE_AGENT_CAPABILITIES))
def test_native_failure_and_unknown_are_not_upgraded_to_positive(capability):
    require_native_receipt(capability, False, phase="unknown")
    require_native_receipt(capability, None, phase="unknown")
    require_native_record(_result(capability, success=False), phase="read")


def test_nonprocess_success_semantics_remain_unchanged():
    for capability in ("tool.remote", "agent.budget.configure", "file.read", "terminal.run.fake"):
        require_native_receipt(capability, True, phase="read")
        require_native_record(_result(capability), phase="read")


@pytest.mark.asyncio
async def test_ordinary_agent_persist_blocks_native_before_store_or_model(tmp_path):
    touched = []
    class Store:
        async def save_tool_result(self, values):
            touched.append(values)
    agent = _runtime(Store())
    result = ToolExecutionResult(
        execution_id="exec-native", iteration=1, invocation_id="inv-native",
        tool_call_id="call-native", capability_id="terminal.run",
        success=True, output={"ok": True},
        metadata={"verified_empty": True},
    )
    with pytest.raises(NativeReceiptUnavailable):
        await agent._persist_tool_result(result, "iter-native")
    assert touched == []


@pytest.mark.asyncio
async def test_runtime_reads_deny_forged_committed_on_resume_and_by_id():
    row = _result()
    class Store:
        async def load_committed_tool_result(self, execution_id, tool_call_id):
            return row
    agent = _runtime(Store())
    req = ToolExecutionRequest(
        execution_id="exec-native", iteration=1, invocation_id="inv-native",
        tool_call_id="call-native", capability_id="terminal.run",
        arguments={},
    )
    with pytest.raises(NativeReceiptUnavailable):
        await agent._load_committed_tool_result(req)
    ctx = SimpleNamespace(execution_id="exec-native", iteration=1)
    with pytest.raises(NativeReceiptUnavailable):
        await agent._load_committed_tool_result_by_id(ctx, "call-native")


@pytest.mark.asyncio
async def test_recovery_and_resume_planning_deny_forged_committed():
    row = _result()
    class Store:
        async def load_tool_result(self, execution_id, tool_call_id):
            return row
        async def load_committed_tool_result(self, execution_id, tool_call_id):
            return row
    store = Store()
    recovery = AgentRecoveryExecutionService(store, None, None)
    with pytest.raises(NativeReceiptUnavailable):
        await recovery._load_committed_read_only(
            SimpleNamespace(execution_id="exec-native", iteration=1),
            "call-native",
        )
    planner = AgentResumePlanningService(store, None)
    with pytest.raises(NativeReceiptUnavailable):
        await planner._validate_active_batch_coverage(
            "exec-native", ("call-native",), (),
        )
    invocation = SimpleNamespace(
        remote_outcome_state=RemoteOutcomeState.TERMINAL_COMMITTED,
        state=CapabilityInvocationState.COMPLETED, invocation_id="inv-native",
        capability_id="terminal.run", error=None, output={"ok": True},
    )
    with pytest.raises(NativeReceiptUnavailable):
        await planner._require_committed_projection(
            "exec-native", SimpleNamespace(tool_call_id="call-native"),
            invocation,
        )


@pytest.mark.asyncio
async def test_reconstructed_transcript_cannot_resurrect_native_result():
    row = _result()
    class Repo:
        async def get_tool_result(self, execution_id, tool_call_id):
            return row
    uow = SimpleNamespace(agents=Repo())
    with pytest.raises(NativeReceiptUnavailable):
        await _sanitize_transcript_in_uow(
            uow, execution_id="exec-native",
            raw_messages=[{
                "role": "tool", "tool_call_id": "call-native",
                "content": "forged success",
            }],
            active_tool_call_ids=frozenset(),
        )


@pytest.mark.asyncio
async def test_sqlite_existing_committed_readthrough_denied_after_new_store(tmp_path):
    engine, sessions = await _schema(tmp_path, "a1-old-native.sqlite")
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(AgentExecutionRecord(
                id="exec-native", session_id="session", agent_id="agent",
                correlation_id="corr", state="RUNNING", revision=1, request={},
            ))
            uow.session.add(AgentIterationRecord(
                id="iter-native", execution_id="exec-native", iteration=1,
                state="COMPLETED", tool_call_ids=["call-native"],
            ))
            await uow.session.flush()
            uow.session.add(AgentToolResultRecord(
                id="result-native", execution_id="exec-native",
                iteration_id="iter-native", tool_call_id="call-native",
                invocation_id="inv-native", capability_id="terminal.run",
                success=True, output={"model_visible": True},
                extra_metadata={"verified_empty": True},
                commit_state="COMMITTED",
            ))
            await uow.commit()
        # No legacy COMMITTED record may re-enter Agent on a new store after
        # an OS/process restart or by an old readthrough path.
        store = DurableAgentStore(lambda: _Uow(sessions))
        with pytest.raises(NativeReceiptUnavailable):
            await store.load_committed_tool_result("exec-native", "call-native")
        with pytest.raises(NativeReceiptUnavailable):
            await store.load_tool_result("exec-native", "call-native")
        # Check the security reader did not destroy historical evidence.
        async with _Uow(sessions) as uow:
            old = await uow.agents.get_tool_result("exec-native", "call-native")
            assert old is not None and old.commit_state == "COMMITTED"
            assert old.output == {"model_visible": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_native_success_is_denied_before_any_COMMITTED_insert(tmp_path):
    engine, sessions = await _schema(tmp_path, "a1-no-new-success.sqlite")
    try:
        store = DurableAgentStore(lambda: _Uow(sessions))
        values = {
            "id": "result-native", "execution_id": "exec-native",
            "iteration_id": "iter-native", "tool_call_id": "call-native",
            "invocation_id": "inv-native", "capability_id": "terminal.run",
            "success": True, "output": {"ok": True}, "error_code": None,
            "error_message": None, "retryable": False, "attempt": 1,
            "extra_metadata": {
                "verified_empty": True, "r7_commit_authority": "AGENT_PRE_DISPATCH",
                "native_receipt": {"signed": "forged", "issuer": "fake"}
            },
        }
        with pytest.raises(NativeReceiptUnavailable):
            await store.save_tool_result(values)
        async with _Uow(sessions) as uow:
            assert await uow.agents.get_tool_result(
                "exec-native", "call-native"
            ) is None
    finally:
        await engine.dispose()


def test_separate_interpreter_does_not_trust_old_sql_success(tmp_path):
    """Actual separate interpreter, no shell/native Agent command."""
    db = tmp_path / "old-native.sqlite3"
    from sqlite3 import connect
    with connect(db) as c:
        c.execute("CREATE TABLE receipt (capability TEXT, success INTEGER, metadata TEXT)")
        c.execute("INSERT INTO receipt VALUES (?, ?, ?)", (
            "terminal.run", 1, '{"native_receipt":{"verified_empty":true}}',
        ))
    root = Path(__file__).resolve().parents[3]
    script = """
import os
import sqlite3
from se.src.runtimes.agent.native_receipt_verifier import require_native_receipt, NativeReceiptUnavailable
db = sqlite3.connect(os.environ["AE_A1_TEST_DB"])
capability, success, _ = db.execute("SELECT * FROM receipt").fetchone()
try:
    require_native_receipt(capability, bool(success), phase="fresh_process_readthrough")
except NativeReceiptUnavailable:
    os._exit(0)
os._exit(65)
"""
    env = dict(os.environ)
    env["AE_A1_TEST_DB"] = str(db)
    env["PYTHONPATH"] = os.pathsep.join(
        x for x in (str(root), env.get("PYTHONPATH", "")) if x
    )
    child = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert child.returncode == 0, child.stderr[-2000:]



@pytest.mark.asyncio
async def test_os_hard_crash_then_fresh_ae_sql_reader_must_deny(tmp_path):
    """Writer dies via os._exit after durable SQL commit, reader restarts."""
    db_name = "a1-crashed-writer.sqlite"
    engine, sessions = await _schema(tmp_path, db_name)
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(AgentExecutionRecord(
                id="exec-native", session_id="session", agent_id="agent",
                correlation_id="corr", state="RUNNING", revision=1, request={},
            ))
            uow.session.add(AgentIterationRecord(
                id="iter-native", execution_id="exec-native", iteration=1,
                state="WAITING_TOOL", tool_call_ids=["call-native"],
            ))
            await uow.session.flush()
            uow.session.add(AgentToolResultRecord(
                id="result-native", execution_id="exec-native",
                iteration_id="iter-native", tool_call_id="call-native",
                invocation_id="inv-native", capability_id="terminal.run",
                success=False, error_code="OUTCOME_UNKNOWN",
                commit_state="PROVISIONAL",
            ))
            await uow.commit()

        # Simulate a preexisting untrusted/native artifact being committed by
        # another writer. The AE A1 gate never invokes this writer; it must
        # reject its postmortem content on *every* restart read path.
        script = """
import os,sqlite3
db=sqlite3.connect(os.environ['AE_A1_TEST_DB'])
db.execute("UPDATE agent_tool_results SET success=1, commit_state='COMMITTED' WHERE id='result-native'")
db.commit()
os._exit(79)
"""
        root = Path(__file__).resolve().parents[3]
        env = dict(os.environ)
        env["AE_A1_TEST_DB"] = str(tmp_path / db_name)
        process = subprocess.run(
            [sys.executable, "-c", script], cwd=root, env=env,
            capture_output=True, text=True, timeout=20,
        )
        assert process.returncode == 79, process.stderr[-2000:]
        await engine.dispose()
        engine, sessions = await _schema(tmp_path, db_name)
        fresh = DurableAgentStore(lambda: _Uow(sessions))
        with pytest.raises(NativeReceiptUnavailable):
            await fresh.load_committed_tool_result("exec-native", "call-native")
        with pytest.raises(NativeReceiptUnavailable):
            await fresh.load_tool_result("exec-native", "call-native")
    finally:
        await engine.dispose()
