from __future__ import annotations

import time

from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerState,
)


def _prepare_running(ledger: ClientInvocationLedger, invocation_id: str) -> None:
    ledger.prepare(
        client_id="client-r11-f0",
        principal_id="user-r11-f0",
        invocation_id=invocation_id,
        capability_id="tool.side_effect",
        capability_version="1",
        request_fingerprint=invocation_id,
        idempotency="UNKNOWN",
    )
    ledger.mark_running(
        client_id="client-r11-f0",
        principal_id="user-r11-f0",
        invocation_id=invocation_id,
    )


def test_r11_f0_generic_ttl_collects_terminal_but_preserves_running(tmp_path):
    ledger = ClientInvocationLedger(
        tmp_path / "r11-f0-client-invocations.sqlite3",
        terminal_ttl=0.01,
    )

    _prepare_running(ledger, "terminal")
    _prepare_running(ledger, "running")

    ledger.commit_terminal(
        client_id="client-r11-f0",
        principal_id="user-r11-f0",
        invocation_id="terminal",
        terminal_type="result",
        terminal_payload={"output": "ok"},
    )

    assert ledger.purge_expired(now=time.time() + 3600.0) == 1

    assert ledger.get(
        client_id="client-r11-f0",
        principal_id="user-r11-f0",
        invocation_id="terminal",
    ) is None

    running = ledger.get(
        client_id="client-r11-f0",
        principal_id="user-r11-f0",
        invocation_id="running",
    )
    assert running is not None
    assert running.state is ClientInvocationLedgerState.RUNNING


def test_r11_f0_running_crash_evidence_survives_repeated_far_future_gc(tmp_path):
    ledger = ClientInvocationLedger(
        tmp_path / "r11-f0-running.sqlite3",
        terminal_ttl=0.01,
    )
    _prepare_running(ledger, "running-crash-evidence")

    for offset in (3600.0, 86400.0, 365 * 86400.0):
        assert ledger.purge_expired(now=time.time() + offset) == 0
        record = ledger.get(
            client_id="client-r11-f0",
            principal_id="user-r11-f0",
            invocation_id="running-crash-evidence",
        )
        assert record is not None
        assert record.state is ClientInvocationLedgerState.RUNNING
