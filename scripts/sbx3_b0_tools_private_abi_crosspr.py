#!/usr/bin/env python3
"""SBX3 SE#164 / Tools#371 private ABI smoke on two PINNED unmerged PRs.

Evidence-only: The workflow supplies the exact SE PR head with this script,
fetches a pinned Tools PR head, and installs only the Tools-owned MODULE into
the disposable runner checkout. This NEVER changes either live PR or main.
It tests no-host-fallback / non-replayable unknown / fake receipt rejection.
It is NOT genuine Windows AppContainer/LPAC or production native certification.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SE_PARENT = "3e0bf347863fe02253b72ad9372ed80133408570"
TOOLS_HEAD = "94a4151b19796bfe287eb6dfbd96adc1551ff464"
TOOLS_BASE = "af0feb268b59cc9828c536eda1629c3a64c39bed"
TOOLS_FILE_BLOB = "ce593df83b76ec4378c693591d06ff446ce13bd1"
TOOLS_TEST_BLOB = "95f2e12c2be80ca50eb53dad7ad182b61e3b9231"
TOOLS_EVIDENCE_BLOB = "9e8cacf93e0302404ee3a66ba5c7c3467a83e9e6"
TOOLS_PATH = "tools/v1/terminal_tool.py"
TOOLS_TEST_PATH = "tools/v1/test/test_t12_b0_private_broker_adapter.py"
TOOLS_EVIDENCE_PATH = ".github/workflows/tools371-b0-private-broker-negatives.yml"


def assert_exact_cross_pr_source() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    os.chdir(root)
    # When launched as "python scripts/...", sys.path[0] is scripts/ rather
    # than repo root. Import SE and Tools from the exact checkout, not ambient
    # globally installed packages.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Tests run on the SE child branch, so its parent commit remains exact
    # unless the workflow is explicitly re-anchored and reviewed.
    ancestral = subprocess.run(
        ["git", "merge-base", "--is-ancestor", SE_PARENT, "HEAD"],
        check=False, capture_output=True,
    )
    if ancestral.returncode != 0:
        raise AssertionError("Cross-PR ABI evidence parent mismatch")
    delta = set(subprocess.run(
        ["git", "diff", "--name-only", SE_PARENT, "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines())
    expected = {
        "scripts/sbx3_b0_tools_private_abi_crosspr.py",
        ".github/workflows/sbx3-b0-tools371-crosspr-abi.yml",
    }
    if delta != expected:
        raise AssertionError(
            f"Unexpected SE PR evidence source scope: {sorted(delta)}"
        )
    subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", TOOLS_HEAD],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    tools_delta = set(subprocess.run(
        ["git", "diff", "--name-only", TOOLS_BASE, TOOLS_HEAD],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines())
    tools_expected = {TOOLS_PATH, TOOLS_TEST_PATH, TOOLS_EVIDENCE_PATH}
    if tools_delta != tools_expected:
        raise AssertionError(
            f"Tools PR474 exact-three scope changed: {sorted(tools_delta)}"
        )
    for path, expected_blob in (
        (TOOLS_PATH, TOOLS_FILE_BLOB),
        (TOOLS_TEST_PATH, TOOLS_TEST_BLOB),
        (TOOLS_EVIDENCE_PATH, TOOLS_EVIDENCE_BLOB),
    ):
        observed = subprocess.run(
            ["git", "rev-parse", f"{TOOLS_HEAD}:{path}"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        if observed != expected_blob:
            raise AssertionError(
                f"Pinned Tools path {path} blob drift: {observed}"
            )
    blob = TOOLS_FILE_BLOB
    original = pathlib.Path(TOOLS_PATH)
    assert original.is_file() and not original.is_symlink()
    tool_source = subprocess.run(
        ["git", "show", f"{TOOLS_HEAD}:{TOOLS_PATH}"],
        check=True, capture_output=True,
    ).stdout
    if not tool_source or len(tool_source) > 100_000:
        raise AssertionError("Unreasonable Tools source blob")
    # Overlay only within a fresh GitHub Actions checkout. No git add/commit/
    # push, source branch change or production activation is performed.
    original.write_bytes(tool_source)
    # A second overlay is TEST-ONLY. Execute the full source-pinned Tools
    # negative suite on this same SE checkout, never against installed code.
    test_source = subprocess.run(
        ["git", "show", f"{TOOLS_HEAD}:{TOOLS_TEST_PATH}"],
        check=True, capture_output=True,
    ).stdout
    if not test_source or len(test_source) > 50_000:
        raise AssertionError("Unreasonable pinned Tools negative test file")
    test_target = pathlib.Path(TOOLS_TEST_PATH)
    if test_target.exists() or test_target.is_symlink():
        raise AssertionError("SE source unexpectedly already owns Tools B0 test")
    test_target.parent.mkdir(parents=True, exist_ok=True)
    test_target.write_bytes(test_source)
    print(f"SE_PARENT={SE_PARENT}")
    print(f"TOOLS_PR474_HEAD={TOOLS_HEAD}")
    print(f"TOOLS_BLOB={blob}")
    print("CROSS_PR_TOOLS_OVERLAY=EPHEMERAL_CI_ONLY")


def run_smoke() -> None:
    from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity
    from se.src.runtimes.capability.native_invocation_authority_b0 import (
        DurableNativeInvocationAuthorityB0,
        NativeTerminalB0FailClosedPort,
    )
    from tools.v1 import terminal_tool as tools_mod

    if not hasattr(tools_mod.TerminalTool, "_run_broker_owned"):
        raise AssertionError("Pinned Tools port is not implemented")
    if not hasattr(tools_mod, "_BrokerRunCompletion"):
        raise AssertionError("Pinned Tools completion type unavailable")

    cases = 0
    with tempfile.TemporaryDirectory(prefix="sbx3-private-port-") as temp:
        ledger = DurableNativeInvocationAuthorityB0(
            pathlib.Path(temp) / "native-attempt.sqlite"
        )
        port = NativeTerminalB0FailClosedPort(ledger)

        def mint(name: str):
            identity = SpawnIdentity(
                owner_id="SE-OWNER", execution_id="SE-EXEC",
                tool_call_id=name, sandbox_id="SBX-PRIVATE",
                lease_generation="gen-uuid-b0",
            )
            token = ledger.mint_diagnostic(
                identity, invocation_id="invocation-" + name,
                deadline_unix=time.time() + 100,
            )
            return identity, token

        async def call(candidate_port, credential):
            return await tools_mod.TerminalTool()._run_broker_owned(
                command="echo NEVER_EXECUTE_HOST",
                timeout=3,
                sandbox_relative_cwd="relative",
                encoding="utf-8",
                spawn_port=candidate_port,
                authority=credential,
            )

        def assert_unknown(result):
            if result.get("ok") is not False:
                raise AssertionError("unsafe Tools candidate success on SE deny")
            err = result.get("error")
            if not isinstance(err, dict) or err.get("code") != "TERMINAL_OUTCOME_UNKNOWN":
                raise AssertionError(f"unexpected Tools failure classification: {err}")
            if err.get("details", {}).get("replay_safe") is not False:
                raise AssertionError("unexpected replay-safe classification")

        # Fresh SE authority is burned durably by the private no-spawn port;
        # Tools must not fall back to subprocess even for a valid command.
        original, token = mint("fresh")
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            result = asyncio.run(call(port, token))
            host_spawn.assert_not_called()
        assert_unknown(result)
        assert ledger.state(original) == ("DENIED", "PREFLIGHT_DENIED")
        cases += 1
        print("PASS PRIVATE_ABI_NATIVE_DENY_NO_HOST_FALLBACK")

        # Replay is still denied and cannot produce a Tool success.
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            replay = asyncio.run(call(port, token))
            host_spawn.assert_not_called()
        assert_unknown(replay)
        cases += 1
        print("PASS PRIVATE_ABI_REPLAY_DENY")

        # A serialized/model-provided impostor is no native authority.
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            bogus = asyncio.run(call(port, {"verified_empty": True}))
            host_spawn.assert_not_called()
        assert_unknown(bogus)
        cases += 1
        print("PASS PRIVATE_ABI_FORGED_AUTHORITY_DENY")

        # An attacker-supplied EXITED Python completion plus fake tree and
        # receipt must not become success when SE's actual verification denies.
        class ForgedCompletionPort:
            async def run_preconfined(self, **kwargs):
                return tools_mod._BrokerRunCompletion(
                    outcome="EXITED", tree_ref=object(), receipt=object(),
                    exit_code=0, stdout=b"fake-success",
                    stdout_bytes_observed=12,
                )

            async def verify_receipt(self, **kwargs):
                return await port.verify_receipt(**kwargs)

        _, another = mint("fabricated")
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            forged = asyncio.run(call(ForgedCompletionPort(), another))
            host_spawn.assert_not_called()
        assert_unknown(forged)
        cases += 1
        print("PASS PRIVATE_ABI_FORGED_RECEIPT_REJECTED")

        # A worker crash before use must invalidate the separate generation's
        # reservation. A new process cannot mint again for the same tool-call.
        restart_id, restart_token = mint("restart")
        reopened = DurableNativeInvocationAuthorityB0(ledger.path)
        if reopened.reconcile_after_restart() < 1:
            raise AssertionError("no stale minted authority invalidated")
        assert reopened.state(restart_id) == ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN")
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            restarted = asyncio.run(call(NativeTerminalB0FailClosedPort(reopened), restart_token))
            host_spawn.assert_not_called()
        assert_unknown(restarted)
        cases += 1
        print("PASS PRIVATE_ABI_RESTART_UNKNOWN_NO_REPLAY")

        # A REAL independent Python worker crashes after a real SE-owned
        # SQLite authority+attempt atomic reservation. There is intentionally
        # no native tool execution or positive success/cleanup receipt.
        worker_script = (
            "import os, sys, time\n"
            "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
            "from se.src.runtimes.capability.native_invocation_authority_b0 "
            "import DurableNativeInvocationAuthorityB0\n"
            "db = DurableNativeInvocationAuthorityB0(sys.argv[1])\n"
            "db.mint_diagnostic(SpawnIdentity('SE-OWNER', 'SE-EXEC', "
            "'hard-crash', 'SBX-PRIVATE', 'gen-uuid-b0'), "
            "invocation_id='invocation-hard-crash', "
            "deadline_unix=time.time()+100)\n"
            "os._exit(87)\n"
        )
        worker = subprocess.run(
            [sys.executable, "-c", worker_script, str(ledger.path)],
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
            capture_output=True, timeout=15, check=False,
        )
        if worker.returncode != 87:
            raise AssertionError(
                f"Actual SE worker hard exit was not observed: "
                f"{worker.returncode}: {worker.stderr[:500]!r}"
            )
        crashed = SpawnIdentity(
            owner_id="SE-OWNER", execution_id="SE-EXEC",
            tool_call_id="hard-crash", sandbox_id="SBX-PRIVATE",
            lease_generation="gen-uuid-b0",
        )
        restarted_se = DurableNativeInvocationAuthorityB0(ledger.path)
        if restarted_se.reconcile_after_restart() < 1:
            raise AssertionError("actual hard-crash reservation not reconciled")
        if restarted_se.state(crashed) != ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN"):
            raise AssertionError("restarted SE authority was replayable")
        try:
            restarted_se.mint_diagnostic(
                crashed, invocation_id="another-hard-crash",
                deadline_unix=time.time()+100,
            )
        except Exception as exc:
            if exc.__class__.__name__ != "NativeAuthorityDeniedB0":
                raise
        else:
            raise AssertionError("SE issued another effect reservation after crash")
        with patch.object(tools_mod.subprocess, "Popen") as host_spawn:
            retry = asyncio.run(call(
                NativeTerminalB0FailClosedPort(restarted_se),
                {"invocation_id": "invocation-hard-crash", "verified_empty": True},
            ))
            host_spawn.assert_not_called()
        assert_unknown(retry)
        cases += 1
        print("PASS PRIVATE_ABI_REAL_SE_WORKER_CRASH_RESTART_NO_REPLAY")

    if cases != 6:
        raise AssertionError("missing private ABI test node")
    print(json.dumps({
        "tests": cases, "failed": 0, "skipped": 0,
        "se_head": SE_PARENT, "tools_head": TOOLS_HEAD,
        "host_popen_invocations": 0,
        "native_command_activated": False,
        "native_root_deletion_released": False,
        "windows_appcontainer_network_none_proven": False,
    }, sort_keys=True))
    print("SBX3_CROSS_PR_PRIVATE_ABI=PASS_DIAGNOSTIC_ONLY")


def run_pinned_tools_negative_suite() -> None:
    import re
    # This runs the REAL changed 52-test Tools suite from the exact PR474
    # head, over the exact production module, on each CI runner OS.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-ra", TOOLS_TEST_PATH],
        check=False, capture_output=True, text=True, timeout=120,
    )
    print(result.stdout)
    if result.returncode != 0 or not re.search(r"\b52 passed\b", result.stdout):
        raise AssertionError(
            f"Pinned Tools 52 negative tests failed: exit={result.returncode}; "
            f"stderr={result.stderr[-2000:]}"
        )
    if re.search(r"\b(skipped|xfailed|xpassed|failed|error)\b", result.stdout):
        raise AssertionError("Pinned Tools negative tests were weakened")
    print("SBX3_PINNED_TOOLS_52_NEGATIVE_TESTS=PASS_SYNTHETIC_ONLY")


if __name__ == "__main__":
    assert_exact_cross_pr_source()
    run_smoke()
    run_pinned_tools_negative_suite()
