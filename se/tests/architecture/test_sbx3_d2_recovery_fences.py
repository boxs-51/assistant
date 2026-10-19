"""D2 fail-closed tests. NO fabricated VERIFIED_EMPTY/OS positive proof.

No fabricated user receipt or mock OS readback authorizes native cleanup.
Test process crash uses os._exit in a real child; marker and root are physical.
Linux cgroup-v2 native-positive proof still requires host delegation and a
trusted pre-exec launcher; Windows native pre-spawn remains denied.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.sandbox import (
    SandboxError,
    SandboxLeaseNotFoundError,
    SandboxLeaseStateError,
    NativeTreeUnverifiedError,
    SandboxManager,
)
from se.src.runtimes.capability.contracts.sandbox import (
    SandboxLeaseState,
    SandboxProfile,
)
from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity
from se.src.runtimes.capability.native_tree_cgroup_b0 import (
    CgroupTreeIdentity, CgroupTreeMismatch, LinuxCgroupTreeLedger,
    VerifiedEmptyWitness,
)
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


def test_real_linux_orphan_survives_worker_crash_but_cannot_be_attested(tmp_path):
    """Real OS process survives worker crash; missing cgroup ref stays HOLD.

    This is deliberately a negative reattachment test, NOT OS tree cleanup
    certification. The test owns and kills its harmless sleeping child.
    """
    if sys.platform != "linux":
        return
    marker = tmp_path / "orphan.pid"
    script = (
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "p = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(6)'], "
        "start_new_session=True, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "Path(" + repr(str(marker)) + ").write_text(str(p.pid))\n"
        "os._exit(83)\n"
    )
    worker = subprocess.run(
        [sys.executable, "-c", script],
        check=False, timeout=15,
    )
    assert worker.returncode == 83
    orphan_pid = int(marker.read_text(encoding="utf-8"))
    try:
        os.kill(orphan_pid, 0)  # real live OS process after worker death
        authority = LinuxCgroupTreeAuthority(tmp_path / "tree.sqlite")
        with pytest.raises(NativeTreeHold):
            authority.require_verified_empty(_identity("orphan"))
    finally:
        # Kill the entire test-owned detached session, not unrelated processes.
        try:
            os.killpg(orphan_pid, 9)
        except ProcessLookupError:
            pass


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


# D2 CONTRACT 2026-10-19: SandboxManager has a durable quarantine registry
# and OS custody mutex; its read-only kernel audit is NOT a cleanup permit.
# This replaces obsolete mock native_tree_authority / bind_native_tree /
# reattach_native_after_restart tests. Preserve the same negative invariants:
# no root deletion on UNKNOWN, no foreign owner adoption, no crash-to-EMPTY.


def test_manager_native_root_quarantined_until_authority_readback(tmp_path):
    """Current contract: even a positive-looking observation cannot delete a root."""
    manager = SandboxManager((tmp_path / "owned-roots").resolve())
    lease = manager.acquire_for_execution(
        execution_id="exec-d2", owner_user_id="owner-d2",
        profile=SandboxProfile(profile_id="native-b0-d2"),
    )
    token = manager.resolve_path(lease, "proof.txt")
    token.write_text("root must remain", encoding="utf-8")
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
    manager.quarantine_native_tree(lease)
    assert marker.is_file(), "native enrollment must be durable before cleanup"

    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert manager.current(lease).state is SandboxLeaseState.CLEANUP
    with pytest.raises(SandboxLeaseStateError):
        manager.resolve_path(lease, "no-new-write")
    with pytest.raises(NativeTreeUnverifiedError):
        manager.release_execution(lease.execution_id, owner_user_id=lease.owner_user_id)
    # No private filesystem delete escape hatch after quarantine.
    with pytest.raises(NativeTreeUnverifiedError):
        manager._remove_root(lease.root)
    assert token.read_text(encoding="utf-8") == "root must remain"
    assert marker.is_file()


def test_restart_reattach_quarantines_native_root_until_os_proof(tmp_path):
    """A real worker hard exit leaves an orphan root, not an empty attestation."""
    base = (tmp_path / "shared-roots").resolve()
    root_export = tmp_path / "orphan-root.txt"
    script = (
        "import os, pathlib\n"
        "from se.src.runtimes.capability.sandbox import SandboxManager\n"
        "from se.src.runtimes.capability.contracts.sandbox import SandboxProfile\n"
        f"m=SandboxManager(pathlib.Path({str(base)!r}))\n"
        "lease=m.acquire_for_execution(execution_id='exec-d2',"
        "owner_user_id='owner-d2',"
        "profile=SandboxProfile(profile_id='native-recover'))\n"
        "m.resolve_path(lease,'survivor.txt').write_text('preserve across restart')\n"
        "m.quarantine_native_tree(lease)\n"
        f"pathlib.Path({str(root_export)!r}).write_text(str(lease.root))\n"
        "os._exit(73)\n"
    )
    worker = subprocess.run(
        [sys.executable, "-c", script], check=False, timeout=20,
        cwd=str(Path.cwd()),
    )
    assert worker.returncode == 73, "hard-crash fixture did not execute"
    root = Path(root_export.read_text(encoding="utf-8"))
    token = root / "survivor.txt"
    marker = base / ".sbx3-native-quarantine" / root.name
    assert token.read_text(encoding="utf-8") == "preserve across restart"
    assert marker.is_file()

    restarted = SandboxManager(base)
    assert root.name in restarted._orphan_root_ids
    with pytest.raises(SandboxLeaseNotFoundError):
        restarted.current_for_execution("exec-d2")
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._require_native_root_empty(root)
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._remove_root(root)
    assert marker.is_file(), "restart must never clear the native tombstone"
    assert token.read_text(encoding="utf-8") == "preserve across restart"


def test_restart_with_missing_os_scope_never_adopts_or_deletes_root(tmp_path):
    """Erased marker or forged user receipt cannot make UNKNOWN into EMPTY."""
    base = (tmp_path / "shared-roots").resolve()
    original = SandboxManager(base)
    lease = original.acquire_for_execution(
        execution_id="exec-d2", owner_user_id="owner-d2",
        profile=SandboxProfile(profile_id="native-recover"),
    )
    token = original.resolve_path(lease, "survivor.txt")
    token.write_text("protected", encoding="utf-8")
    original.quarantine_native_tree(lease)
    marker = base / ".sbx3-native-quarantine" / lease.sandbox_id

    # A caller-supplied shape that says VERIFIED_EMPTY is diagnostic input,
    # never a replacement for the kernel/journal read-through.
    synthetic = VerifiedEmptyWitness(
        owner_id=lease.owner_user_id, execution_id=lease.execution_id,
        sandbox_id=lease.sandbox_id, generation=uuid4().hex,
        cgroup_device=1, cgroup_inode=1,
    )
    identity = CgroupTreeIdentity(
        owner_id=lease.owner_user_id, execution_id=lease.execution_id,
        sandbox_id=lease.sandbox_id, generation=uuid4().hex,
    )
    assert synthetic.status == "VERIFIED_EMPTY"
    assert synthetic.production_usable is False
    with pytest.raises(CgroupTreeMismatch, match="native kernel ledger required"):
        original.inspect_native_cleanup_evidence(
            lease, ledger=synthetic, identity=identity,
        )
    with pytest.raises(NativeTreeUnverifiedError):
        original.destroy(lease)
    assert token.read_text(encoding="utf-8") == "protected"

    # Even deliberate marker removal must not release the old live manager,
    # or enable a fresh manager to delete an orphan root.
    marker.unlink()
    with pytest.raises(NativeTreeUnverifiedError):
        original._remove_root(lease.root)
    restarted = SandboxManager(base)
    assert lease.sandbox_id in restarted._orphan_root_ids
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._remove_root(lease.root)
    assert token.read_text(encoding="utf-8") == "protected"
    assert lease.root.is_dir()


def test_manager_rejects_foreign_native_binding_before_spawn(tmp_path):
    """Foreign owner, execution or sandbox identity never reaches OS audit."""
    manager = SandboxManager((tmp_path / "owned-roots").resolve())
    lease = manager.acquire_for_execution(
        execution_id="exec-d2", owner_user_id="owner-d2",
        profile=SandboxProfile(profile_id="native-b0-d2"),
    )
    token = manager.resolve_path(lease, "owner-proof.txt")
    token.write_text("preserve", encoding="utf-8")
    manager.quarantine_native_tree(lease)
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id

    # A typed (but deliberately uninitialized) ledger makes the *identity*
    # preflight reachable without faking OS VERIFIED_EMPTY or touching cgroup.
    # The guard MUST reject every foreign identity BEFORE calling the ledger.
    ledger = object.__new__(LinuxCgroupTreeLedger)
    def forbidden_audit(_identity):
        raise AssertionError("FOREIGN_IDENTITY_REACHED_NATIVE_OS_AUDIT")
    ledger.audit_committed_empty = forbidden_audit

    for name, changes in (
        ("foreign-owner", {"owner_id": "other-owner"}),
        ("foreign-execution", {"execution_id": "other-exec"}),
        ("foreign-root", {"sandbox_id": "sbx-" + "0" * 32}),
    ):
        data = {
            "owner_id": lease.owner_user_id,
            "execution_id": lease.execution_id,
            "sandbox_id": lease.sandbox_id,
            "generation": uuid4().hex,
        }
        data.update(changes)
        with pytest.raises(CgroupTreeMismatch, match="identity mismatch"):
            manager.inspect_native_cleanup_evidence(
                lease, ledger=ledger, identity=CgroupTreeIdentity(**data),
            )

    # Agent-created altered lease objects and wrong-owner acquisition cannot
    # take over an existing sandbox, even if the sandbox ID is known.
    foreign_lease = lease.model_copy(update={"owner_user_id": "other-owner"})
    with pytest.raises(SandboxLeaseNotFoundError):
        manager.current(foreign_lease)
    with pytest.raises(SandboxError):
        manager.acquire_for_execution(
            execution_id="exec-d2", owner_user_id="other-owner",
            profile=SandboxProfile(profile_id="native-b0-d2"),
        )
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert marker.is_file()
    assert token.read_text(encoding="utf-8") == "preserve"


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
