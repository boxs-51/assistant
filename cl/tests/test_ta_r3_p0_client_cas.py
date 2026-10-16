"""TA-V1-R3 P0-CLIENT-CAS: one-winner first-effect admission.

No physical handler, network, or subprocess is invoked.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerConflict,
    ClientInvocationLedgerState,
)


def _prepare(ledger: ClientInvocationLedger) -> None:
    ledger.prepare(
        client_id="client-ta-r3",
        principal_id="user-ta-r3",
        invocation_id="ta-r3-attempt",
        capability_id="safe.echo",
        capability_version="1.0",
        request_fingerprint="f" * 64,
        idempotency="NON_IDEMPOTENT",
    )


def _claim(ledger: ClientInvocationLedger) -> bool:
    try:
        record = ledger.mark_running(
            client_id="client-ta-r3",
            principal_id="user-ta-r3",
            invocation_id="ta-r3-attempt",
        )
        assert record.state is ClientInvocationLedgerState.RUNNING
        return True
    except ClientInvocationLedgerConflict:
        return False


def test_one_client_ledger_issues_only_one_first_effect_permit(tmp_path):
    ledger = ClientInvocationLedger(tmp_path / "single.sqlite3")
    _prepare(ledger)
    assert _claim(ledger)
    assert not _claim(ledger)
    assert not _claim(ledger)


def test_separate_client_ledger_instances_share_a_durable_cas(tmp_path):
    path = tmp_path / "shared.sqlite3"
    left = ClientInvocationLedger(path)
    right = ClientInvocationLedger(path)
    _prepare(left)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_claim, left),
            pool.submit(_claim, right),
        ]
        winners = [future.result() for future in futures]
    assert sorted(winners) == [False, True]
    third = ClientInvocationLedger(path)
    assert not _claim(third)


def test_terminal_record_cannot_reenter_first_effect(tmp_path):
    ledger = ClientInvocationLedger(tmp_path / "terminal.sqlite3")
    _prepare(ledger)
    assert _claim(ledger)
    ledger.commit_terminal(
        client_id="client-ta-r3",
        principal_id="user-ta-r3",
        invocation_id="ta-r3-attempt",
        terminal_type="result",
        terminal_payload={"output": "safe"},
    )
    with pytest.raises(ClientInvocationLedgerConflict):
        ledger.mark_running(
            client_id="client-ta-r3",
            principal_id="user-ta-r3",
            invocation_id="ta-r3-attempt",
        )


def test_running_mcp_replay_is_blocked_even_if_registry_claims_deduplicated(tmp_path):
    import time
    from types import SimpleNamespace

    from cl.src.core.capability_dispatcher import CapabilityDispatcher

    capability_id = "mcp__ingress__replay_probe"
    invocation_id = "mcp-running-replay"
    arguments = {"value": "x"}
    fingerprint = CapabilityDispatcher._request_fingerprint(
        capability_id, "1.0", arguments
    )
    path = tmp_path / "mcp-running.sqlite3"
    ledger = ClientInvocationLedger(path)
    ledger.prepare(
        client_id="client-ta-r3",
        principal_id="user-ta-r3",
        invocation_id=invocation_id,
        capability_id=capability_id,
        capability_version="1.0",
        request_fingerprint=fingerprint,
        idempotency="DEDUPLICATED",
    )
    ledger.mark_running(
        client_id="client-ta-r3",
        principal_id="user-ta-r3",
        invocation_id=invocation_id,
    )

    calls = []

    class _Registry:
        def __init__(self):
            self.tools = {
                capability_id: {
                    "func": lambda value: calls.append(value) or value,
                    "metadata": {
                        "name": capability_id,
                        "version": "1.0",
                        "idempotency": "DEDUPLICATED",
                        "base_risk": "LOW",
                    },
                }
            }

        def get_tool(self, name):
            return self.tools.get(name)

    class _Realtime:
        connection_id = "client-conn-2"

        def __init__(self):
            self.events = []

        def send_result(self, *args, **kwargs):
            self.events.append(("result", args, kwargs))

        def send_error(self, *args, **kwargs):
            self.events.append(("error", args, kwargs))

        def send_cancelled(self, *args, **kwargs):
            self.events.append(("cancelled", args, kwargs))

        def send_reconciliation(self, *args, **kwargs):
            self.events.append(("reconciliation", args, kwargs))

    realtime = _Realtime()
    dispatcher = CapabilityDispatcher(
        _Registry(),
        realtime,
        invocation_ledger=ClientInvocationLedger(path),
        client_id="client-ta-r3",
        principal_id="user-ta-r3",
    )
    dispatcher.update_registration_snapshot([capability_id])
    envelope = {
        "type": "capability.invoke",
        "connection_id": realtime.connection_id,
        "execution_id": "agent-root",
        "invocation_id": invocation_id,
        "trace_id": "trace-mcp-replay",
        "payload": {
            "capability_id": capability_id,
            "capability_version": "1.0",
            "arguments": arguments,
            "request_fingerprint": fingerprint,
        },
    }
    try:
        dispatcher.dispatch(envelope)
    finally:
        dispatcher.shutdown()
    assert calls == []
    assert realtime.events == []
