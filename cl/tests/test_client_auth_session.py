import json
from pathlib import Path
import uuid

import requests

from cl.src.core.auth_session import AuthSessionStore
from cl.src.core.client_runtime import ClientRuntime
from cl.src.core.gateway_client import GatewayLLMClient


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
