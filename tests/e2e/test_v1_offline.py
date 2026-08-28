"""Canonical API v1 E2E with zero external AI/network provider traffic."""
from __future__ import annotations
from types import SimpleNamespace
from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.schemas.identity import Identity
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.mock import MockProvider
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler
from src.provider.policies.routing_policy import RoutingPolicy
from src.circuit_breaker import CircuitBreakerManager
from src.transport.gateway.api.v1 import admin, agent_router, auth_router, chat_router, embeddings_router, events_router, files_router, health_router, models_router, multi_agent_router, tool_router
from src.transport.gateway.api.v1.auth_router import get_auth_facade, get_api_key_service
from src.transport.gateway.authentication.dependency import get_current_identity, verify_admin_ip
from src.transport.gateway.dependencies import get_container

class InlineEventBus:
    def __init__(self): self.handlers={}
    def subscribe(self,n,h): self.handlers.setdefault(n,[]).append(h)
    def unsubscribe(self,n,h):
        if h in self.handlers.get(n,[]): self.handlers[n].remove(h)
    async def publish(self,event):
        for h in list(self.handlers.get(event.event_name,[])): await h(event)

class FakeWS:
    def __init__(self): self.connected=[]; self.subscriptions={}
    async def connect(self,ws): self.connected.append(ws)
    def disconnect(self,ws):
        if ws in self.connected: self.connected.remove(ws)
    async def subscribe(self,ws,event): self.subscriptions.setdefault(ws,set()).add(event)
    async def unsubscribe(self,ws,event): self.subscriptions.get(ws,set()).discard(event)
    async def shutdown(self): pass

class FakeAuthFacade:
    async def initiate_registration(self,data): return {"status":"pending","email":str(data.email)}
    async def confirm_registration(self,email,otp):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline",refresh_token="offline-refresh")
    async def login(self,data):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline",refresh_token="offline-refresh")
    async def refresh_access_token(self,token):
        from src.domain.schemas.auth import AccessTokenSchema; return AccessTokenSchema(access_token="offline")
    async def logout(self,token): return None
    async def handle_oauth_callback(self,provider,user):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline-oauth",refresh_token="offline-refresh")
    async def get_current_user_info(self,identity):
        from src.domain.schemas.auth import UserMeSchema; return UserMeSchema(id=identity.user_id or "offline-user",email="offline@example.com",roles=["member"])

class FakeAPIKeys:
    async def create_api_key(self,data,identity): return {"id":"mock-key","full_key":"mock-api-key","prefix":"mock","created_at":"1970-01-01T00:00:00Z"}
    async def list_api_keys(self,identity): return []
    async def revoke_api_key(self,key_id,identity): return True

class FakeOAuthClient:
    async def authorize_redirect(self,request,redirect_uri):
        from fastapi.responses import RedirectResponse; return RedirectResponse(str(redirect_uri))
    async def authorize_access_token(self,request): return {"access_token":"offline"}
    async def userinfo(self,token): return {"email":"offline@example.com","sub":"offline-user","name":"Offline"}
    async def get(self,*args,**kwargs): return httpx.Response(200,json=[])
class FakeOAuth:
    _clients={"mock":object()}
    def create_client(self,provider): return FakeOAuthClient()

class FakeCoordinator:
    def __init__(self): self.sessions={}; self.messages={}; self.tasks={}; self.executor=None
    async def create_session_async(self,identity,agent_ids):
        sid=f"mock-session-{len(self.sessions)+1}"; self.sessions[sid]={"session_id":sid,"owner_user_id":identity.user_id,"agent_ids":list(agent_ids),"status":"ACTIVE"}; return self.sessions[sid]
    def add_agent(self,sid,aid,identity): self.sessions[sid]["agent_ids"].append(aid); return self.sessions[sid]
    def list_messages(self,sid,identity): return self.messages.get(sid,[])
    async def send_message_async(self,**kw):
        item={"message_id":f"mock-message-{len(self.messages.get(kw['session_id'],[]))+1}",**kw}; self.messages.setdefault(kw['session_id'],[]).append(item); return item
    async def create_task_async(self,**kw):
        tid=f"mock-task-{len(self.tasks)+1}"; item={"task_id":tid,"status":"CREATED",**kw}; self.tasks[tid]=item; return item
    def get_task(self,tid,identity): return self.tasks[tid]
    def cancel_task(self,tid,identity): self.tasks[tid]["status"]="CANCELLED"; return self.tasks[tid]
    def close_session(self,sid,identity): self.sessions[sid]["status"]="CLOSED"; return self.sessions[sid]
    async def execute_task(self,tid,identity,executor): return await executor(self.tasks[tid])
    def get_execution(self,eid,identity): return {"execution_id":eid,"status":"completed"}

