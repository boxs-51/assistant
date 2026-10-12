"""SBX3 B0 SE/Tools private ABI durable scope: negative-only OS-neutral tests.

Uses real SQLite disk and independent Python processes (Linux + Windows).
No mocked database connections or manufactured VERIFIED_EMPTY success.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity
from se.src.runtimes.capability.native_invocation_authority_b0 import (
    DurableNativeInvocationAuthorityB0,
    NativeAuthorityDeniedB0,
    NativeTerminalB0FailClosedPort,
)


def _identity(n: str = "a") -> SpawnIdentity:
    return SpawnIdentity(
        owner_id="owner-" + n,
        execution_id="execution-" + n,
        tool_call_id="toolcall-" + n,
        sandbox_id="sandbox-" + n,
        lease_generation="generation-" + n,
    )


def _mint(journal: DurableNativeInvocationAuthorityB0, n: str = "a"):
    return journal.mint_diagnostic(
        _identity(n), invocation_id="invocation-" + n,
        deadline_unix=time.time() + 120,
    )


def _port_call(port: NativeTerminalB0FailClosedPort, authority):
    return asyncio.run(port.run_preconfined(
        authority=authority,
        command="do-not-execute-native",
        timeout=10,
        sandbox_relative_cwd="src",
        encoding="utf-8",
        max_stdout_bytes=100,
        max_stderr_bytes=100,
        max_total_output_bytes=200,
    ))


def test_atomic_mint_and_no_exec_preflight_consumption(tmp_path):
    path = tmp_path / "native-attempts.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    authority = _mint(journal)
    assert "<" not in repr(authority)
    assert authority._secret.hex() not in repr(authority)
    assert journal.state(authority.identity) == ("RESERVED", "ISSUED")
    with patch.object(subprocess, "Popen") as spawned:
        with pytest.raises(NativeAuthorityDeniedB0, match="denied before OS spawn"):
            _port_call(NativeTerminalB0FailClosedPort(journal), authority)
        spawned.assert_not_called()
    assert journal.state(authority.identity) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.deny_before_spawn(authority)
    with pytest.raises(NativeAuthorityDeniedB0):
        _mint(journal)


def test_forged_scope_token_and_wrong_invocation_do_not_consume_good_ticket(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "ledger.sqlite")
    good = _mint(journal)
    for bad in (
        replace(good, _secret=os.urandom(32)),
        replace(good, invocation_id="foreign-invocation"),
        replace(good, owner_id="foreign-owner"),
        replace(good, lease_generation="foreign-generation"),
        replace(good, sandbox_id="foreign-sandbox"),
        replace(good, deadline_unix=good.deadline_unix - 1),
    ):
        with pytest.raises(NativeAuthorityDeniedB0):
            journal.deny_before_spawn(bad)
        assert journal.state(good.identity) == ("RESERVED", "ISSUED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.deny_before_spawn({"spoofed": "verified-empty"})
    with pytest.raises(NativeAuthorityDeniedB0):
        _mint(journal, "a")
    assert journal.state(good.identity) == ("RESERVED", "ISSUED")


def test_different_toolcalls_and_invocations_unique(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "ledger.sqlite")
    first = _mint(journal, "a")
    other = _mint(journal, "b")
    assert first._secret != other._secret
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.mint_diagnostic(
            _identity("c"), invocation_id=first.invocation_id,
            deadline_unix=time.time() + 60,
        )
    assert journal.state(_identity("c")) is None
    assert journal.state(first.identity) == ("RESERVED", "ISSUED")
    assert journal.state(other.identity) == ("RESERVED", "ISSUED")


def test_recovery_fences_unconsumed_authority_and_attempt_on_disk(tmp_path):
    journal_path = tmp_path / "native-ledger.sqlite"
    path_file = tmp_path / "minted-token.txt"
    script = (
        "import os, pathlib, time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0\n"
        "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
        f"j=DurableNativeInvocationAuthorityB0({str(journal_path)!r})\n"
        "token=j.mint_diagnostic(SpawnIdentity("
        "'owner-a','execution-a','toolcall-a','sandbox-a','generation-a'),"
        "invocation_id='invocation-a', deadline_unix=time.time()+120)\n"
        f"pathlib.Path({str(path_file)!r}).write_text(token._secret.hex())\n"
        "os._exit(87)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], timeout=25, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 87, result.stderr.decode("utf-8", "replace")
    assert len(path_file.read_text()) == 64
    restarted = DurableNativeInvocationAuthorityB0(journal_path)
    assert restarted.state(_identity()) == ("RESERVED", "ISSUED")
    assert restarted.reconcile_after_restart() == 1
    assert restarted.state(_identity()) == ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN")
    assert restarted.reconcile_after_restart() == 0
    with pytest.raises(NativeAuthorityDeniedB0):
        _mint(restarted)
    with sqlite3.connect(journal_path) as db:
        records = db.execute(
            "SELECT state FROM native_invocation_authority_b0"
        ).fetchall()
        assert records == [("OUTCOME_UNKNOWN",)]


def test_independent_processes_race_only_one_reservation_wins(tmp_path):
    database = tmp_path / "native-multworker.sqlite"
    common = (
        "import sys,time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0,NativeAuthorityDeniedB0\n"
        "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
        f"j=DurableNativeInvocationAuthorityB0({str(database)!r})\n"
        "try:\n"
        " j.mint_diagnostic(SpawnIdentity('owner-a','execution-a',"
        "'toolcall-a','sandbox-a','generation-a'),"
        "invocation_id='invocation-a',deadline_unix=time.time()+120)\n"
        "except NativeAuthorityDeniedB0:\n"
        " sys.exit(7)\n"
        "sys.exit(0)\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", common],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=35)
        results.append((process.returncode, stderr.decode("utf-8", "replace")[-600:]))
    assert sorted(x[0] for x in results) == [0, 7, 7, 7], results
    journal = DurableNativeInvocationAuthorityB0(database)
    assert journal.state(_identity()) == ("RESERVED", "ISSUED")


def test_unsupported_native_port_never_validates_forged_receipt(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "native.sqlite")
    port = NativeTerminalB0FailClosedPort(journal)
    authority = _mint(journal)
    assert asyncio.run(port.verify_receipt(
        authority=authority, completion=object()
    )) is False
    assert asyncio.run(port.verify_receipt(
        authority=authority,
        completion={"outcome": "EXITED", "verified_empty": True},
    )) is False
    with pytest.raises(NativeAuthorityDeniedB0):
        _port_call(port, authority)
    assert journal.state(authority.identity) == ("DENIED", "PREFLIGHT_DENIED")


def _same_generation(tool_call: str, generation: str = "g-1") -> SpawnIdentity:
    return SpawnIdentity(
        "owner-revoked", "exec-revoked", tool_call, "sandbox-revoked", generation,
    )


def test_generation_revocation_burns_all_pending_once_without_activating_native(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "generation.sqlite")
    tokens = [
        journal.mint_diagnostic(
            _same_generation(f"tool-{i}"),
            invocation_id=f"revoked-invocation-{i}",
            deadline_unix=time.time() + 180,
        )
        for i in range(3)
    ]
    other = journal.mint_diagnostic(
        _same_generation("other-generation", "g-2"),
        invocation_id="other-generation-invocation",
        deadline_unix=time.time() + 180,
    )
    with patch.object(subprocess, "Popen") as spawn:
        changed = journal.revoke_generation(
            owner_id="owner-revoked", execution_id="exec-revoked",
            sandbox_id="sandbox-revoked", lease_generation="g-1",
        )
        assert changed == 3
        assert journal.revoke_generation(
            owner_id="owner-revoked", execution_id="exec-revoked",
            sandbox_id="sandbox-revoked", lease_generation="g-1",
        ) == 0
        for token in tokens:
            assert journal.state(token.identity) == ("DENIED", "PREFLIGHT_DENIED")
            with pytest.raises(NativeAuthorityDeniedB0):
                journal.deny_before_spawn(token)
        assert journal.state(other.identity) == ("RESERVED", "ISSUED")
        # Native remains negative-only even for an unrevoked generation.
        with pytest.raises(NativeAuthorityDeniedB0):
            journal.deny_before_spawn(other)
        spawn.assert_not_called()


def test_expired_ticket_is_explicitly_burned_and_never_replayed(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "expire.sqlite")
    deadline = time.time() + 2
    token = journal.mint_diagnostic(
        _same_generation("expired-call"),
        invocation_id="expired-invocation",
        deadline_unix=deadline,
    )
    with patch(
        "se.src.runtimes.capability.native_invocation_authority_b0.time.time",
        return_value=deadline + 1,
    ):
        with pytest.raises(NativeAuthorityDeniedB0, match="expired"):
            journal.deny_before_spawn(token)
    assert journal.state(token.identity) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.deny_before_spawn(token)


def test_crash_after_generation_revocation_commit_is_durable(tmp_path):
    db = tmp_path / "generation-crash.sqlite"
    journal = DurableNativeInvocationAuthorityB0(db)
    token = journal.mint_diagnostic(
        _same_generation("durable-revoke"),
        invocation_id="durable-revoke-invocation",
        deadline_unix=time.time() + 160,
    )
    script = (
        "import os\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0\n"
        f"j=DurableNativeInvocationAuthorityB0({str(db)!r})\n"
        "assert j.revoke_generation("
        "owner_id='owner-revoked', execution_id='exec-revoked',"
        "sandbox_id='sandbox-revoked', lease_generation='g-1')==1\n"
        "os._exit(74)\n"
    )
    terminated = subprocess.run(
        [sys.executable, "-c", script], timeout=25,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert terminated.returncode == 74, terminated.stderr.decode(
        "utf-8", "replace"
    )[-1200:]
    reopened = DurableNativeInvocationAuthorityB0(db)
    assert reopened.state(token.identity) == ("DENIED", "PREFLIGHT_DENIED")
    assert reopened.reconcile_after_restart() == 0
    with pytest.raises(NativeAuthorityDeniedB0):
        reopened.deny_before_spawn(token)
    with pytest.raises(NativeAuthorityDeniedB0):
        reopened.mint_diagnostic(
            token.identity, invocation_id=token.invocation_id,
            deadline_unix=time.time() + 100,
        )


def test_separate_worker_revoke_vs_preflight_cas_is_single_winner(tmp_path):
    db = tmp_path / "crossproc-revoke.sqlite"
    journal = DurableNativeInvocationAuthorityB0(db)
    token = journal.mint_diagnostic(
        _same_generation("crossproc"),
        invocation_id="crossproc-invocation",
        deadline_unix=time.time() + 150,
    )
    go = tmp_path / "start"
    common = (
        "import os, pathlib, sys, time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0, "
        "OpaqueSpawnAuthorityB0, NativeAuthorityDeniedB0\n"
        f"j=DurableNativeInvocationAuthorityB0({str(db)!r})\n"
        f"barrier=pathlib.Path({str(go)!r})\n"
        "while not barrier.exists(): time.sleep(.005)\n"
    )
    revoker = common + (
        "count=j.revoke_generation("
        "owner_id='owner-revoked',execution_id='exec-revoked',"
        "sandbox_id='sandbox-revoked',lease_generation='g-1')\n"
        "assert count in (0,1)\n"
    )
    contender = common + (
        "credential=OpaqueSpawnAuthorityB0("
        f"{token.owner_id!r},{token.execution_id!r},{token.tool_call_id!r},"
        f"{token.invocation_id!r},{token.sandbox_id!r},"
        f"{token.lease_generation!r},{token.deadline_unix!r},"
        f"bytes.fromhex({token._secret.hex()!r}))\n"
        "try: j.deny_before_spawn(credential)\n"
        "except NativeAuthorityDeniedB0: sys.exit(0)\n"
        "sys.exit(22)\n"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for code in (revoker, contender)
    ]
    go.touch()
    outcomes = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=30)
        outcomes.append((worker.returncode, stderr.decode("utf-8", "replace")[-1000:]))
    assert outcomes[0][0] == outcomes[1][0] == 0, outcomes
    assert DurableNativeInvocationAuthorityB0(db).state(
        token.identity
    ) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.deny_before_spawn(token)


def test_bad_deadline_or_missing_identity_fails_before_durable_reservation(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "ledger.sqlite")
    for deadline in (time.time() - 5, time.time() + 9999):
        with pytest.raises(NativeAuthorityDeniedB0):
            journal.mint_diagnostic(
                _identity(), invocation_id="invocation-a",
                deadline_unix=deadline,
            )
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.mint_diagnostic(
            _identity(), invocation_id="../bad invocation ",
            deadline_unix=time.time() + 15,
        )
    assert journal.state(_identity()) is None
