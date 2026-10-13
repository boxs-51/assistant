import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import uuid

import pytest
import requests

from cl.src.core.auth_session import AuthSessionStore
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.gateway_client import GatewayLLMClient, GatewayEffectOutcomeUnknown, DIRECT_UI_EFFECT_TIMEOUT


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Registry:
    settings = {"loaded": True}


def _store(request):
    store = AuthSessionStore(
        Path.cwd() / f".auth-session-test-{uuid.uuid4().hex}.json"
    )
    request.addfinalizer(store.clear)
    return store


def test_registration_initiation_never_sends_stale_authorization(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Response({"status": "success"})

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway", api_key="stale-token")

    client.register({"email": "user@example.com", "password": "secret1"})

    assert captured["url"].endswith("/v1/auth/register/initiate")
    assert "Authorization" not in captured["headers"]


def test_registration_verification_carries_guest_identity(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Response({"access_token": "user-access", "refresh_token": "user-refresh"})

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway")
    client.set_access_token("guest-access")

    client.verify_registration({"email": "user@example.com", "otp": "123456"})

    assert captured["headers"]["Authorization"] == "Bearer guest-access"
    assert client.access_token == "user-access"
    assert client.refresh_token_value == "user-refresh"


def test_stream_preserves_agent_fallback_notification(monkeypatch):
    notice = {
        "status": "AGENT_FALLBACK",
        "reason": "AGENT_NOT_SPECIFIED",
        "fallback": "DIRECT",
    }

    class StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield f"data: {json.dumps(notice)}".encode()
            yield b"data: [DONE]"

    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: StreamResponse())

    chunks = list(GatewayLLMClient("http://gateway")._stream_response({}))

    assert chunks == [notice]


def test_stream_preserves_agent_activity_before_answer(monkeypatch):
    activity = {
        "object": "agent_stream_event",
        "event_id": "event-1",
        "event_type": "agent.tool.started",
        "execution_id": "exec-1",
        "channel": "tool",
        "data": {"name": "skill.load", "status": "started"},
    }
    answer = {
        "id": "chunk-1",
        "model": "mock",
        "choices": [{"index": 0, "delta": {"content": "done"}}],
    }

    class StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield f"data: {json.dumps(activity)}".encode()
            yield f"data: {json.dumps(answer)}".encode()
            yield b"data: [DONE]"

    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: StreamResponse())
    chunks = list(GatewayLLMClient("http://gateway")._stream_response({}))
    assert chunks[0] == activity
    assert chunks[1].choices[0].delta.content == "done"


def test_start_creates_and_persists_guest_session(request):
    store = _store(request)
    runtime = ClientRuntime("http://gateway", _Registry(), session_store=store)
    calls = []

    def create_guest():
        calls.append("guest")
        runtime.gateway.set_access_token("guest-token")
        return {
            "access_token": "guest-token",
            "user_id": "guest-1",
            "expires_in": 600,
        }

    runtime.gateway.create_guest = create_guest
    runtime.gateway.current_session = lambda: {
        "user_id": "guest-1",
        "principal_type": "guest",
        "roles": ["guest"],
    }
    runtime.realtime.connect = lambda: calls.append("connect") or {}
    runtime.capabilities.register = lambda: calls.append("register") or {}

    runtime.start()

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert calls == ["guest", "connect", "register"]
    assert runtime.owner_id == "guest-1"
    assert runtime.principal_type == "guest"
    assert runtime.realtime.headers["Authorization"] == "Bearer guest-token"
    assert saved["access_token"] == "guest-token"
    assert saved["principal_type"] == "guest"


def test_start_refreshes_and_restores_saved_user_session(request):
    store = _store(request)
    store.save({
        "access_token": "expired-access",
        "refresh_token": "refresh-1",
        "principal_type": "user",
        "user_id": "user-1",
    })
    runtime = ClientRuntime("http://gateway", _Registry(), session_store=store)
    attempts = iter([False, True])

    def current_session():
        if not next(attempts):
            raise requests.HTTPError("401")
        return {"user_id": "user-1", "principal_type": "user", "roles": []}

    def refresh(refresh_token):
        assert refresh_token == "refresh-1"
        runtime.gateway.set_access_token("fresh-access")
        return {"access_token": "fresh-access"}

    runtime.gateway.current_session = current_session
    runtime.gateway.refresh_token = refresh
    runtime.gateway.create_guest = lambda: (_ for _ in ()).throw(AssertionError("unexpected guest"))
    runtime.realtime.connect = lambda: {}
    runtime.capabilities.register = lambda: {}

    runtime.start()

    assert runtime.owner_id == "user-1"
    assert runtime.principal_type == "user"
    assert store.load()["access_token"] == "fresh-access"


def test_direct_effect_propagates_explicit_finite_socket_timeout(monkeypatch):
    captured = []

    def request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        return _Response({"output": {"ok": True}})

    monkeypatch.setattr(requests, "request", request)
    client = GatewayLLMClient("http://gateway")
    result = client.execute_capability("echo", {"arguments": {"text": "hello"}})

    assert result == {"output": {"ok": True}}
    assert len(captured) == 1
    assert captured[0][0] == "POST"
    assert captured[0][1].endswith("/v1/capabilities/echo/execute")
    assert captured[0][2]["timeout"] == DIRECT_UI_EFFECT_TIMEOUT
    assert all(isinstance(limit, float) and limit > 0
               for limit in captured[0][2]["timeout"])


@pytest.mark.parametrize("error", [
    requests.exceptions.ConnectTimeout("connect stalled"),
    requests.exceptions.ReadTimeout("read stalled"),
    requests.exceptions.ConnectionError("peer disconnected"),
    requests.exceptions.ChunkedEncodingError("partial chunk"),
    requests.exceptions.ContentDecodingError("corrupt body"),
])
def test_direct_effect_transport_failure_is_unknown_and_not_auto_retried(
    monkeypatch, error,
):
    attempts = []

    def failing_request(*args, **kwargs):
        attempts.append((args, kwargs))
        raise error

    monkeypatch.setattr(requests, "request", failing_request)
    client = GatewayLLMClient("http://gateway")
    with pytest.raises(GatewayEffectOutcomeUnknown, match="do not retry"):
        client.execute_capability("effect", {"arguments": {}})
    assert len(attempts) == 1


def test_direct_effect_malformed_success_body_is_unknown(monkeypatch):
    class BadBody(_Response):
        def json(self):
            raise requests.exceptions.JSONDecodeError("invalid body", "not json", 0)

    monkeypatch.setattr(requests, "request", lambda *a, **k: BadBody({}))
    with pytest.raises(GatewayEffectOutcomeUnknown):
        GatewayLLMClient("http://gateway").execute_capability("effect", {})


def test_native_loopback_silent_gateway_times_out_without_retry(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class SilentGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            calls.append(self.path)
            entered.set()
            release.wait(3.0)
            # Never provide an HTTP response while the client is waiting.

    server = ThreadingHTTPServer(("127.0.0.1", 0), SilentGateway)
    server.daemon_threads = True
    host = threading.Thread(target=server.serve_forever, daemon=True)
    host.start()
    try:
        monkeypatch.setattr(
            "cl.src.core.gateway_client.DIRECT_UI_EFFECT_TIMEOUT",
            (1.0, 0.35),
        )
        client = GatewayLLMClient(f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(GatewayEffectOutcomeUnknown):
            client.execute_capability("opaque-effect", {"arguments": {"x": 1}})
        assert entered.is_set()
        assert calls == ["/v1/capabilities/opaque-effect/execute"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        host.join(2.0)
