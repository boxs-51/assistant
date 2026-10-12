"""D2 fail-closed tests. NO fabricated VERIFIED_EMPTY/OS positive proof.

All fake tree receipts in this file are for MANAGER CONTROL FLOW ONLY, not
kernel/native certification. Test process crash uses os._exit in a real child.
Linux cgroup-v2 native-positive proof still requires host delegation and a
trusted pre-exec launcher; Windows native pre-spawn remains denied.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.sandbox import (
    SandboxLeaseStateError,
    SandboxManager,
)
from se.src.runtimes.capability.contracts.sandbox import (
    SandboxLeaseState,
    SandboxProfile,
)
from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity
from se.src.runtimes.capability.native_tree_recovery_b0 import (
    LinuxCgroupTreeAuthority,
    NativeTreeHold,
    require_no_uncertified_agent_success,
)
from se.src.runtimes.agent.contracts.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
)
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_execution import AgentRecoveryExecutionService
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService


def _identity(tag: str = "d2") -> SpawnIdentity:
    return SpawnIdentity(
        owner_id="owner-" + tag,
        execution_id="exec-" + tag,
        tool_call_id="call-" + tag,
        sandbox_id="sandbox-" + tag,
        lease_generation="gen-1",
    )


@pytest.mark.parametrize("capability", ["terminal.run", "terminal.launch", "python.run"])
def test_no_untrusted_success_without_native_receipt(capability):
    with pytest.raises(NativeTreeHold, match="NATIVE_RECEIPT_UNAVAILABLE"):
        require_no_uncertified_agent_success(capability, True)
    require_no_uncertified_agent_success(capability, False)
    require_no_uncertified_agent_success("file.read", True)


def test_cgroup_fake_files_and_foreign_refs_cannot_mint_verified_empty(tmp_path):
    root = tmp_path / "fake-cgroup"
    cg = root / _identity().sandbox_id
    cg.mkdir(parents=True)
    for name, value in (
        ("cgroup.events", "populated 0\nfrozen 1\n"),
        ("cgroup.procs", ""),
        ("cgroup.freeze", "1"),
        ("cgroup.kill", ""),
    ):
        (cg / name).write_text(value, encoding="utf-8")
    journal = tmp_path / "native-ledger.sqlite"
    authority = LinuxCgroupTreeAuthority(journal, delegated_root=root)
    with pytest.raises(NativeTreeHold):
        authority.register(_identity(), cg)
    with pytest.raises(NativeTreeHold):
        authority.require_verified_empty(_identity())


def test_real_crash_recovery_cannot_fabricate_tree_receipt(tmp_path):
    journal = tmp_path / "journal.sqlite"
    script = (
        "import os\n"
        "from se.src.runtimes.capability.native_tree_recovery_b0 "
        "import LinuxCgroupTreeAuthority\n"
        "LinuxCgroupTreeAuthority(" + repr(str(journal)) + ")\n"
        "os._exit(67)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path.cwd()),
        timeout=20,
        check=False,
    )
    assert child.returncode == 67
    recovered = LinuxCgroupTreeAuthority(journal)
    with pytest.raises(NativeTreeHold, match="missing/stale/foreign"):
        recovered.require_verified_empty(_identity())


class _TestTreeGate:
    """Only verifies manager call ordering; never an OS security witness."""
    def __init__(self):
        self.allowed = False
        self.reads = 0

    def require_registered(self, identity):
        assert identity == _identity()

    def require_verified_empty(self, identity):
        self.reads += 1
        assert identity == _identity()
        if not self.allowed:
            raise NativeTreeHold("unknown real OS state")


def test_manager_native_root_quarantined_until_authority_readback(tmp_path):
    gate = _TestTreeGate()
    manager = SandboxManager(
        (tmp_path / "owned-roots").resolve(), native_tree_authority=gate
    )
    identity = _identity()
    lease = manager.acquire_for_execution(
        execution_id=identity.execution_id,
        owner_user_id=identity.owner_id,
        profile=SandboxProfile(profile_id="native-b0-d2"),
    )
    manager.bind_native_tree(lease, identity)
    marker = manager.resolve_path(lease, "proof.txt")
    marker.write_text("root must remain", encoding="utf-8")

    with pytest.raises(SandboxLeaseStateError, match="VERIFIED_EMPTY"):
        manager.destroy(lease)
    assert gate.reads == 1
    assert marker.read_text(encoding="utf-8") == "root must remain"
    assert manager.current(lease).state is SandboxLeaseState.CLEANUP
    with pytest.raises(SandboxLeaseStateError):
        manager.resolve_path(lease, "no-new-write")
    with pytest.raises(SandboxLeaseStateError, match="VERIFIED_EMPTY"):
        manager.release_execution(identity.execution_id, owner_user_id=identity.owner_id)

    # CONTROL-FLOW mock only; production proof always comes from a kernel
    # LinuxCgroupTreeAuthority, never from this illustrative gate.
    gate.allowed = True
    destroyed = manager.destroy(lease)
    assert destroyed.state is SandboxLeaseState.DESTROYED
    assert not lease.root.exists()


def test_manager_rejects_foreign_native_binding_before_spawn(tmp_path):
    gate = _TestTreeGate()
    manager = SandboxManager(
        (tmp_path / "owned-roots").resolve(), native_tree_authority=gate
    )
    lease = manager.acquire(
        execution_id="exec-d2",
        owner_user_id="owner-d2",
        profile=SandboxProfile(profile_id="native-b0-d2"),
    )
    foreign = SpawnIdentity(
        owner_id="other-owner",
        execution_id="exec-d2",
        tool_call_id="call-d2",
        sandbox_id=lease.sandbox_id,
        lease_generation="gen-1",
    )
    with pytest.raises(SandboxLeaseStateError, match="identity mismatch"):
        manager.bind_native_tree(lease, foreign)
    assert manager.destroy(lease).state is SandboxLeaseState.DESTROYED


def test_agent_origin_crt_bypass_denied_before_any_runtime_driver():
    runtime = CapabilityRuntime()
    identity = Identity(user_id="d2-owner", auth_type="jwt")
    async def scenario():
        await runtime.execute_capability(
            capability_id="terminal.run",
            arguments={"command": "DO_NOT_EXECUTE"},
            identity=identity,
            execution_id="d2-agent-execution",
            caller_agent_execution_id="d2-agent-execution",
        )
    with pytest.raises(Exception) as exc:
        asyncio.run(scenario())
    assert getattr(exc.value, "code", None) == "SBX3_NATIVE_RECEIPT_UNAVAILABLE"


def test_store_rejects_synthetic_native_success_before_uow_entry():
    async def scenario():
        return await DurableAgentStore.save_tool_result(
            SimpleNamespace(uow_factory=None),
            {
                "execution_id": "exec-d2",
                "tool_call_id": "call-d2",
                "capability_id": "terminal.run",
                "success": True,
            },
        )
    with pytest.raises(NativeTreeHold):
        asyncio.run(scenario())


def test_ae_result_write_rejected_even_when_store_absent():
    result = ToolExecutionResult(
        execution_id="exec-d2", iteration=0,
        invocation_id="inv-d2", tool_call_id="call-d2",
        capability_id="terminal.launch", success=True,
    )
    with pytest.raises(NativeTreeHold):
        asyncio.run(
            AgentRuntime._persist_tool_result(
                SimpleNamespace(_durable_store=None),
                result,
                "iteration-d2",
            )
        )


def test_ae_committed_byid_reuse_and_recovery_reader_rejected():
    record = SimpleNamespace(
        id="d2-row", commit_state="COMMITTED",
        execution_id="exec-d2", tool_call_id="call-d2",
        invocation_id="inv-d2", capability_id="terminal.run",
        success=True, output={"exit_code": 0},
    )
    store = SimpleNamespace(
        load_committed_tool_result=AsyncMock(return_value=record),
        load_tool_result=AsyncMock(return_value=record),
    )
    ctx = SimpleNamespace(execution_id="exec-d2", iteration=0)
    with pytest.raises(NativeTreeHold):
        asyncio.run(
            AgentRuntime._load_committed_tool_result_by_id(
                SimpleNamespace(_durable_store=store), ctx, "call-d2"
            )
        )
    with pytest.raises(NativeTreeHold):
        asyncio.run(
            AgentRecoveryExecutionService._load_committed_read_only(
                SimpleNamespace(_store=store),
                SimpleNamespace(execution_id="exec-d2", iteration=0),
                "call-d2",
            )
        )


def test_r7_planning_coverage_cannot_reuse_native_committed_result():
    record = SimpleNamespace(capability_id="terminal.run", success=True)
    store = SimpleNamespace(
        load_committed_tool_result=AsyncMock(return_value=record)
    )
    with pytest.raises(NativeTreeHold):
        asyncio.run(
            AgentResumePlanningService._validate_active_batch_coverage(
                SimpleNamespace(_store=store),
                "exec-d2",
                ("call-d2",),
                (),
            )
        )
