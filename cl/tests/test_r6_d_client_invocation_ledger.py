import time
from types import SimpleNamespace

import pytest

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerConflict,
    ClientInvocationLedgerState,
)


class _Realtime:
    def __init__(self, connection_id="conn-1"):
        self.connection_id = connection_id
        self.events = []

    def send_result(self, invocation_id, result, **correlation):
        self.events.append(("result", invocation_id, {"output": result}))

    def send_error(self, invocation_id, **payload):
        self.events.append(("error", invocation_id, payload))

    def send_cancelled(self, invocation_id, **correlation):
        self.events.append(("cancelled", invocation_id, {}))

    def send_reconciliation(
        self,
        invocation_id,
        payload,
        **correlation,
    ):
        self.events.append(
            ("reconciliation", invocation_id, dict(payload))
        )


class _FailingRealtime(_Realtime):
    def send_result(self, invocation_id, result, **correlation):
        self.events.append(("send-attempt", invocation_id, {"output": result}))
        raise ConnectionError("result transport lost")


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for dispatcher state")


def _registry(func, *, idempotency="NON_IDEMPOTENT"):
    return SimpleNamespace(
        tools={
            "tool.side_effect": {
                "func": func,
                "metadata": {
                    "name": "tool.side_effect",
                    "version": "3.0",
                    "idempotency": idempotency,
                    "base_risk": "LOW",
                },
            }
        }
    )


def _fingerprint(dispatcher, value="x"):
    return dispatcher._request_fingerprint(
        "tool.side_effect",
        "3.0",
        {"value": value},
    )


def _invoke(dispatcher, connection_id="conn-1", value="x"):
    return {
        "type": "capability.invoke",
        "connection_id": connection_id,
        "execution_id": "exec-1",
        "invocation_id": "inv-1",
        "trace_id": "trace-1",
        "payload": {
            "capability_id": "tool.side_effect",
            "capability_version": "3.0",
            "arguments": {"value": value},
            "request_fingerprint": _fingerprint(dispatcher, value),
        },
    }


def _reconcile(dispatcher, connection_id="conn-2", fingerprint=None):
    dispatcher.reconcile(
        {
            "type": "capability.reconcile",
            "connection_id": connection_id,
            "execution_id": "exec-1",
            "invocation_id": "inv-1",
            "trace_id": "trace-1",
            "payload": {
                "capability_id": "tool.side_effect",
                "capability_version": "3.0",
                "request_fingerprint": (
                    fingerprint or _fingerprint(dispatcher)
                ),
            },
        }
    )


def _dispatcher(registry, realtime, ledger):
    dispatcher = CapabilityDispatcher(
        registry,
        realtime,
        invocation_ledger=ledger,
        client_id="client-1",
        principal_id="user-1",
    )
    dispatcher.update_registration_snapshot(["tool.side_effect"])
    return dispatcher


def test_sqlite_ledger_orders_prepared_running_terminal(tmp_path):
    ledger = ClientInvocationLedger(tmp_path / "invocations.sqlite3")
    prepared = ledger.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint="f" * 64,
        idempotency="NON_IDEMPOTENT",
    )
    assert prepared.state is ClientInvocationLedgerState.PREPARED

    running = ledger.mark_running(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
    )
    assert running.state is ClientInvocationLedgerState.RUNNING

    terminal = ledger.commit_terminal(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        terminal_type="result",
        terminal_payload={"output": {"ok": True}},
    )
    assert terminal.state is ClientInvocationLedgerState.TERMINAL
    assert terminal.terminal_payload == {"output": {"ok": True}}

    with pytest.raises(ClientInvocationLedgerConflict):
        ledger.mark_running(
            client_id="client-1",
            principal_id="user-1",
            invocation_id="inv-1",
        )


