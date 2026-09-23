from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.registration import ClientCapabilityRegistrationService
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.runtimes.agent.resume_claim import (
    ResumeClaimDeferred,
    ResumeClaimRejected,
)
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import (
    get_current_identity,
    get_websocket_identity
)
from se.src.transport.gateway.dependencies import get_container


def make_app():
    catalog = CapabilityCatalog()
    connection_runtime = ConnectionRuntime(
        ClientCapabilityRegistrationService(catalog, None),
    )
    connection_runtime.registration_service.connections = connection_runtime.registry
    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(ws_manager=WebSocketConnectionManager()),
        connection_runtime=connection_runtime,
    )
    container.require=lambda key: getattr(container, key)
    identity = Identity(
        user_id="user-1",
        session_id="session-1",
        auth_type="jwt",
    )
    app = FastAPI()
    app.include_router(events_router.router)
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_current_identity] = lambda: identity
    async def _mock_ws_identity(*args, **kwargs):
        return identity
    app.dependency_overrides[get_websocket_identity] = _mock_ws_identity
    return app, catalog, connection_runtime


def registration_payload():
    return {
        "connection_id": "conn-1",
        "client_id": "desktop-1",
        "owner_id": "user-1",
        "capabilities": [
            {
                "kind": "TOOL",
                "location": "CLIENT",
                "driver_kind": "REMOTE_CLIENT",
                "owner_type": "CLIENT",
                "implementation_id": "desktop-1:filesystem.read",
                "definition": {
                    "id": "filesystem.read",
                    "name": "filesystem.read",
                    "description": "Read a local file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
    }


def test_websocket_registers_client_capabilities_and_unregisters_on_disconnect():
    app, catalog, connection_runtime = make_app()

    with TestClient(app) as client:
        with client.websocket_connect("/v1/events/ws?args={}&kwargs={}") as websocket:
            websocket.send_json(
                {
                    "type": "connection.register",
                    "message_id": "connect-1",
                    "connection_id": "conn-1",
                    "session_id": "session-1",
                    "payload": {"client_id": "desktop-1"},
                }
            )
            connected = websocket.receive_json()
            assert connected["type"] == "connection.registered"
            assert connected["payload"]["state"] == "ACTIVE"

            websocket.send_json(
                {
                    "type": "capability.register",
                    "message_id": "capability-1",
                    "connection_id": "conn-1",
                    "session_id": "session-1",
                    "payload": registration_payload(),
                }
            )
            registered = websocket.receive_json()
            assert registered["type"] == "capability.registered"
            assert registered["payload"]["capabilities"][0]["state"] == "ENABLED"
            assert catalog.get_implementation("desktop-1:filesystem.read").state.value == "ENABLED"

        assert connection_runtime.registry.get("conn-1").state.value == "DISCONNECTED"
        implementation = catalog.get_implementation("desktop-1:filesystem.read")
        assert implementation.state.value == "REMOVED"
        assert catalog.list_implementations(
            "filesystem.read",
            routable_only=True,
        ) == []


def test_r8_f_retryable_resume_conflict_preserves_same_claim_id():
    claim = SimpleNamespace(claim_id="claim-r8-f-retry")
    deferred = ResumeClaimDeferred(
        "RESUME_CONFLICT",
        "transient Task activity conflict",
        retryable=True,
    )
    rejected = ResumeClaimRejected(
        "RESUME_CONFLICT",
        "non-retryable conflict",
    )

    assert (
        events_router._resume_retry_claim_id(claim, deferred)
        == "claim-r8-f-retry"
    )
    assert events_router._resume_retry_claim_id(claim, rejected) is None
    assert events_router._resume_retry_claim_id(None, deferred) is None
