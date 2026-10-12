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


def test_permanent_revoke_before_any_issue_denies_new_ids_across_restart(tmp_path):
    db = tmp_path / "generation-tombstone.sqlite"
    first = DurableNativeInvocationAuthorityB0(db)
    scope = dict(owner_id="owner-revoked", execution_id="exec-revoked",
                 sandbox_id="sandbox-revoked", lease_generation="g-1")
    assert first.revoke_generation(**scope) == 0
    assert first.revoke_generation(**scope) == 0
    reopened = DurableNativeInvocationAuthorityB0(db)
    for suffix in ("new-a", "new-b"):
        with pytest.raises(NativeAuthorityDeniedB0, match="permanently revoked"):
            reopened.mint_diagnostic(
                _same_generation(suffix),
                invocation_id="invocation-" + suffix,
                deadline_unix=time.time() + 90,
            )
        assert reopened.state(_same_generation(suffix)) is None
    with sqlite3.connect(db) as sql:
        assert sql.execute(
            "SELECT COUNT(*) FROM native_generation_revocations_b0"
        ).fetchone()[0] == 1


def test_revoking_one_generation_does_not_revoke_next_generation(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "generations.sqlite")
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 0
    other = replace(_same_generation("fresh"), lease_generation="g-2")
    token = journal.mint_diagnostic(
        other, invocation_id="fresh-generation-invocation",
        deadline_unix=time.time() + 60,
    )
    assert journal.state(other) == ("RESERVED", "ISSUED")
    with pytest.raises(NativeAuthorityDeniedB0, match="denied before OS spawn"):
        journal.deny_before_spawn(token)
    assert journal.state(other) == ("DENIED", "PREFLIGHT_DENIED")


def test_independent_workers_revoke_vs_fresh_mint_never_leave_issued_token(tmp_path):
    db = tmp_path / "revoke-race.sqlite"
    DurableNativeInvocationAuthorityB0(db)
    go = tmp_path / "start"
    common = (
        "import pathlib,sys,time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0,NativeAuthorityDeniedB0\n"
        "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
        f"j=DurableNativeInvocationAuthorityB0({str(db)!r})\n"
        f"g=pathlib.Path({str(go)!r})\n"
        "while not g.exists(): time.sleep(.005)\n"
    )
    revoke = common + (
        "j.revoke_generation(owner_id='owner-revoked',"
        "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
        "lease_generation='g-1')\n"
    )
    mint = common + (
        "try:\n"
        " j.mint_diagnostic(SpawnIdentity('owner-revoked','exec-revoked',"
        "'race-call','sandbox-revoked','g-1'),"
        "invocation_id='race-invocation',deadline_unix=time.time()+60)\n"
        "except NativeAuthorityDeniedB0: sys.exit(0)\n"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
        for script in (revoke, mint)
    ]
    go.touch()
    outcomes = []
    for worker in processes:
        _, stderr = worker.communicate(timeout=30)
        outcomes.append((worker.returncode, stderr.decode("utf-8", "replace")[-1000:]))
    assert all(code == 0 for code, _ in outcomes), outcomes
    restarted = DurableNativeInvocationAuthorityB0(db)
    # A token minted before revocation is retired within the revocation commit.
    assert restarted.state(_same_generation("race-call")) in (
        None, ("DENIED", "PREFLIGHT_DENIED")
    )
    with pytest.raises(NativeAuthorityDeniedB0, match="permanently revoked"):
        restarted.mint_diagnostic(
            _same_generation("post-race"),
            invocation_id="after-race", deadline_unix=time.time() + 60,
        )


