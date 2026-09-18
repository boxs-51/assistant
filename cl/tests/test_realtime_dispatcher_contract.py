import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from cl.src.core.auth_session import AuthSessionStore
from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.installation_identity import InstallationIdentityStore


class _Realtime:
    def __init__(self, connection_id="conn-1"):
        self.connection_id = connection_id
        self.events = []

    def send_result(self, invocation_id, result, **correlation):
        self.events.append(("result", invocation_id, result, correlation))

    def send_error(self, invocation_id, **payload):
        self.events.append(("error", invocation_id, payload))

    def send_cancelled(self, invocation_id, **correlation):
        self.events.append(("cancelled", invocation_id, correlation))


class _Hitl:
    def __init__(self, approved):
        self.approved = approved

    def request_approval(self, *args, **kwargs):
        return self.approved


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for dispatcher outcome")


def _envelope(invocation_id="inv-1", connection_id="conn-1"):
    return {
        "type": "capability.invoke",
        "connection_id": connection_id,
        "execution_id": "exec-1",
        "invocation_id": invocation_id,
        "trace_id": "trace-1",
        "payload": {"capability_id": "tool.echo", "arguments": {"value": "x"}},
    }


def test_high_risk_remote_invocation_requires_local_approval():
    called = []
    registry = SimpleNamespace(tools={
        "tool.echo": {
            "func": lambda value: called.append(value) or value,
            "metadata": {"base_risk": "HIGH"},
        }
    })
    realtime = _Realtime()
    dispatcher = CapabilityDispatcher(registry, realtime, hitl=_Hitl(False))
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        dispatcher.dispatch(_envelope())
        _wait(lambda: bool(realtime.events))
        assert called == []
        assert realtime.events[0][0] == "error"
        assert realtime.events[0][2]["code"] == "HITL_DENIED"
    finally:
        dispatcher.shutdown()


def test_duplicate_running_executes_once_and_terminal_duplicate_replays():
    release = threading.Event()
    called = []

    def tool(value):
        called.append(value)
        release.wait(1.0)
        return {"value": value}

    registry = SimpleNamespace(tools={
        "tool.echo": {"func": tool, "metadata": {"base_risk": "LOW"}}
    })
    realtime = _Realtime()
    dispatcher = CapabilityDispatcher(registry, realtime)
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        envelope = _envelope()
        dispatcher.dispatch(envelope)
        dispatcher.dispatch(envelope)
        release.set()
        _wait(lambda: len(realtime.events) == 1)
        dispatcher.dispatch(envelope)
        _wait(lambda: len(realtime.events) == 2)
        assert called == ["x"]
        assert [item[0] for item in realtime.events] == ["result", "result"]
        assert realtime.events[0][2] == {"value": "x"}
    finally:
        dispatcher.shutdown()


def test_completed_outcome_is_replayed_after_transport_generation_changes():
    called = []

    class FailingRealtime(_Realtime):
        def send_result(self, invocation_id, result, **correlation):
            self.events.append(("attempt", invocation_id, result, correlation))
            raise ConnectionError("socket closed before result was sent")

    registry = SimpleNamespace(tools={
        "tool.echo": {
            "func": lambda value: called.append(value) or value,
            "metadata": {"base_risk": "LOW"},
        }
    })
    old_realtime = FailingRealtime()
    dispatcher = CapabilityDispatcher(registry, old_realtime)
    dispatcher.update_registration_snapshot(["tool.echo"])
    try:
        dispatcher.dispatch(_envelope())
        _wait(lambda: len(old_realtime.events) == 1)

        new_realtime = _Realtime("conn-2")
        dispatcher.set_realtime(new_realtime)
        dispatcher.dispatch(_envelope(connection_id="conn-2"))
        _wait(lambda: len(new_realtime.events) == 1)

        assert called == ["x"]
        assert new_realtime.events[0][0:3] == ("result", "inv-1", "x")
    finally:
        dispatcher.shutdown()


def test_installation_id_is_stable_but_connection_generation_rotates(request):
    test_root = Path.cwd() / f".client-identity-test-{uuid.uuid4().hex}"
    test_root.mkdir()
    request.addfinalizer(
        lambda: [item.unlink() for item in test_root.iterdir()] and test_root.rmdir()
    )
    installation = InstallationIdentityStore(test_root / "installation.json")
    first_id = installation.load_or_create()
    assert InstallationIdentityStore(test_root / "installation.json").load_or_create() == first_id

    registry = SimpleNamespace(settings={"loaded": True}, tools={})
    runtime = ClientRuntime(
        "http://gateway",
        registry,
        owner_id="user-1",
        session_store=AuthSessionStore(test_root / "auth.json"),
        installation_store=installation,
    )
    first_connection = runtime.connection_id
    runtime._replace_realtime_generation()
    try:
        assert runtime.client_id == first_id
        assert runtime.connection_id != first_connection
    finally:
        runtime.stop()
