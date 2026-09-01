from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.schemas.event import BaseEvent
from src.domain.schemas.identity import Identity
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings, ProviderConfig, CircuitBreakerSettings, CircuitBreakerProviderSettings
from src.provider.mock import MockProvider
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler
from src.circuit_breaker import CircuitBreakerManager
from src.transport.gateway.api.v1 import (
    admin,
    agent_router,
    auth_router,
    chat_router,
    embeddings_router,
    events_router,
    files_router,
    health_router,
    models_router,
    multi_agent_router,
    tool_router,
)
from src.transport.gateway.authentication.dependency import get_current_identity, verify_admin_ip, get_api_key_service
from src.transport.gateway.dependencies import get_container, get_auth


class InlineEventBus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    def unsubscribe(self, name, handler):
        handlers = self.handlers.get(name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: BaseEvent):
        handlers = list(self.handlers.get(event.event_name, []))
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        return None


class FakeWS:
    def __init__(self):
        self.connected = []

    async def connect(self, websocket):
        await websocket.accept()
        self.connected.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.connected:
            self.connected.remove(websocket)

    async def subscribe(self, websocket, event):
        return None

    async def unsubscribe(self, websocket, event):
        return None

    async def shutdown(self):
        self.connected.clear()


class FakeAuthFacade:
    async def initiate_registration(self, data):
        return {"status": "pending", "email": str(data.email)}

    async def confirm_registration(self, email, otp):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline", refresh_token="offline-refresh")

    async def login(self, data):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline", refresh_token="offline-refresh")

    async def refresh_access_token(self, token):
        from src.domain.schemas.auth import AccessTokenSchema
        return AccessTokenSchema(access_token="offline")

    async def logout(self, token):
        return None

    async def handle_oauth_callback(self, provider, user):
        from src.domain.schemas.auth import TokenSchema
        return TokenSchema(access_token="offline-oauth", refresh_token="offline-refresh")

    async def get_current_user_info(self, identity):
        from src.domain.schemas.auth import UserMeSchema
        return UserMeSchema(
            id=identity.user_id or "offline-user",
            email="offline@example.com",
            roles=["member"],
        )


class FakeAPIKeys:
    async def create_api_key(self, data, identity):
        return {
            "id": "mock-key",
            "full_key": "mock-api-key",
            "prefix": "mock",
            "created_at": "1970-01-01T00:00:00Z",
        }

    async def list_api_keys(self, identity):
        return []

    async def revoke_api_key(self, key_id, identity):
        return True


class FakeOAuthClient:
    async def authorize_redirect(self, request, redirect_uri):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(str(redirect_uri))

    async def authorize_access_token(self, request):
        return {"access_token": "offline"}

    async def userinfo(self, token):
        return {"email": "offline@example.com", "sub": "offline-user", "name": "Offline"}

    async def get(self, *args, **kwargs):
        return httpx.Response(200, json=[])


class FakeOAuth:
    _clients = {"mock": object()}

    def create_client(self, provider):
        return FakeOAuthClient()


class FakeCoordinator:
    def __init__(self):
        self.sessions = {}
        self.messages = {}
        self.tasks = {}
        self.executor = None

    async def create_session_async(self, identity, agent_ids):
        sid = f"mock-session-{len(self.sessions)+1}"
        self.sessions[sid] = {
            "session_id": sid,
            "owner_user_id": identity.user_id,
            "agent_ids": list(agent_ids),
            "status": "ACTIVE",
        }
        return self.sessions[sid]

    def add_agent(self, sid, aid, identity):
        self.sessions[sid]["agent_ids"].append(aid)
        return self.sessions[sid]

    def list_messages(self, sid, identity):
        return self.messages.get(sid, [])

    async def send_message_async(self, **kw):
        item = {
            "message_id": f"mock-message-{len(self.messages.get(kw['session_id'], []))+1}",
            **kw,
        }
        self.messages.setdefault(kw["session_id"], []).append(item)
        return item

    async def create_task_async(self, **kw):
        tid = f"mock-task-{len(self.tasks)+1}"
        item = {"task_id": tid, "status": "CREATED", **kw}
        self.tasks[tid] = item
        return item

    def get_task(self, tid, identity):
        return self.tasks[tid]

    def cancel_task(self, tid, identity):
        self.tasks[tid]["status"] = "CANCELLED"
        return self.tasks[tid]

    def close_session(self, sid, identity):
        self.sessions[sid]["status"] = "CLOSED"
        return self.sessions[sid]

    async def execute_task(self, tid, identity, executor):
        return await executor(self.tasks[tid])

    def get_execution(self, eid, identity):
        return {"execution_id": eid, "status": "completed"}


