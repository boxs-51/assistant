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
TOOLS_HEAD = "46a44bacfe9b99ac2e1e87171a2e5a92490cb3b4"
TOOLS_FILE_BLOB = "ce593df83b76ec4378c693591d06ff446ce13bd1"
TOOLS_PATH = "tools/v1/terminal_tool.py"


def assert_exact_cross_pr_source() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    os.chdir(root)
    # Tests run on the SE child branch, so its parent commit remains exact
    # unless the workflow is explicitly re-anchored and reviewed.
    base = subprocess.run(
        ["git", "rev-parse", "HEAD^"], check=True, text=True,
        capture_output=True,
    ).stdout.strip()
    if base != SE_PARENT:
        raise AssertionError(
            f"Cross-PR ABI evidence invalid: SE parent {base} != {SE_PARENT}"
        )
    subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", TOOLS_HEAD],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    blob = subprocess.run(
        ["git", "rev-parse", f"{TOOLS_HEAD}:{TOOLS_PATH}"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    if blob != TOOLS_FILE_BLOB:
        raise AssertionError(
            f"TOOLS private port source changed ({blob}); require explicit reanchor"
        )
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

    if cases != 5:
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


if __name__ == "__main__":
    assert_exact_cross_pr_source()
    run_smoke()
