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
