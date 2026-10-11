"""Security #408: WebSocket namespace preflight and real-PG batch fencing.

The portable tests exercise the actual WebSocket ingress and local preflight;
the opt-in PostgreSQL test uses two independent engines, not a fake authority.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition, CapabilityKind,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation, CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.registration import (
    CapabilityRegistration, ClientCapabilityRegistration,
)
from se.src.runtimes.capability.registration import ClientCapabilityRegistrationService
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import get_websocket_identity
from se.src.transport.gateway.dependencies import get_container


def _definition(capability_id: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id, name=capability_id, description="WS security probe",
        kind=CapabilityKind.TOOL, execution_kind="PYTHON", source="LOCAL",
        input_schema={"type": "object"}, metadata={},
    )


def _request(user: str, client: str, connection: str, *ids: str):
    return ClientCapabilityRegistration(
        owner_id=user, client_id=client, connection_id=connection,
        capabilities=[
            CapabilityRegistration(
                definition=_definition(capability_id),
                kind=CapabilityKind.TOOL,
                location=CapabilityExecutionLocation.CLIENT,
                driver_kind="REMOTE_CLIENT",
                owner_type=CapabilityOwnerType.CLIENT,
                owner_id=user, connection_id=connection,
                implementation_id=f"{connection}-{capability_id}",
            )
            for capability_id in ids
        ],
    )


def _service(user: str, client: str, connection: str):
    catalog = CapabilityCatalog()
    connections = ConnectionRegistry()
    connections.register(
        session_id=f"session-{connection}", connection_id=connection,
        user_id=user, metadata={"client_id": client},
    )
    connections.activate(connection)
    return catalog, ClientCapabilityRegistrationService(catalog, connections)


class _AtomicFakeAuthority:
    def __init__(self):
        self.owners = {}
        self.calls = 0
        self.failure = None

    async def reserve_user_namespaces(self, definitions, *, publisher_id):
        from se.src.infrastructure.storage.repositories.capability_publications import (
            PublicationConflict,
        )
        self.calls += 1
        if self.failure:
            raise self.failure
        batch = {d.capability_id: d.kind.value for d in definitions}
        if any(
            key in self.owners and self.owners[key] != (publisher_id, kind)
            for key, kind in batch.items()
        ):
            raise PublicationConflict("A different origin reserved this ID.")
        self.owners.update({key: (publisher_id, kind) for key, kind in batch.items()})


@pytest.mark.asyncio
async def test_ws_durable_preflight_rejects_bad_owner_invalid_batch_and_sql_fault():
    from se.src.infrastructure.storage.repositories.capability_publications import (
        PublicationAuthorityUnavailable, PublicationConflict,
    )

    catalog, service = _service("alice", "client-a", "connection-a")
    authority = _AtomicFakeAuthority()
    valid = _request("alice", "client-a", "connection-a", "ok")
    with pytest.raises(PermissionError):
        await service.register_durable(
            valid, user_id=None, publication_authority=authority
        )
    with pytest.raises(PermissionError):
        await service.register_durable(
            valid, user_id="mallory", publication_authority=authority
        )
    assert authority.calls == 0 and not catalog.contains_definition("ok")

    duplicate = _request("alice", "client-a", "connection-a", "duplicate", "duplicate")
    with pytest.raises(ValueError):
        await service.register_durable(
            duplicate, user_id="alice", publication_authority=authority
        )
    assert authority.calls == 0
    assert not catalog.contains_definition("duplicate")

    invalid_client = _request("alice", "spoofed-client", "connection-a", "spoofed")
    with pytest.raises(PermissionError):
        await service.register_durable(
            invalid_client, user_id="alice", publication_authority=authority
        )
    assert authority.calls == 0
    assert not catalog.contains_definition("spoofed")

    authority.failure = PublicationAuthorityUnavailable("database unavailable")
    with pytest.raises(PublicationAuthorityUnavailable):
        await service.register_durable(
            valid, user_id="alice", publication_authority=authority
        )
    assert not catalog.contains_definition("ok")
    authority.failure = None

    authority.owners["blocked"] = ("bob", "TOOL")
    with pytest.raises(PublicationConflict):
        await service.register_durable(
            _request("alice", "client-a", "connection-a", "fresh", "blocked"),
            user_id="alice", publication_authority=authority,
        )
    assert "fresh" not in authority.owners
    assert not catalog.contains_definition("fresh")
    assert not catalog.contains_definition("blocked")

    registered = await service.register_durable(
        valid, user_id="alice", publication_authority=authority,
    )
    assert len(registered) == 1
    assert catalog.contains_definition("ok")
    assert authority.owners["ok"] == ("alice", "TOOL")


def test_real_websocket_capability_ingress_uses_durable_fence():
    """An actual ASGI WS message must await SQL fence before Catalog mutation."""
    catalog = CapabilityCatalog()
    registration = ClientCapabilityRegistrationService(catalog, None)
    connections = ConnectionRuntime(registration)
    registration.connections = connections.registry
    authority = _AtomicFakeAuthority()
    authority.owners["blocked"] = ("other", "TOOL")
    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(ws_manager=WebSocketConnectionManager()),
        connection_runtime=connections,
        capability_runtime=SimpleNamespace(publication_authority=authority),
    )
    identity = Identity(user_id="alice", auth_type="jwt")
    app = FastAPI()
    app.include_router(events_router.router)
    app.dependency_overrides[get_websocket_identity] = lambda: identity
    app.dependency_overrides[get_container] = lambda: container

    with TestClient(app) as client:
        with client.websocket_connect("/v1/events/ws") as ws:
            ws.send_json({
                "type": "connection.register", "message_id": "m1",
                "connection_id": "ws-one",
                "payload": {"client_id": "ws-client"},
            })
            assert ws.receive_json()["type"] == "connection.registered"

            ws.send_json({
                "type": "capability.register", "message_id": "m2",
                "connection_id": "ws-one",
                "payload": _request(
                    "alice", "ws-client", "ws-one", "ws-free", "blocked"
                ).model_dump(mode="json"),
            })
            error = ws.receive_json()
            assert error["status"] == "error"
            assert not catalog.contains_definition("ws-free")
            assert not catalog.contains_definition("blocked")

            ws.send_json({
                "type": "capability.register", "message_id": "m3",
                "connection_id": "ws-one",
                "payload": _request(
                    "alice", "ws-client", "ws-one", "ws-allowed"
                ).model_dump(mode="json"),
            })
            ack = ws.receive_json()
            assert ack["type"] == "capability.registered"
            assert catalog.contains_definition("ws-allowed")
            assert authority.owners["ws-allowed"] == ("alice", "TOOL")


@pytest.mark.asyncio
async def test_real_postgres_ws_atomic_batch_and_concurrent_http_owner_fence():
    """Opt-in native PG, two engines, rollback and cross-principal races."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from se.src.infrastructure.storage.repositories.capability_publications import (
        CapabilityPublicationRepository, PublicationConflict,
    )

    if os.getenv("ASSISTANT_SECURITY_PG_E2E") != "1":
        pytest.skip("Real PG WS evidence requires ASSISTANT_SECURITY_PG_E2E=1")
    dsn = os.getenv("ASSISTANT_SKILL_PUBLICATION_DATABASE_URL", "")
    assert dsn.startswith("postgresql+asyncpg://")
    command.upgrade(Config("alembic_skill_publications.ini"), "head")
    engine_a = create_async_engine(dsn, pool_pre_ping=True)
    engine_b = create_async_engine(dsn, pool_pre_ping=True)
    authority_a = CapabilityPublicationRepository(
        async_sessionmaker(engine_a, expire_on_commit=False)
    )
    authority_b = CapabilityPublicationRepository(
        async_sessionmaker(engine_b, expire_on_commit=False)
    )
    prefix = f"sec-ws-{uuid.uuid4().hex}"
    try:
        await asyncio.gather(authority_a.ensure_ready(), authority_b.ensure_ready())
        catalog_a, service_a = _service("alice", "client-a", "connection-a")
        catalog_b, service_b = _service("bob", "client-b", "connection-b")

        # Invalid batch's first valid insertion MUST roll back because the
        # second logical ID is already owned by immutable SYSTEM origin.
        first, blocked = f"{prefix}-new", f"{prefix}-system"
        await authority_b.reserve_system_namespace(_definition(blocked))
        with pytest.raises(PublicationConflict):
            await service_a.register_durable(
                _request("alice", "client-a", "connection-a", first, blocked),
                user_id="alice", publication_authority=authority_a,
            )
        assert not catalog_a.contains_definition(first)
        async with engine_a.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM skill_publications.capability_publications "
                        "WHERE capability_id = :id"
                    ), {"id": first},
                )
            ).scalar_one() == 0

        # HTTP equivalent reserves on the other engine, even though its
        # logical definition never entered this worker's local Catalog.
        http_first = f"{prefix}-http"
        await authority_b.reserve_user_namespace(
            _definition(http_first), publisher_id="bob"
        )
        with pytest.raises(PublicationConflict):
            await service_a.register_durable(
                _request("alice", "client-a", "connection-a", http_first),
                user_id="alice", publication_authority=authority_a,
            )
        assert not catalog_a.contains_definition(http_first)

        # Simultaneous WS A vs WS B on different engine/session instances.
        shared = f"{prefix}-shared"
        results = await asyncio.wait_for(
            asyncio.gather(
                service_a.register_durable(
                    _request("alice", "client-a", "connection-a", shared),
                    user_id="alice", publication_authority=authority_a,
                ),
                service_b.register_durable(
                    _request("bob", "client-b", "connection-b", shared),
                    user_id="bob", publication_authority=authority_b,
                ),
                return_exceptions=True,
            ), timeout=30,
        )
        winners = [r for r in results if isinstance(r, list)]
        losers = [r for r in results if isinstance(r, BaseException)]
        assert len(winners) == len(losers) == 1, [type(r).__name__ for r in results]
        assert isinstance(losers[0], PublicationConflict)
        assert catalog_a.contains_definition(shared) != catalog_b.contains_definition(shared)
        async with engine_b.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT publisher_id, purpose, visibility FROM "
                        "skill_publications.capability_publications WHERE capability_id = :id"
                    ), {"id": shared},
                )
            ).one()
        assert row[0] in {"alice", "bob"}
        assert tuple(row[1:]) == ("NAMESPACE_RESERVATION", "NONE")
    finally:
        await engine_b.dispose()
        await engine_a.dispose()