class OfflineContainer(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def require(self, key):
        value = getattr(self, key, None)
        if value is None:
            raise RuntimeError(f"Offline container dependency not found: {key}")
        return value


@pytest.fixture
def offline_app():
    app = FastAPI()
    identity = Identity(
        auth_type="jwt",
        user_id="offline-user",
        permissions=["admin:read", "admin:write"],
        scopes={"profile", "email"},
    )

    config = ConfigSchema(
        provider=ProviderSettings(priority=["mock"], timeout=5, retry=0,
        config={"mock" : ProviderConfig(enabled=True, base_url = "http://testserver",options = {"seed" : "v1-offline"})}),
        circuit_breaker=CircuitBreakerSettings()
    )
    bus = InlineEventBus()
    ws = FakeWS()
    provider = MockProvider(config=ProviderConfig(enabled=True, base_url = "http://testserver",options = {"seed" : "v1-offline"}))
    breakers = CircuitBreakerManager(config=config.circuit_breaker)
    executor = ProviderExecutor(breakers, max_retries=0)
    providers = {"mock": provider}
    routing = RoutingPolicy(providers, config=config.provider)
    handler_kwargs = dict(
        providers=providers,
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=breakers,
        timeout=config.provider.timeout,
    )

    # Use the same handler contracts as ProviderRuntime without requiring the
    # production storage/bootstrap stack.
    runtime = SimpleNamespace(
        providers=providers,
        routing_policy=routing,
        circuit_breaker_manager=breakers,
        chat_handler=ChatExecutionHandler(**handler_kwargs),
        embedding_handler=EmbeddingExecutionHandler(**handler_kwargs),
        model_handler=ModelOperationHandler(**handler_kwargs),
        file_handler=FileOperationHandler(**handler_kwargs),
        _http_client=httpx.AsyncClient(),
        event_bus=bus,
    )

    async def _handle_chat(event):
        try:
            body = event.payload.get("request_body", {})
            if body.get("config", {}).get("stream"):
                async for chunk in runtime.chat_handler.stream_with_fallback(runtime._http_client, body):
                    await bus.publish(BaseEvent(
                        event_name="provider.stream.chunk_emitted",
                        session_id=event.session_id,
                        payload={"chunk": chunk.model_dump(), "sse": chunk.to_sse()},
                    ))
                await bus.publish(BaseEvent(
                    event_name="provider.stream.completed",
                    session_id=event.session_id,
                    payload={},
                ))
            else:
                response = await runtime.chat_handler.execute_with_fallback(runtime._http_client, body)
                await bus.publish(BaseEvent(
                    event_name="provider.chat.responded",
                    session_id=event.session_id,
                    payload={"response": response.model_dump()},
                ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 503},
            ))

    async def _handle_embeddings(event):
        try:
            result = await runtime.embedding_handler.execute(
                runtime._http_client, event.payload.get("request_body", {})
            )
            await bus.publish(BaseEvent(
                event_name="provider.embeddings.responded",
                session_id=event.session_id,
                payload={"response": result},
            ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 503},
            ))

    async def _handle_models(event):
        try:
            result = await runtime.model_handler.execute(
                event.payload.get("provider_name"),
                event.payload.get("model_id"),
                runtime._http_client,
            )
            await bus.publish(BaseEvent(
                event_name="provider.model.responded",
                session_id=event.session_id,
                payload={"result": result},
            ))
        except KeyError as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 404},
            ))

    async def _handle_files(event):
        try:
            result = await runtime.file_handler.execute(
                event.payload, runtime._http_client
            )
            await bus.publish(BaseEvent(
                event_name="provider.file.responded",
                session_id=event.session_id,
                payload={"result": result},
            ))
        except KeyError as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 404},
            ))
        except Exception as exc:
            await bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                payload={"error": str(exc), "status_code": 500},
            ))

    bus.subscribe("provider.chat.execute", _handle_chat)
    bus.subscribe("provider.embeddings.execute", _handle_embeddings)
    bus.subscribe("provider.model.execute", _handle_models)
    bus.subscribe("provider.file.execute", _handle_files)

    agent_store = {}
    tool_store = {}
    agent_registry = SimpleNamespace(
        register=lambda x: agent_store.__setitem__(x.name, x),
        get=lambda x: agent_store.get(x),
    )
    tool_registry = SimpleNamespace(
        register=lambda x: tool_store.__setitem__(x.name, x),
        get=lambda x: tool_store.get(x),
    )

    container = SimpleNamespace(
        config=config,
        storage=SimpleNamespace(drivers={}, repositories={},get_cache_driver=lambda: None),
        http_client=runtime._http_client,
        eventing_manager=SimpleNamespace(bus=bus, ws_manager=ws),
        event_bus=bus,
        provider_runtime=runtime,
        circuit_breaker_manager=breakers,
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        multi_agent_coordinator=FakeCoordinator(),
        oauth=FakeOAuth(),
    )
    container.require = lambda key: getattr(container, key)
    app.state.container = container
    
    for router in [
        auth_router.router,
        files_router.router,
        models_router.router,
        chat_router.router,
        embeddings_router.router,
        admin.router,
        agent_router.router,
        tool_router.router,
        events_router.router,
        multi_agent_router.router,
        health_router.router,
    ]:
        app.include_router(router)

    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[verify_admin_ip] = lambda: None
    app.dependency_overrides[get_auth] = lambda: FakeAuthFacade()
    app.dependency_overrides[get_api_key_service] = lambda: FakeAPIKeys()

    yield app
    asyncio.run(runtime._http_client.aclose())