def test_terminal_survives_client_process_restart_and_reconciles_exactly(
    tmp_path,
):
    path = tmp_path / "invocations.sqlite3"
    called = []

    def tool(value):
        called.append(value)
        return {"value": value, "ordinal": len(called)}

    first_ledger = ClientInvocationLedger(path)
    first_realtime = _FailingRealtime("conn-1")
    first = _dispatcher(_registry(tool), first_realtime, first_ledger)
    try:
        first.dispatch(_invoke(first, "conn-1"))
        _wait(lambda: bool(first_realtime.events))
        assert called == ["x"]
        record = first_ledger.get(
            client_id="client-1",
            principal_id="user-1",
            invocation_id="inv-1",
        )
        assert record is not None
        assert record.state is ClientInvocationLedgerState.TERMINAL
    finally:
        first.shutdown()

    second_ledger = ClientInvocationLedger(path)
    second_realtime = _Realtime("conn-2")
    second = _dispatcher(_registry(tool), second_realtime, second_ledger)
    try:
        _reconcile(second)
        response = second_realtime.events[0][2]
        assert response["status"] == "TERMINAL"
        assert response["terminal_type"] == "result"
        assert response["terminal_payload"] == {
            "output": {
                "value": "x",
                "ordinal": 1,
            }
        }
        assert response["ledger_state"] == "TERMINAL"
        assert called == ["x"]
    finally:
        second.shutdown()


def test_running_after_restart_is_unknown_and_duplicate_invoke_is_blocked(
    tmp_path,
):
    path = tmp_path / "invocations.sqlite3"
    bootstrap = ClientInvocationLedger(path)
    bootstrap.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint=CapabilityDispatcher._request_fingerprint(
            "tool.side_effect",
            "3.0",
            {"value": "x"},
        ),
        idempotency="NON_IDEMPOTENT",
    )
    bootstrap.mark_running(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
    )

    called = []
    realtime = _Realtime("conn-2")
    dispatcher = _dispatcher(
        _registry(lambda value: called.append(value) or value),
        realtime,
        ClientInvocationLedger(path),
    )
    try:
        _reconcile(dispatcher)
        assert realtime.events[0][2]["status"] == "UNKNOWN"
        assert realtime.events[0][2]["ledger_state"] == "RUNNING"

        realtime.events.clear()
        dispatcher.dispatch(_invoke(dispatcher, "conn-2"))
        time.sleep(0.05)
        assert called == []
        assert realtime.events == []
    finally:
        dispatcher.shutdown()


def test_prepared_restart_can_execute_only_after_explicit_invoke(tmp_path):
    path = tmp_path / "invocations.sqlite3"
    ledger = ClientInvocationLedger(path)
    fingerprint = CapabilityDispatcher._request_fingerprint(
        "tool.side_effect",
        "3.0",
        {"value": "x"},
    )
    ledger.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint=fingerprint,
        idempotency="NON_IDEMPOTENT",
    )

    called = []
    realtime = _Realtime("conn-2")
    dispatcher = _dispatcher(
        _registry(lambda value: called.append(value) or {"value": value}),
        realtime,
        ClientInvocationLedger(path),
    )
    try:
        _reconcile(dispatcher, fingerprint=fingerprint)
        assert realtime.events[0][2]["status"] == "UNKNOWN"
        assert realtime.events[0][2]["ledger_state"] == "PREPARED"
        assert called == []

        realtime.events.clear()
        dispatcher.dispatch(_invoke(dispatcher, "conn-2"))
        _wait(lambda: any(item[0] == "result" for item in realtime.events))
        assert called == ["x"]
        record = ledger.get(
            client_id="client-1",
            principal_id="user-1",
            invocation_id="inv-1",
        )
        assert record is not None
        assert record.state is ClientInvocationLedgerState.TERMINAL
    finally:
        dispatcher.shutdown()