@pytest.fixture
def offline_app():
    app=FastAPI()
    identity=Identity(auth_type="jwt",user_id="offline-user",permissions=["admin:read","admin:write"],scopes={"profile","email"})
    bus=InlineEventBus(); ws=FakeWS(); provider=MockProvider(); breakers=CircuitBreakerManager(); executor=ProviderExecutor(breakers); providers={"mock":provider}; routing=RoutingPolicy(providers)
    h=dict(providers=providers,routing_policy=routing,executor=executor,circuit_breaker_manager=breakers)
    runtime=SimpleNamespace(providers=providers,routing_policy=routing,circuit_breaker_manager=breakers,chat_handler=ChatExecutionHandler(**h),embedding_handler=EmbeddingExecutionHandler(**h),model_handler=ModelOperationHandler(**h),file_handler=FileOperationHandler(**h))
    agent_registry=SimpleNamespace(_items={},register=lambda x: agent_registry._items.__setitem__(x.name,x),get=lambda x: agent_registry._items.get(x))
    tool_registry=SimpleNamespace(_items={},register=lambda x: tool_registry._items.__setitem__(x.name,x),get=lambda x: tool_registry._items.get(x))
    container=SimpleNamespace(config=ConfigSchema(provider=ProviderSettings(priority=["mock"],mock_enabled=True)),storage=SimpleNamespace(drivers={},repositories={}),http_client=httpx.AsyncClient(),eventing_manager=SimpleNamespace(bus=bus,ws_manager=ws),provider_runtime=runtime,circuit_breaker_manager=breakers,agent_registry=agent_registry,tool_registry=tool_registry,multi_agent_coordinator=FakeCoordinator(),oauth=FakeOAuth())
    for r in [auth_router.router,files_router.router,models_router.router,chat_router.router,embeddings_router.router,admin.router,agent_router.router,tool_router.router,events_router.router,multi_agent_router.router,health_router.router]: app.include_router(r)
    app.dependency_overrides[get_container]=lambda:container
    app.dependency_overrides[get_current_identity]=lambda:identity
    app.dependency_overrides[verify_admin_ip]=lambda:None
    app.dependency_overrides[get_auth_facade]=lambda:FakeAuthFacade()
    app.dependency_overrides[get_api_key_service]=lambda:FakeAPIKeys()
    yield app,container
    import asyncio; asyncio.get_event_loop().run_until_complete(container.http_client.aclose())

@pytest.mark.asyncio
async def test_v1_provider_apis(offline_app):
    app,_=offline_app
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://testserver") as c:
        assert (await c.post("/v1/chat/completions",json={"model":"mock-chat","provider":"mock","messages":[{"role":"user","content":"hello"}],"config":{"stream":False}})).status_code==200
        assert (await c.post("/v1/embeddings",json={"model":"mock-embedding","provider":"mock","input":["hello"]})).status_code==200
        models=await c.get("/v1/models/",params={"provider_name":"mock"}); assert models.status_code==200
        detail=await c.get("/v1/models/mock-chat",params={"provider_name":"mock"}); assert detail.status_code==200
        up=await c.post("/v1/files/",params={"provider_name":"mock","display_name":"x.txt"},files={"file":("x.txt",b"hello","text/plain")}); assert up.status_code in (200,201)
        fid=up.json()["name"]
        assert (await c.get(f"/v1/files/{fid}",params={"provider_name":"mock","action":"metadata"})).status_code==200
        d=await c.get(f"/v1/files/{fid}",params={"provider_name":"mock","action":"download"}); assert d.content==b"hello"
        assert (await c.delete(f"/v1/files/{fid}",params={"provider_name":"mock"})).status_code==204