@pytest.mark.asyncio
async def test_v1_provider_apis_are_offline(offline_app: FastAPI):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-chat",
                "provider": "mock",
                "messages": [{"role": "user", "content": "hello"}],
                "config": {"stream": False},
            },
        )
        assert chat.status_code == 200, chat.text
        assert chat.json()["provider"] == "mock"

        embeddings = await client.post(
            "/v1/embeddings",
            json={"model": "mock-embedding", "provider": "mock", "input": ["hello"]},
        )
        assert embeddings.status_code == 200, embeddings.text

        models = await client.get("/v1/models/", params={"provider_name": "mock"})
        assert models.status_code == 200, models.text

        detail = await client.get(
            "/v1/models/mock-chat", params={"provider_name": "mock"}
        )
        assert detail.status_code == 200, detail.text

        upload = await client.post(
            "/v1/files/",
            params={"provider_name": "mock", "display_name": "x.txt"},
            files={"file": ("x.txt", b"hello", "text/plain")},
        )
        assert upload.status_code in {200, 201}, upload.text
        file_id = upload.json()["name"]

        metadata = await client.get(
            f"/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "metadata"},
        )
        assert metadata.status_code == 200, metadata.text

        downloaded = await client.get(
            f"/v1/files/{file_id}",
            params={"provider_name": "mock", "action": "download"},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"hello"

        deleted = await client.delete(
            f"/v1/files/{file_id}", params={"provider_name": "mock"}
        )
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_v1_streaming_chat_is_offline(offline_app: FastAPI):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "mock-chat",
                "provider": "mock",
                "messages": [{"role": "user", "content": "one two"}],
                "config": {"stream": True},
            },
        ) as response:
            body = await response.aread()
            assert response.status_code == 200, body
            text = body.decode()
            assert "mock:one" in text
            assert "mock:two" in text
            assert "[DONE]" in text