def test_ledger_is_partitioned_by_principal_and_client(tmp_path):
    ledger = ClientInvocationLedger(tmp_path / "invocations.sqlite3")
    ledger.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint="f" * 64,
        idempotency="UNKNOWN",
    )
    assert ledger.get(
        client_id="client-1",
        principal_id="user-2",
        invocation_id="inv-1",
    ) is None
    assert ledger.get(
        client_id="client-2",
        principal_id="user-1",
        invocation_id="inv-1",
    ) is None


def test_same_invocation_different_fingerprint_conflicts(tmp_path):
    ledger = ClientInvocationLedger(tmp_path / "invocations.sqlite3")
    ledger.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint="a" * 64,
        idempotency="UNKNOWN",
    )
    with pytest.raises(ClientInvocationLedgerConflict):
        ledger.prepare(
            client_id="client-1",
            principal_id="user-1",
            invocation_id="inv-1",
            capability_id="tool.side_effect",
            capability_version="3.0",
            request_fingerprint="b" * 64,
            idempotency="UNKNOWN",
        )


def test_gc_never_erases_running_crash_evidence(tmp_path):
    ledger = ClientInvocationLedger(
        tmp_path / "invocations.sqlite3",
        terminal_ttl=0.01,
    )
    for invocation_id in ("terminal", "running"):
        ledger.prepare(
            client_id="client-1",
            principal_id="user-1",
            invocation_id=invocation_id,
            capability_id="tool.side_effect",
            capability_version="3.0",
            request_fingerprint=invocation_id,
            idempotency="UNKNOWN",
        )
        ledger.mark_running(
            client_id="client-1",
            principal_id="user-1",
            invocation_id=invocation_id,
        )
    ledger.commit_terminal(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="terminal",
        terminal_type="result",
        terminal_payload={"output": "ok"},
    )

    assert ledger.purge_expired(now=time.time() + 1.0) == 1
    assert ledger.get(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="terminal",
    ) is None
    running = ledger.get(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="running",
    )
    assert running is not None
    assert running.state is ClientInvocationLedgerState.RUNNING


def test_sqlite_handles_are_released_after_each_operation(tmp_path):
    path = tmp_path / "invocations.sqlite3"
    ledger = ClientInvocationLedger(path)
    ledger.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint="f" * 64,
        idempotency="UNKNOWN",
    )
    ledger.mark_running(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
    )
    ledger.commit_terminal(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        terminal_type="result",
        terminal_payload={"output": "ok"},
    )
    assert ledger.get(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
    ) is not None

    # Windows raises WinError 32 here if any SQLite connection handle leaked.
    path.unlink()
    assert not path.exists()



@pytest.mark.parametrize("idempotency", ["IDEMPOTENT", "DEDUPLICATED"])
def test_running_after_restart_replays_only_when_semantics_are_safe(
    tmp_path,
    idempotency,
):
    path = tmp_path / f"safe-running-{idempotency}.sqlite3"
    bootstrap = ClientInvocationLedger(path)
    fingerprint = CapabilityDispatcher._request_fingerprint(
        "tool.side_effect",
        "3.0",
        {"value": "x"},
    )
    bootstrap.prepare(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
        capability_id="tool.side_effect",
        capability_version="3.0",
        request_fingerprint=fingerprint,
        idempotency=idempotency,
    )
    bootstrap.mark_running(
        client_id="client-1",
        principal_id="user-1",
        invocation_id="inv-1",
    )

    called = []
    realtime = _Realtime("conn-2")
    dispatcher = _dispatcher(
        _registry(
            lambda value: called.append(value) or {"value": value},
            idempotency=idempotency,
        ),
        realtime,
        ClientInvocationLedger(path),
    )
    try:
        dispatcher.dispatch(_invoke(dispatcher, "conn-2"))
        _wait(lambda: any(item[0] == "result" for item in realtime.events))
        assert called == ["x"]
        record = bootstrap.get(
            client_id="client-1",
            principal_id="user-1",
            invocation_id="inv-1",
        )
        assert record is not None
        assert record.state is ClientInvocationLedgerState.TERMINAL
    finally:
        dispatcher.shutdown()