def test_crash_after_zero_ticket_revoke_fences_fresh_mint_in_new_worker(tmp_path):
    """A durable tombstone survives os._exit even when there were no tickets."""
    db = tmp_path / "zero-revoke-hard-exit.sqlite"
    original = DurableNativeInvocationAuthorityB0(db)
    script = (
        "import os\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0\n"
        f"j=DurableNativeInvocationAuthorityB0({str(db)!r})\n"
        "assert j.revoke_generation(owner_id='owner-revoked',"
        "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
        "lease_generation='g-1') == 0\n"
        "os._exit(93)\n"
    )
    exit_worker = subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=25,
    )
    assert exit_worker.returncode == 93, exit_worker.stderr.decode(
        "utf-8", "replace"
    )[-900:]
    assert original.reconcile_after_restart() == 0
    worker = (
        "import sys,time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0,NativeAuthorityDeniedB0\n"
        "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
        f"j=DurableNativeInvocationAuthorityB0({str(db)!r})\n"
        "try:\n"
        " j.mint_diagnostic(SpawnIdentity('owner-revoked','exec-revoked',"
        "'never-before-used-call','sandbox-revoked','g-1'),"
        "invocation_id='never-before-used-invocation',"
        "deadline_unix=time.time()+90)\n"
        "except NativeAuthorityDeniedB0: sys.exit(0)\n"
        "sys.exit(31)\n"
    )
    tested = subprocess.run(
        [sys.executable, "-c", worker],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=25,
    )
    assert tested.returncode == 0, tested.stderr.decode(
        "utf-8", "replace"
    )[-900:]
    assert original.state(_same_generation("never-before-used-call")) is None
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM native_generation_revocations_b0"
        ).fetchone()[0] == 1


def test_s0_issued_authority_unknown_attempt_cannot_rollback_revoke_tombstone(tmp_path):
    """S0-13: a legal independent ledger UNKNOWN cannot undo permanent revoke."""
    path = tmp_path / "s0-mixed.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    scope = dict(owner_id="owner-revoked", execution_id="exec-revoked",
                 sandbox_id="sandbox-revoked", lease_generation="g-1")
    old = journal.mint_diagnostic(
        _same_generation("old-call"), invocation_id="old-invocation",
        deadline_unix=time.time() + 90,
    )
    journal.attempts.transition(old.identity, "RESERVED", "OUTCOME_UNKNOWN")
    assert journal.state(old.identity) == ("OUTCOME_UNKNOWN", "ISSUED")
    with patch.object(subprocess, "Popen") as spawn:
        assert journal.revoke_generation(**scope) == 1
        assert journal.state(old.identity) == ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN")
        with pytest.raises(NativeAuthorityDeniedB0):
            journal.deny_before_spawn(old)
        restarted = DurableNativeInvocationAuthorityB0(path)
        assert restarted.reconcile_after_restart() == 0
        with sqlite3.connect(path) as conn:
            assert conn.execute(
                """SELECT COUNT(*) FROM native_generation_revocations_b0
                   WHERE owner_id=? AND execution_id=? AND sandbox_id=?
                     AND lease_generation=?""", tuple(scope.values()),
            ).fetchone()[0] == 1
        with pytest.raises(NativeAuthorityDeniedB0, match="permanently revoked"):
            restarted.mint_diagnostic(
                _same_generation("fresh-call"), invocation_id="fresh-invocation",
                deadline_unix=time.time() + 90,
            )
        assert restarted.state(_same_generation("fresh-call")) is None
        assert restarted.revoke_generation(**scope) == 0
        spawn.assert_not_called()


def test_s0_dispatching_attempt_revoke_preserves_unknown_outcome(tmp_path):
    """Never turn an already-dispatching effect into proven clean DENIED."""
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "s0-dispatching.sqlite")
    ticket = journal.mint_diagnostic(
        _same_generation("dispatching"), invocation_id="dispatching-inv",
        deadline_unix=time.time() + 90,
    )
    journal.attempts.transition(ticket.identity, "RESERVED", "DISPATCHING")
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 1
    assert journal.state(ticket.identity) == ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.deny_before_spawn(ticket)
    with pytest.raises(NativeAuthorityDeniedB0, match="permanently revoked"):
        journal.mint_diagnostic(
            _same_generation("different-call"), invocation_id="new-inv",
            deadline_unix=time.time() + 90,
        )