@pytest.mark.asyncio
async def test_v1_auth_api_is_offline(offline_app: FastAPI):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.post("/v1/auth/register/initiate", json={"email":"offline@example.com","password":"secret123","name":"Offline"})).status_code == 200
        assert (await client.post("/v1/auth/register/verify", json={"email":"offline@example.com","otp":"123456"})).status_code == 200
        assert (await client.post("/v1/auth/login", json={"email":"offline@example.com","password":"secret123"})).status_code == 200
        assert (await client.post("/v1/auth/refresh", json={"refresh_token":"offline-refresh"})).status_code == 200
        assert (await client.post("/v1/auth/logout", json={"refresh_token":"offline-refresh"})).status_code == 204
        assert (await client.get("/v1/auth/me")).status_code == 200
        assert (await client.post("/v1/auth/api-keys", json={"name":"x"})).status_code == 201
        assert (await client.get("/v1/auth/api-keys")).status_code == 200
        assert (await client.delete("/v1/auth/api-keys/mock-key")).status_code == 204
        assert (await client.post("/v1/auth/oauth/mock", json={"provider":"mock","provider_user_id":"offline-user","email":"offline@example.com"})).status_code == 200


@pytest.mark.asyncio
async def test_v1_agent_tool_admin_health_multi_agent(offline_app: FastAPI):
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        tool = {"name":"offline.tool","description":"offline","parameters":{"type":"object","properties":{}}}
        assert (await client.post("/v1/tools/", json=tool)).status_code == 201
        agent = {"name":"offline-agent","goal":"test","instruction":"test","tools":["offline.tool"]}
        assert (await client.post("/v1/agents/", json=agent)).status_code == 201
        session = await client.post("/v1/multi-agent/sessions", json={"agent_ids":[]})
        assert session.status_code == 201
        sid = session.json()["session_id"]
        assert (await client.post(f"/v1/multi-agent/sessions/{sid}/agents", json={"agent_id":"offline-agent"})).status_code == 200
        assert (await client.get(f"/v1/multi-agent/sessions/{sid}/messages")).status_code == 200
        assert (await client.post("/v1/multi-agent/messages", json={"session_id":sid,"sender_id":"offline-user","payload":{"x":1}})).status_code == 201
        task = await client.post("/v1/multi-agent/tasks", json={"session_id":sid,"assigned_agent_id":"offline-agent","input":{"prompt":"hi"}})
        assert task.status_code == 201
        tid = task.json()["task_id"]
        assert (await client.get(f"/v1/multi-agent/tasks/{tid}")).status_code == 200
        assert (await client.post(f"/v1/multi-agent/tasks/{tid}/cancel")).status_code == 200
        assert (await client.post(f"/v1/multi-agent/sessions/{sid}/close")).status_code == 200
        assert (await client.get("/v1/multi-agent/executions/ex1")).status_code == 200
        assert (await client.post("/v1/admin/reload/routing")).status_code == 200
        assert (await client.get("/v1/admin/circuit-breakers/status")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        assert (await client.get("/metrics")).status_code == 200
        assert (await client.get("/stats")).status_code == 200


def test_v1_events_websocket_offline(offline_app: FastAPI):
    with TestClient(offline_app) as client:
        with client.websocket_connect("/v1/events/ws") as ws:
            ws.send_json({"action":"subscribe","event_name":"mock.event"})
            assert ws.receive_json()["status"] == "success"
            ws.send_json({"action":"unsubscribe","event_name":"mock.event"})
            assert ws.receive_json()["status"] == "success"