@pytest.mark.asyncio
async def test_v1_auth_api(offline_app):
    app,_=offline_app; t=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t,base_url="http://testserver") as c:
        assert (await c.post("/v1/auth/register/initiate",json={"email":"offline@example.com","password":"secret123","name":"Offline"})).status_code==200
        assert (await c.post("/v1/auth/register/verify",json={"email":"offline@example.com","otp":"123456"})).status_code==200
        assert (await c.post("/v1/auth/login",json={"email":"offline@example.com","password":"secret123"})).status_code==200
        assert (await c.post("/v1/auth/refresh",json={"refresh_token":"offline-refresh"})).status_code==200
        assert (await c.post("/v1/auth/logout",json={"refresh_token":"offline-refresh"})).status_code==204
        assert (await c.get("/v1/auth/me")).status_code==200
        assert (await c.post("/v1/auth/api-keys",json={"name":"x"})).status_code==201
        assert (await c.get("/v1/auth/api-keys")).status_code==200
        assert (await c.delete("/v1/auth/api-keys/mock-key")).status_code==204
        oauth=await c.post("/v1/auth/oauth/mock",json={"provider":"mock","provider_user_id":"offline-user","email":"offline@example.com"}); assert oauth.status_code==200
        redirect=await c.get("/v1/auth/oauth/login/mock",follow_redirects=False); assert redirect.status_code in (302,307)

@pytest.mark.asyncio
async def test_v1_agent_tool_admin_health_multi_agent(offline_app):
    app,_=offline_app; t=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t,base_url="http://testserver") as c:
        tool={"name":"offline.tool","description":"offline","parameters":{"type":"object","properties":{}}}
        assert (await c.post("/v1/tools/",json=tool)).status_code==201
        agent={"name":"offline-agent","goal":"test","instruction":"test","tools":["offline.tool"]}
        assert (await c.post("/v1/agents/",json=agent)).status_code==201
        s=await c.post("/v1/multi-agent/sessions",json={"agent_ids":[]}); assert s.status_code==201; sid=s.json()["session_id"]
        assert (await c.post(f"/v1/multi-agent/sessions/{sid}/agents",json={"agent_id":"offline-agent"})).status_code==200
        assert (await c.get(f"/v1/multi-agent/sessions/{sid}/messages")).status_code==200
        m=await c.post("/v1/multi-agent/messages",json={"session_id":sid,"sender_id":"offline-user","payload":{"x":1}}); assert m.status_code==201
        task=await c.post("/v1/multi-agent/tasks",json={"session_id":sid,"assigned_agent_id":"offline-agent","input":{"prompt":"hi"}}); assert task.status_code==201; tid=task.json()["task_id"]
        assert (await c.get(f"/v1/multi-agent/tasks/{tid}")).status_code==200
        assert (await c.post(f"/v1/multi-agent/tasks/{tid}/cancel")).status_code==200
        assert (await c.post(f"/v1/multi-agent/sessions/{sid}/close")).status_code==200
        assert (await c.get("/v1/multi-agent/executions/ex1")).status_code==200
        assert (await c.post("/v1/admin/reload/routing")).status_code==200
        assert (await c.get("/v1/admin/circuit-breakers/status")).status_code==200
        assert (await c.get("/health")).status_code==200
        assert (await c.get("/ready")).status_code==200
        assert (await c.get("/metrics")).status_code==200
        assert (await c.get("/stats")).status_code==200

def test_v1_events_websocket(offline_app):
    app,_=offline_app
    with TestClient(app) as client:
        with client.websocket_connect("/v1/events/ws") as ws:
            ws.send_json({"action":"subscribe","event_name":"mock.event"})
            msg=ws.receive_json(); assert msg["status"]=="success"
            ws.send_json({"action":"unsubscribe","event_name":"mock.event"})
            msg=ws.receive_json(); assert msg["status"]=="success"