def test_s0_rejected_mint_leaves_no_orphan_reservation(tmp_path):
    """Revoke even an empty generation; failed mint may insert neither row."""
    path = tmp_path / "s0-orphan.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    scope = dict(owner_id="owner-revoked", execution_id="exec-revoked",
                 sandbox_id="sandbox-revoked", lease_generation="g-1")
    assert journal.revoke_generation(**scope) == 0
    with pytest.raises(NativeAuthorityDeniedB0, match="permanently revoked"):
        journal.mint_diagnostic(
            _same_generation("never-issued"), invocation_id="never-inv",
            deadline_unix=time.time() + 90,
        )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM native_invocation_authority_b0"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM native_generation_revocations_b0"
        ).fetchone()[0] == 1


def test_s0_other_owner_execution_sandbox_generation_unaffected(tmp_path):
    """All four scope fields must be exact, not just the generation name."""
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "s0-scope.sqlite")
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 0
    for field, value in (
        ("owner_id", "owner-other"), ("execution_id", "exec-other"),
        ("sandbox_id", "sandbox-other"), ("lease_generation", "g-other"),
    ):
        identity = replace(_same_generation("scope-" + field), **{field: value})
        token = journal.mint_diagnostic(
            identity, invocation_id="scope-inv-" + field,
            deadline_unix=time.time() + 90,
        )
        assert journal.state(identity) == ("RESERVED", "ISSUED")
        with pytest.raises(NativeAuthorityDeniedB0, match="denied before OS spawn"):
            journal.deny_before_spawn(token)
        assert journal.state(identity) == ("DENIED", "PREFLIGHT_DENIED")


