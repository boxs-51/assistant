"""Issue #371 private broker adapter: synthetic contract checks only.

These tests do not spawn untrusted tool commands or certify OS isolation.
A single TEST-OWNED Python worker hard-exits after persisting a synthetic
reservation; this is not a native broker or kernel process-tree experiment.
Real broker authority, process-tree cleanup and AE receipt persistence remain
the independent #164/#359/#417 integration/security gates.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tools.v1 import terminal_tool as terminal_module
from tools.v1.terminal_tool import TerminalTool


class _SyntheticPort:
    def __init__(self, completion=None, *, verified=True, error=None):
        self.completion = completion or terminal_module._BrokerRunCompletion(
            outcome="EXITED", tree_ref=object(), receipt=object(),
            exit_code=0, stdout=b"synthetic-ok", stderr=b"",
            stdout_bytes_observed=12, stderr_bytes_observed=0, duration_ms=7,
        )
        self.verified = verified
        self.error = error
        self.calls = []

    async def run_preconfined(self, **kwargs):
        self.calls.append(("run", kwargs))
        if self.error is not None:
            raise self.error
        return self.completion

    async def verify_receipt(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return self.verified


def _call(port, **overrides):
    kwargs = {
        "command": "synthetic-never-executed",
        "timeout": 3,
        "sandbox_relative_cwd": "subdir",
        "encoding": "utf-8",
        "spawn_port": port,
        "authority": object(),
    }
    kwargs.update(overrides)
    return asyncio.run(TerminalTool()._run_broker_owned(**kwargs))


def test_private_success_preserves_bounded_shape_and_never_calls_host_popen():
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["ok"] is True
    data = result["data"]
    assert data["stdout"] == "synthetic-ok"
    assert data["cwd"] == "subdir"
    assert data["stdout_bytes"] == 12
    assert data["exit_code"] == 0
    assert len(port.calls) == 2
    limits = port.calls[0][1]
    assert limits["max_stdout_bytes"] == terminal_module.MAX_STDOUT_BYTES
    assert limits["max_stderr_bytes"] == terminal_module.MAX_STDERR_BYTES
    assert limits["max_total_output_bytes"] == terminal_module.MAX_TOTAL_OUTPUT_BYTES
    assert "receipt" not in repr(result)
    assert "tree_ref" not in repr(result)
    assert "authority" not in repr(result)


@pytest.mark.parametrize(
    "relative_cwd",
    ["/etc", "../escape", "sub/../other", r"C:\Windows", r"sub\directory",
     "//server/share", "sub\x00dir", ""],
)
def test_private_relative_cwd_denies_ambiguous_or_escaping_input(relative_cwd):
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port, sandbox_relative_cwd=relative_cwd)
        popen.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert port.calls == []


def test_missing_private_authority_or_port_denied_before_dispatch():
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        no_auth = _call(port, authority=None)
        no_port = _call(None)
        popen.assert_not_called()
    assert no_auth["error"]["code"] == "SANDBOX_PRESPAWN_DENIED"
    assert no_port["error"]["code"] == "SANDBOX_PRESPAWN_DENIED"
    assert port.calls == []


def test_stale_receipt_is_unknown_and_never_success():
    port = _SyntheticPort(verified=False)
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["details"]["replay_safe"] is False


def test_broker_disconnection_after_possible_spawn_is_nonreplayable_unknown():
    port = _SyntheticPort(error=ConnectionError("private root /secret/token"))
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert "secret" not in repr(result)
    assert len(port.calls) == 1


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [("TIMEOUT", "TERMINAL_TIMEOUT"),
     ("OUTPUT_LIMIT", "TERMINAL_OUTPUT_LIMIT"),
     ("IO_ERROR", "TERMINAL_IO_ERROR")],
)
def test_verified_broker_errors_preserve_existing_tool_error_codes(outcome, expected_code):
    completion = terminal_module._BrokerRunCompletion(
        outcome=outcome, tree_ref=object(), receipt=object(),
    )
    result = _call(_SyntheticPort(completion))
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code


def test_unverified_unknown_outcome_does_not_leak_success_or_retry():
    completion = terminal_module._BrokerRunCompletion(
        outcome="OUTCOME_UNKNOWN", tree_ref=object(), receipt=object(),
    )
    result = _call(_SyntheticPort(completion))
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["details"]["replay_safe"] is False


def test_oversized_broker_output_is_rejected_after_receipt_check():
    completion = terminal_module._BrokerRunCompletion(
        outcome="EXITED", tree_ref=object(), receipt=object(), exit_code=0,
        stdout=b"x", stdout_bytes_observed=terminal_module.MAX_STDOUT_BYTES + 1,
    )
    port = _SyntheticPort(completion)
    result = _call(port)
    assert result["error"]["code"] == "TERMINAL_OUTPUT_LIMIT"
    assert [k for k, _ in port.calls] == ["run", "verify"]


def test_cancellation_is_not_mapped_to_success_or_predispatch_deny():
    port = _SyntheticPort(error=asyncio.CancelledError())
    async def invoke():
        await TerminalTool()._run_broker_owned(
            command="synthetic", spawn_port=port, authority=object(),
        )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(invoke())


def test_public_metadata_and_dispatch_have_no_broker_arguments():
    exports = {x["id"]: x for x in terminal_module.TOOL_METADATA["exports"]}
    assert set(exports) == {"terminal.run", "terminal.launch"}
    for item in exports.values():
        assert item["input_schema"]["additionalProperties"] is False
        assert "spawn_port" not in item["input_schema"]["properties"]
        assert "authority" not in item["input_schema"]["properties"]
    # The public method still uses the existing DIRECT behavior, and cannot
    # turn private kwargs into an internal broker call.
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = TerminalTool().execute(
            action="run", command="", spawn_port=_SyntheticPort(),
            authority=object(),
        )
        popen.assert_not_called()
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_missing_tree_or_receipt_never_returns_success():
    for tree_ref, receipt in [(None, object()), (object(), None)]:
        completion = terminal_module._BrokerRunCompletion(
            outcome="EXITED", tree_ref=tree_ref, receipt=receipt,
            exit_code=0,
        )
        port = _SyntheticPort(completion)
        result = _call(port)
        assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
        assert [kind for kind, _ in port.calls] == ["run"]


def test_valid_public_execute_cannot_inject_private_port():
    tool = TerminalTool()
    port = _SyntheticPort()
    with patch.object(tool, "run", return_value={"ok": True, "data": "DIRECT"}) as run:
        answer = tool.execute(
            action="run", command="synthetic-never-executed",
            spawn_port=port, authority=object(),
        )
    assert answer["data"] == "DIRECT"
    run.assert_called_once()
    assert port.calls == []



# SECURITY NEGATIVE GATES: test-only doubles; no production broker authority.
# The authentic SE #164 issuer, kernel tree, and root cleanup must be tested
# separately. A self-asserted bool from this test port is not certification.


@pytest.mark.parametrize(
    "authority_variant",
    [
        ("other-owner", "exec-a", 9, "inv-a", "call-a"),
        ("owner-a", "other-exec", 9, "inv-a", "call-a"),
        ("owner-a", "exec-a", 8, "inv-a", "call-a"),
        ("owner-a", "exec-a", 9, "other-inv", "call-a"),
        ("owner-a", "exec-a", 9, "inv-a", "other-call"),
    ],
)
def test_stale_foreign_generation_invocation_and_tool_call_never_succeed(
    authority_variant,
):
    expected = ("owner-a", "exec-a", 9, "inv-a", "call-a")

    class _ScopeCheckingPort(_SyntheticPort):
        def __init__(self):
            super().__init__()
            self.dispatched = 0

        async def run_preconfined(self, **kwargs):
            self.calls.append(("attempt", kwargs))
            if kwargs["authority"] != expected:
                raise PermissionError("stale or foreign owner/lease/call")
            self.dispatched += 1
            return self.completion

    port = _ScopeCheckingPort()
    with patch.object(terminal_module.subprocess, "Popen") as host:
        result = _call(port, authority=authority_variant)
        host.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["retryable"] is False
    assert result["error"]["details"]["replay_safe"] is False
    assert port.dispatched == 0
    assert [kind for kind, _ in port.calls] == ["attempt"]


def test_consumed_authority_cannot_repeat_an_effectful_attempt():
    authority = object()

    class _OneUsePort(_SyntheticPort):
        def __init__(self):
            super().__init__(verified=False)
            self.used = set()
            self.dispatched = 0

        async def run_preconfined(self, **kwargs):
            token = kwargs["authority"]
            if token in self.used:
                raise RuntimeError("consumed authority cannot be replayed")
            self.used.add(token)
            self.dispatched += 1
            return self.completion

    port = _OneUsePort()
    with patch.object(terminal_module.subprocess, "Popen") as host:
        first = _call(port, authority=authority)
        second = _call(port, authority=authority)
        host.assert_not_called()
    for result in (first, second):
        assert result["ok"] is False
        assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
        assert result["error"]["retryable"] is False
        assert result["error"]["details"]["replay_safe"] is False
    assert port.dispatched == 1


@pytest.mark.parametrize("verified", [False, None, 1, "VERIFIED_EMPTY", {}])
@pytest.mark.parametrize("outcome", ["EXITED", "TIMEOUT", "OUTPUT_LIMIT", "IO_ERROR"])
def test_uncertified_tree_cleanup_overrides_all_broker_outcomes(verified, outcome):
    completion = terminal_module._BrokerRunCompletion(
        outcome=outcome,
        tree_ref=object(),
        receipt=object(),
        exit_code=0 if outcome == "EXITED" else None,
    )
    port = _SyntheticPort(completion=completion, verified=verified)
    with patch.object(terminal_module.subprocess, "Popen") as host:
        result = _call(port)
        host.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["retryable"] is False
    assert result["error"]["details"]["replay_safe"] is False
    assert [name for name, _ in port.calls] == ["run", "verify"]


def test_receipt_revoked_after_dispatch_never_publishes_success():
    class _RevocationPort(_SyntheticPort):
        def __init__(self):
            super().__init__()
            self.active_generation = 4
            self.dispatched = 0

        async def run_preconfined(self, **kwargs):
            self.dispatched += 1
            self.calls.append(("run", kwargs))
            # Simulates a concurrent lease revoke while the child is exiting.
            self.active_generation += 1
            return self.completion

        async def verify_receipt(self, **kwargs):
            self.calls.append(("verify", kwargs))
            return self.active_generation == 4

    port = _RevocationPort()
    with patch.object(terminal_module.subprocess, "Popen") as host:
        result = _call(port)
        host.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert port.dispatched == 1
    assert [kind for kind, _ in port.calls] == ["run", "verify"]


@pytest.mark.parametrize("phase", ["collect", "receipt"])
def test_async_cancellation_propagates_and_requires_broker_quarantine(phase):
    async def scenario():
        class _CancellationPort(_SyntheticPort):
            def __init__(self):
                super().__init__()
                self.entered = asyncio.Event()
                self.quarantined = False
                self.dispatch_count = 0

            async def run_preconfined(self, **kwargs):
                if self.quarantined:
                    raise RuntimeError("quarantined invocation")
                self.dispatch_count += 1
                if phase == "collect":
                    self.entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        # Responsibility of a real trusted SE broker, not a
                        # cleanup guarantee from TerminalTool/finally.
                        self.quarantined = True
                return self.completion

            async def verify_receipt(self, **kwargs):
                if phase == "receipt":
                    self.entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        self.quarantined = True
                return False

        port = _CancellationPort()
        auth = object()
        task = asyncio.create_task(
            TerminalTool()._run_broker_owned(
                command="synthetic-never-executed",
                spawn_port=port, authority=auth,
            )
        )
        await asyncio.wait_for(port.entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert task.cancelled()
        assert port.quarantined
        assert port.dispatch_count == 1
        # New adapter call MUST NOT re-dispatch an effectful invocation after
        # the port has fenced it, even if coroutine state was discarded.
        again = await TerminalTool()._run_broker_owned(
            command="synthetic-never-executed",
            spawn_port=port, authority=auth,
        )
        assert not again["ok"]
        assert again["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
        assert again["error"]["details"]["replay_safe"] is False
        assert port.dispatch_count == 1

    with patch.object(terminal_module.subprocess, "Popen") as host:
        asyncio.run(scenario())
        host.assert_not_called()


def test_hard_worker_exit_then_restart_keeps_attempt_nonreplayable(tmp_path):
    """Real test-owned os._exit; fake durable ledger, NO native command run."""
    import sqlite3
    import subprocess
    import sys

    ledger = tmp_path / "synthetic_attempts.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE attempts (invocation TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )

    # Separate Python worker writes and fsync-commits the hypothetical effect
    # reservation, then dies without a success receipt. No child process is
    # launched by TerminalTool and no native cleanup is being simulated as real.
    worker = (
        "import os, sqlite3, sys\n"
        "with sqlite3.connect(sys.argv[1]) as db:\n"
        "    db.execute('PRAGMA synchronous=FULL')\n"
        "    db.execute(\"INSERT INTO attempts VALUES (?, ?)\", "
        "('inv-a', 'OUTCOME_UNKNOWN'))\n"
        "os._exit(73)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", worker, str(ledger)],
        check=False, capture_output=True, timeout=15,
    )
    assert child.returncode == 73, child.stderr.decode(errors="replace")

    class _RecoveryPort(_SyntheticPort):
        def __init__(self, db_path):
            super().__init__()
            self.db_path = db_path
            self.dispatched = 0

        async def run_preconfined(self, **kwargs):
            with sqlite3.connect(self.db_path) as db:
                row = db.execute(
                    "SELECT state FROM attempts WHERE invocation=?",
                    ("inv-a",),
                ).fetchone()
            if row is not None:
                raise RuntimeError("durable reservation: OUTCOME_UNKNOWN")
            self.dispatched += 1
            return self.completion

    # A brand-new port instance represents restarted application memory.
    for _ in range(2):
        restarted = _RecoveryPort(ledger)
        with patch.object(terminal_module.subprocess, "Popen") as host:
            result = _call(restarted)
            host.assert_not_called()
        assert result["ok"] is False
        assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
        assert result["error"]["retryable"] is False
        assert result["error"]["details"]["replay_safe"] is False
        assert restarted.dispatched == 0
    with sqlite3.connect(ledger) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM attempts WHERE invocation='inv-a'"
        ).fetchone() == (1,)


def test_receipt_verification_failure_redacts_privileged_broker_state():
    secret = "/privileged/lease/secret-credential"

    class _SensitiveFailurePort(_SyntheticPort):
        async def verify_receipt(self, **kwargs):
            raise PermissionError("forged receipt " + secret)

    port = _SensitiveFailurePort()
    with patch.object(terminal_module.subprocess, "Popen") as host:
        result = _call(port)
        host.assert_not_called()
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["details"]["replay_safe"] is False
    assert secret not in repr(result)
