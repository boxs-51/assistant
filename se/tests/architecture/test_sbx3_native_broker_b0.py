"""Real OS B0 diagnostic and durable no-replay tests.

No Agent dispatch, no production native receipts, no simulated native-positive
PASS. The Linux CI's strict marker requires an ACTUAL successful bwrap
namespace probe, and Windows CI verifies an actual pre-spawn DENY.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from se.src.runtimes.capability.native_broker_b0 import (
    B0LedgerConflict,
    B0ReplayDenied,
    DurableAttemptLedger,
    NativeBrokerB0Diagnostic,
    SpawnIdentity,
)


def _identity(tag: str) -> SpawnIdentity:
    return SpawnIdentity(
        owner_id="owner-" + tag,
        execution_id="execution-" + tag,
        tool_call_id="call-" + tag,
        sandbox_id="sandbox-" + tag,
        lease_generation="gen-01",
    )


def test_durable_restart_marks_prepared_outcome_unknown_and_no_replay(tmp_path):
    journal = tmp_path / "durable" / "ledger.sqlite"
    identity = _identity("crash")
    script = (
        "import os\n"
        "from se.src.runtimes.capability.native_broker_b0 import "
        "DurableAttemptLedger, SpawnIdentity\n"
        "ledger = DurableAttemptLedger(" + repr(str(journal)) + ")\n"
        "ledger.reserve(SpawnIdentity(" + ", ".join(repr(v) for v in (
            identity.owner_id,
            identity.execution_id,
            identity.tool_call_id,
            identity.sandbox_id,
            identity.lease_generation,
        )) + "))\n"
        "os._exit(79)\n"
    )
    crash = subprocess.run(
        [sys.executable, "-c", script],
        shell=False,
        check=False,
        timeout=20,
    )
    assert crash.returncode == 79, "real worker hard-exit must have occurred"

    recovered = DurableAttemptLedger(journal)
    assert recovered.state(identity) == "RESERVED"
    assert recovered.reconcile_after_restart() == 1
    assert recovered.state(identity) == "OUTCOME_UNKNOWN"
    with pytest.raises(B0ReplayDenied):
        recovered.reserve(identity)


def test_durable_dispatching_recovery_and_stale_foreign_cas(tmp_path):
    path = tmp_path / "ledger.sqlite"
    original = _identity("transition")
    journal = DurableAttemptLedger(path)
    journal.reserve(original)
    journal.transition(original, "RESERVED", "DISPATCHING")

    foreign = SpawnIdentity(
        owner_id="other-owner",
        execution_id=original.execution_id,
        tool_call_id=original.tool_call_id,
        sandbox_id=original.sandbox_id,
        lease_generation=original.lease_generation,
    )
    with pytest.raises(B0LedgerConflict):
        journal.transition(foreign, "DISPATCHING", "OUTCOME_UNKNOWN")

    restart = DurableAttemptLedger(path)
    assert restart.reconcile_after_restart() == 1
    assert restart.state(original) == "OUTCOME_UNKNOWN"
    with pytest.raises(B0LedgerConflict):
        journal.transition(original, "DISPATCHING", "OUTCOME_UNKNOWN")
    assert restart.reconcile_after_restart() == 0


def test_two_workers_cannot_claim_same_effect(tmp_path):
    journal = tmp_path / "shared.sqlite"
    identity = _identity("race")

    def reserve_once(_):
        try:
            DurableAttemptLedger(journal).reserve(identity)
            return "RESERVED"
        except B0ReplayDenied:
            return "DENIED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve_once, range(2)))
    assert sorted(results) == ["DENIED", "RESERVED"]


def test_native_probe_rejects_invalid_arguments_before_journal(tmp_path):
    journal = DurableAttemptLedger(tmp_path / "state.sqlite")
    broker = NativeBrokerB0Diagnostic(journal)
    identity = _identity("args")
    with pytest.raises(ValueError):
        broker.probe_python(identity, tmp_path, "")
    with pytest.raises(ValueError):
        broker.probe_python(identity, tmp_path, "print(1)", timeout_seconds=60)
    assert journal.state(identity) is None


def test_linux_argv_includes_preexec_namespace_and_fs_fences(tmp_path):
    argv = NativeBrokerB0Diagnostic._linux_argv(
        "/usr/bin/bwrap", tmp_path, "print('diagnostic')"
    )
    for required in (
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--ro-bind",
        "--bind",
    ):
        assert required in argv
    assert "--share-net" not in argv
    assert argv[-3:] == ["/usr/bin/python3", "-c", "print('diagnostic')"]


def test_real_windows_native_host_denies_before_any_process(tmp_path):
    if platform.system() != "Windows":
        return
    journal = DurableAttemptLedger(tmp_path / "ledger.sqlite")
    root = tmp_path / "root"
    root.mkdir()
    broker = NativeBrokerB0Diagnostic(journal)
    identity = _identity("windows")
    with patch(
        "se.src.runtimes.capability.native_broker_b0.subprocess.run",
        side_effect=AssertionError("host process must not spawn"),
    ) as launch:
        result = broker.probe_python(identity, root, "print(99)")
    launch.assert_not_called()
    assert result.disposition == "DENIED_UNSUPPORTED_PRESPAWN"
    assert result.native_launch_attempted is False
    assert result.certified is False
    assert journal.state(identity) == "DENIED"
    with pytest.raises(B0ReplayDenied):
        broker.probe_python(identity, root, "print(99)")


def test_real_linux_mount_and_network_namespace_or_explicit_red(tmp_path):
    if platform.system() != "Linux":
        return

    journal = DurableAttemptLedger(tmp_path / "journal" / "ledger.sqlite")
    root = tmp_path / "sandbox-root"
    root.mkdir()
    outside = tmp_path / "host-only-private-canary"
    outside.write_text("must-not-be-visible", encoding="utf-8")
    identity = _identity("linux-native")
    broker = NativeBrokerB0Diagnostic(journal)
    strict = os.environ.get("SBX3_REQUIRE_NATIVE_POSITIVE") == "1"

    # Real host socket server: an isolated network namespace cannot reach it
    # through the sandbox's own 127.0.0.1.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        script = (
            "import os, socket\n"
            "assert not os.path.exists(" + repr(str(outside)) + ")\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(1.0)\n"
            "try:\n"
            "    s.connect(('127.0.0.1', " + str(port) + "))\n"
            "except OSError:\n"
            "    print('HOST_NETWORK_BLOCKED=1')\n"
            "else:\n"
            "    raise AssertionError('host network reachable')\n"
            "with open('/work/native-marker', 'w', encoding='utf-8') as f:\n"
            "    f.write('isolated')\n"
            "print('HOST_FILE_HIDDEN=1')\n"
        )
        result = broker.probe_python(identity, root, script)

    assert result.certified is False
    assert journal.state(identity) in {"DENIED", "OUTCOME_UNKNOWN"}
    with pytest.raises(B0ReplayDenied):
        broker.probe_python(identity, root, script)

    if result.disposition == "DENIED_UNSUPPORTED_PRESPAWN":
        if strict:
            pytest.fail("NATIVE_LINUX_POSITIVE_NOT_EXECUTED: bwrap unavailable")
        return

    assert result.disposition == "OUTCOME_UNKNOWN_NO_DURABLE_TREE_PROOF"
    if result.returncode != 0:
        if strict:
            pytest.fail(
                "NATIVE_LINUX_POSITIVE_FAILED: "
                + result.stderr[-600:]
            )
        return
    assert "HOST_NETWORK_BLOCKED=1" in result.stdout
    assert "HOST_FILE_HIDDEN=1" in result.stdout
    assert (root / "native-marker").read_text(encoding="utf-8") == "isolated"
    # A clean native probe exit never upgrades this stage to Agent SUCCESS.
    assert journal.state(identity) == "OUTCOME_UNKNOWN"