def test_s0_existing_journal_upgrade_preserves_replay_history(tmp_path):
    """Old authority/attempt tables survive a CREATE TABLE IF NOT EXISTS upgrade."""
    from se.src.runtimes.capability.native_broker_b0 import DurableAttemptLedger

    path = tmp_path / "s0-legacy.sqlite"
    DurableAttemptLedger(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE native_invocation_authority_b0 (
                execution_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL, lease_generation TEXT NOT NULL,
                deadline_unix REAL NOT NULL,
                route TEXT NOT NULL CHECK(route='terminal.run'),
                credential_hash BLOB NOT NULL,
                state TEXT NOT NULL CHECK(state IN
                    ('ISSUED','PREFLIGHT_DENIED','OUTCOME_UNKNOWN')),
                PRIMARY KEY (execution_id, tool_call_id)
            )"""
        )
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,'RESERVED')",
            ("exec-revoked", "legacy-call", "owner-revoked", "sandbox-revoked", "g-1"),
        )
        conn.execute(
            """INSERT INTO native_invocation_authority_b0
               VALUES (?,?,?,?,?,?,?,'terminal.run',?,'ISSUED')""",
            ("exec-revoked", "legacy-call", "old-invocation", "owner-revoked",
             "sandbox-revoked", "g-1", time.time() + 120, bytes(32)),
        )
    journal = DurableNativeInvocationAuthorityB0(path)
    old = _same_generation("legacy-call")
    assert journal.state(old) == ("RESERVED", "ISSUED")
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 1
    reopened = DurableNativeInvocationAuthorityB0(path)
    assert reopened.state(old) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        reopened.mint_diagnostic(
            _same_generation("post-upgrade"), invocation_id="post-upgrade-inv",
            deadline_unix=time.time() + 90,
        )


def test_s0_empty_generation_revocation_persists_and_denies_new_mint(tmp_path):
    path = tmp_path / "s0-empty.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 0
    reopened = DurableNativeInvocationAuthorityB0(path)
    with pytest.raises(NativeAuthorityDeniedB0):
        reopened.mint_diagnostic(
            _same_generation("fresh-tool"), invocation_id="fresh-invocation",
            deadline_unix=time.time() + 90,
        )
    with sqlite3.connect(path) as sql:
        assert sql.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert sql.execute("SELECT COUNT(*) FROM native_invocation_authority_b0").fetchone()[0] == 0


def test_s0_existing_tickets_burn_and_fresh_mint_denied(tmp_path):
    journal = DurableNativeInvocationAuthorityB0(tmp_path / "s0-multi.sqlite")
    tokens = [
        journal.mint_diagnostic(
            _same_generation("issued-" + str(i)), invocation_id="i-" + str(i),
            deadline_unix=time.time() + 100,
        )
        for i in range(3)
    ]
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 3
    for token in tokens:
        assert journal.state(token.identity) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.mint_diagnostic(
            _same_generation("fresh"), invocation_id="fresh-i",
            deadline_unix=time.time() + 90,
        )


def test_s0_revoke_idempotent_tombstone_irreversible(tmp_path):
    path = tmp_path / "s0-repeat.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    scope = dict(owner_id="owner-revoked", execution_id="exec-revoked",
                 sandbox_id="sandbox-revoked", lease_generation="g-1")
    assert journal.revoke_generation(**scope) == 0
    with sqlite3.connect(path) as sql:
        original = sql.execute(
            "SELECT revoked_at_unix FROM native_generation_revocations_b0"
        ).fetchone()
        assert original is not None
    for _ in range(3):
        assert journal.revoke_generation(**scope) == 0
    with sqlite3.connect(path) as sql:
        assert sql.execute(
            "SELECT revoked_at_unix FROM native_generation_revocations_b0"
        ).fetchone() == original
    assert not hasattr(journal, "unrevoke_generation")
    with pytest.raises(NativeAuthorityDeniedB0):
        DurableNativeInvocationAuthorityB0(path).mint_diagnostic(
            _same_generation("future"), invocation_id="future",
            deadline_unix=time.time() + 90,
        )


def test_s0_stale_forged_expired_preflight_never_escapes_revoke(tmp_path):
    path = tmp_path / "s0-forgeries.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    ticket = journal.mint_diagnostic(
        _same_generation("pre-revoke"), invocation_id="pre-revoke-inv",
        deadline_unix=time.time() + 90,
    )
    journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    )
    candidates = (
        ticket,
        replace(ticket, _secret=bytes([0x77]) * 32),
        replace(ticket, sandbox_id="foreign-sandbox"),
    )
    with patch.object(subprocess, "Popen") as spawn:
        for forged in candidates:
            with pytest.raises(NativeAuthorityDeniedB0):
                journal.deny_before_spawn(forged)
        with patch(
            "se.src.runtimes.capability.native_invocation_authority_b0.time.time",
            return_value=ticket.deadline_unix + 1,
        ):
            with pytest.raises(NativeAuthorityDeniedB0):
                journal.deny_before_spawn(ticket)
        spawn.assert_not_called()
    assert journal.state(ticket.identity) == ("DENIED", "PREFLIGHT_DENIED")


def test_s0_two_process_mint_vs_revoke_serializable(tmp_path):
    """Twenty independent OS-worker races, each with a new generation."""
    path = tmp_path / "s0-twenty-races.sqlite"
    DurableNativeInvocationAuthorityB0(path)
    for index in range(20):
        generation = f"race-{index}"
        tool_id = f"call-{index}"
        gate = tmp_path / f"race-{index}.go"
        header = (
            "import pathlib,sys,time\n"
            "from se.src.runtimes.capability.native_invocation_authority_b0 "
            "import DurableNativeInvocationAuthorityB0, NativeAuthorityDeniedB0\n"
            "from se.src.runtimes.capability.native_broker_b0 import SpawnIdentity\n"
            f"j=DurableNativeInvocationAuthorityB0({str(path)!r})\n"
            f"while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.002)\n"
        )
        revoker = header + (
            "j.revoke_generation(owner_id='owner-revoked',"
            "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
            f"lease_generation={generation!r})\n"
        )
        minter = header + (
            "try:\n"
            f" j.mint_diagnostic(SpawnIdentity('owner-revoked','exec-revoked',{tool_id!r},"
            f"'sandbox-revoked',{generation!r}),"
            f"invocation_id={'inv-'+tool_id!r},deadline_unix=time.time()+90)\n"
            "except NativeAuthorityDeniedB0: pass\n"
        )
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for script in (revoker, minter)
        ]
        gate.touch()
        for worker in workers:
            _, stderr = worker.communicate(timeout=30)
            assert worker.returncode == 0, stderr.decode("utf-8", "replace")[-1200:]
        with sqlite3.connect(path) as sql:
            assert sql.execute(
                """SELECT COUNT(*) FROM native_invocation_authority_b0
                   WHERE lease_generation=? AND state='ISSUED'""",
                (generation,),
            ).fetchone()[0] == 0
            assert sql.execute(
                """SELECT COUNT(*) FROM attempts
                   WHERE lease_generation=? AND state='RESERVED'""",
                (generation,),
            ).fetchone()[0] == 0
        with pytest.raises(NativeAuthorityDeniedB0):
            DurableNativeInvocationAuthorityB0(path).mint_diagnostic(
                _same_generation("after-" + tool_id, generation),
                invocation_id="after-" + tool_id,
                deadline_unix=time.time() + 90,
            )


def test_s0_two_process_preflight_vs_revoke_single_winner(tmp_path):
    path = tmp_path / "s0-preflight-race.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    token = journal.mint_diagnostic(
        _same_generation("preflight-race"), invocation_id="preflight-inv",
        deadline_unix=time.time() + 90,
    )
    gate = tmp_path / "preflight-go"
    header = (
        "import pathlib,time\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0,NativeAuthorityDeniedB0,"
        "OpaqueSpawnAuthorityB0\n"
        f"j=DurableNativeInvocationAuthorityB0({str(path)!r})\n"
        f"while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.002)\n"
    )
    preflight = header + (
        f"token=OpaqueSpawnAuthorityB0({token.owner_id!r},{token.execution_id!r},"
        f"{token.tool_call_id!r},{token.invocation_id!r},{token.sandbox_id!r},"
        f"{token.lease_generation!r},{token.deadline_unix!r},"
        f"bytes.fromhex({token._secret.hex()!r}))\n"
        "try: j.deny_before_spawn(token)\n"
        "except NativeAuthorityDeniedB0: pass\n"
    )
    revoker = header + (
        "j.revoke_generation(owner_id='owner-revoked',"
        "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
        "lease_generation='g-1')\n"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for script in (preflight, revoker)
    ]
    gate.touch()
    for worker in workers:
        _, stderr = worker.communicate(timeout=30)
        assert worker.returncode == 0, stderr.decode("utf-8", "replace")[-1200:]
    assert journal.state(token.identity) == ("DENIED", "PREFLIGHT_DENIED")
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.mint_diagnostic(
            _same_generation("post-preflight"), invocation_id="post-preflight",
            deadline_unix=time.time() + 90,
        )


def test_s0_os_exit_after_revoke_commit_preserves_tombstone(tmp_path):
    """A separate process crashes after COMMIT and restart still denies mint."""
    path = tmp_path / "s0-postcommit.sqlite"
    DurableNativeInvocationAuthorityB0(path)
    script = (
        "import os\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0\n"
        f"j=DurableNativeInvocationAuthorityB0({str(path)!r})\n"
        "assert j.revoke_generation(owner_id='owner-revoked',"
        "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
        "lease_generation='g-1')==0\n"
        "os._exit(93)\n"
    )
    worker = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=30,
    )
    assert worker.returncode == 93, worker.stderr.decode("utf-8", "replace")[-1200:]
    with pytest.raises(NativeAuthorityDeniedB0):
        DurableNativeInvocationAuthorityB0(path).mint_diagnostic(
            _same_generation("post-exit"), invocation_id="post-exit",
            deadline_unix=time.time() + 90,
        )


def test_s0_os_exit_before_commit_rolls_back_atomically(tmp_path):
    """Execute real revoke_generation, force os._exit at its SQL COMMIT boundary."""
    path = tmp_path / "s0-precommit.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    token = journal.mint_diagnostic(
        _same_generation("precommit"), invocation_id="precommit-inv",
        deadline_unix=time.time() + 90,
    )
    child = (
        "import os,sqlite3\n"
        "from se.src.runtimes.capability.native_invocation_authority_b0 "
        "import DurableNativeInvocationAuthorityB0\n"
        f"path={str(path)!r}\n"
        "j=DurableNativeInvocationAuthorityB0(path)\n"
        "class CrashAtCommit(sqlite3.Connection):\n"
        " def execute(self, sql, *args, **kwargs):\n"
        "  if sql.strip().upper() == 'COMMIT': os._exit(94)\n"
        "  return super().execute(sql,*args,**kwargs)\n"
        "def connect_fault():\n"
        " db=sqlite3.connect(path,timeout=15,isolation_level=None,factory=CrashAtCommit)\n"
        " db.execute('PRAGMA synchronous=FULL')\n"
        " return db\n"
        "j.attempts._connect=connect_fault\n"
        "j.revoke_generation(owner_id='owner-revoked',"
        "execution_id='exec-revoked',sandbox_id='sandbox-revoked',"
        "lease_generation='g-1')\n"
        "os._exit(29)\n"
    )
    worker = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, timeout=30,
    )
    assert worker.returncode == 94, worker.stderr.decode("utf-8", "replace")[-1200:]
    assert journal.state(token.identity) == ("RESERVED", "ISSUED")
    with sqlite3.connect(path) as sql:
        assert sql.execute(
            "SELECT COUNT(*) FROM native_generation_revocations_b0"
        ).fetchone()[0] == 0
    # Crash-before-COMMIT was NOT a successful revoke. Explicit recovery must
    # fence uncertainty; a later real revoke commits the permanent tombstone.
    assert journal.revoke_generation(
        owner_id="owner-revoked", execution_id="exec-revoked",
        sandbox_id="sandbox-revoked", lease_generation="g-1",
    ) == 1
    with pytest.raises(NativeAuthorityDeniedB0):
        journal.mint_diagnostic(
            _same_generation("after-real-revoke"), invocation_id="after-revoke",
            deadline_unix=time.time() + 90,
        )


def test_s0_restart_reconcile_never_resurrects_generation(tmp_path):
    path = tmp_path / "s0-reconcile.sqlite"
    journal = DurableNativeInvocationAuthorityB0(path)
    scope = dict(owner_id="owner-revoked", execution_id="exec-revoked",
                 sandbox_id="sandbox-revoked", lease_generation="g-1")
    assert journal.revoke_generation(**scope) == 0
    other = journal.mint_diagnostic(
        _same_generation("other-uncertain", "g-2"),
        invocation_id="other-uncertain-inv", deadline_unix=time.time() + 90,
    )
    restarted = DurableNativeInvocationAuthorityB0(path)
    assert restarted.reconcile_after_restart() == 1
    assert restarted.state(other.identity) == ("OUTCOME_UNKNOWN", "OUTCOME_UNKNOWN")
    assert restarted.reconcile_after_restart() == 0
    with pytest.raises(NativeAuthorityDeniedB0):
        restarted.mint_diagnostic(
            _same_generation("after-reconcile"), invocation_id="after-reconcile",
            deadline_unix=time.time() + 90,
        )
    with sqlite3.connect(path) as sql:
        assert sql.execute(
            "SELECT COUNT(*) FROM native_generation_revocations_b0"
        ).fetchone()[0] == 1
