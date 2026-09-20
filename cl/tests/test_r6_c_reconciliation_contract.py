import threading
import time
from types import SimpleNamespace

from cl.src.core.capability_dispatcher import CapabilityDispatcher


class _Realtime:
    def __init__(self, connection_id):
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


def _registry(func):
    return SimpleNamespace(
        tools={
            "tool.echo": {
                "func": func,
                "metadata": {
                    "name": "tool.echo",
                    "version": "2.0",
                    "base_risk": "LOW",
                },
            }
        }
    )


def _invoke(dispatcher, *, connection_id="conn-1", value="x"):
    fingerprint = dispatcher._request_fingerprint(
        "tool.echo",
        "2.0",
        {"value": value},
    )
    return {
        "type": "capability.invoke",
        "connection_id": connection_id,
        "execution_id": "exec-1",
        "invocation_id": "inv-1",
        "trace_id": "trace-1",
        "payload": {
            "capability_id": "tool.echo",
            "capability_version": "2.0",
            "arguments": {"value": value},
            "request_fingerprint": fingerprint,
        },
    }


def _reconcile(dispatcher, connection_id, fingerprint):
    dispatcher.reconcile(
        {
            "type": "capability.reconcile",
            "connection_id": connection_id,
            "execution_id": "exec-1",
            "invocation_id": "inv-1",
            "trace_id": "trace-1",
            "payload": {
                "capability_id": "tool.echo",
                "capability_version": "2.0",
                "request_fingerprint": fingerprint,
            },
        }
    )


def test_reconcile_running_does_not_execute_twice_after_generation_change():
    release = threading.Event()
    called = []

    def tool(value):
        called.append(value)
        release.wait(1.0)
        return value

    old_realtime = _Realtime("conn-1")
    dispatcher = CapabilityDispatcher(_registry(tool), old_realtime)
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        invoke = _invoke(dispatcher)
        dispatcher.dispatch(invoke)
        _wait(lambda: called == ["x"])

        new_realtime = _Realtime("conn-2")
        dispatcher.set_realtime(new_realtime)
        _reconcile(
            dispatcher,
            "conn-2",
            invoke["payload"]["request_fingerprint"],
        )

        assert new_realtime.events[0][0] == "reconciliation"
        assert new_realtime.events[0][2]["status"] == "RUNNING"
        assert called == ["x"]
    finally:
        release.set()
        dispatcher.shutdown()


def test_result_send_loss_reconciles_exact_terminal_without_reexecution():
    called = []

    def tool(value):
        called.append(value)
        return {"value": value}

    old_realtime = _FailingRealtime("conn-1")
    dispatcher = CapabilityDispatcher(_registry(tool), old_realtime)
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        invoke = _invoke(dispatcher)
        dispatcher.dispatch(invoke)
        _wait(lambda: bool(old_realtime.events))

        new_realtime = _Realtime("conn-2")
        dispatcher.set_realtime(new_realtime)
        _reconcile(
            dispatcher,
            "conn-2",
            invoke["payload"]["request_fingerprint"],
        )
        response = new_realtime.events[0][2]
        assert response["status"] == "TERMINAL"
        assert response["terminal_type"] == "result"
        assert response["terminal_payload"] == {
            "output": {"value": "x"}
        }
        assert called == ["x"]
    finally:
        dispatcher.shutdown()


def test_reconciliation_conflict_never_replays_or_reexecutes():
    called = []
    realtime = _Realtime("conn-1")
    dispatcher = CapabilityDispatcher(
        _registry(lambda value: called.append(value) or value),
        realtime,
    )
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        invoke = _invoke(dispatcher)
        dispatcher.dispatch(invoke)
        _wait(lambda: any(item[0] == "result" for item in realtime.events))
        realtime.events.clear()

        _reconcile(dispatcher, "conn-1", "0" * 64)
        assert realtime.events[0][2]["status"] == "CONFLICT"
        assert called == ["x"]
    finally:
        dispatcher.shutdown()
