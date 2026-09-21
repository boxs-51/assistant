from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import CapabilityRuntime as ClientCapabilityRuntime
from cl.src.core.client_invocation_ledger import ClientInvocationLedger
from cl.src.core.realtime_client import GatewayRealtimeClient

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    CapabilityWaitReason,
    ExistingInvocationContinuationMode,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registration import ClientCapabilityRegistrationService
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import get_websocket_identity
from se.src.transport.gateway.dependencies import get_container


CAPABILITY_ID = "desktop.r7e"
USER_ID = "r7e-user"
CLIENT_ID = "r7e-client"
SESSION_ID = "r7e-session"
K1 = "r7e-k1"


def _identity() -> Identity:
    return Identity(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_type="jwt",
    )


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_until(predicate, *, timeout=8.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for R7-E real-TCP condition")


def _build_gateway_app():
    catalog = CapabilityCatalog()
    registration = ClientCapabilityRegistrationService(catalog, None)
    connections = ConnectionRuntime(registration)
    registration.connections = connections.registry

    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
        connection_runtime=connections,
    )
    container.require = lambda key: getattr(container, key)

    app = FastAPI()
    app.include_router(events_router.router)
    app.state.container = container
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_websocket_identity] = _identity
    return app, catalog, connections


async def _start_gateway(app):
    port = _free_tcp_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=2.0,
        )
    )
    task = asyncio.create_task(server.serve(), name="r7-e-real-tcp-gateway")
    await _wait_until(lambda: server.started or task.done(), timeout=10.0)
    if task.done():
        await task
        raise AssertionError("R7-E gateway exited during startup")
    return server, task, port


async def _stop_gateway(server, task):
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    server.should_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        return
    except asyncio.TimeoutError:
        server.force_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _client_registry(tool, *, idempotency: CapabilityIdempotency):
    return SimpleNamespace(
        tools={
            CAPABILITY_ID: {
                "func": tool,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "description": "R7-E real TCP capability",
                    "version": "1.0",
                    "idempotency": idempotency.value,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                        "required": ["value"],
                    },
                },
            }
        }
    )


async def _connect_client(
    *,
    port: int,
    registry,
    ledger: ClientInvocationLedger,
    connection_id: str,
):
    holder = {}
    realtime = GatewayRealtimeClient(
        f"http://127.0.0.1:{port}",
        {"Authorization": "Bearer r7-e"},
        connection_id=connection_id,
        session_id=SESSION_ID,
        client_id=CLIENT_ID,
        heartbeat_interval=60.0,
        on_message=lambda envelope: holder["capabilities"].handle_message(envelope),
    )
    dispatcher = CapabilityDispatcher(
        registry,
        realtime,
        invocation_ledger=ledger,
        client_id=CLIENT_ID,
        principal_id=USER_ID,
    )
    capabilities = ClientCapabilityRuntime(
        registry,
        realtime,
        client_id=CLIENT_ID,
        owner_id=USER_ID,
        dispatcher=dispatcher,
    )
    holder["capabilities"] = capabilities

    registered = await asyncio.to_thread(realtime.connect, wait_timeout=5.0)
    assert registered["type"] == "connection.registered"
    capability_registered = await asyncio.to_thread(
        capabilities.register,
        timeout=5.0,
    )
    assert capability_registered["type"] == "capability.registered"
    return SimpleNamespace(
        realtime=realtime,
        dispatcher=dispatcher,
        capabilities=capabilities,
        connection_id=connection_id,
    )


async def _close_client(generation):
    if generation is None:
        return
    await asyncio.to_thread(generation.realtime.close)
    generation.dispatcher.shutdown()


def _server_runtime(catalog, connections):
    store = InMemoryCapabilityInvocationStore()
    lifecycle = CapabilityInvocationLifecycle(store)
    runtime = CapabilityRuntime(
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=connections.registry,
        ),
        connection_registry=connections.registry,
        realtime=connections.realtime,
        invocation_lifecycle=lifecycle,
    )
    return runtime, store


async def _seed_waiting(
    runtime: CapabilityRuntime,
    *,
    invocation_id: str,
    outcome: RemoteOutcomeState,
    value: str = "expected",
):
    definition = runtime.catalog.get_definition(CAPABILITY_ID)
    fingerprint = capability_request_fingerprint(
        capability_id=CAPABILITY_ID,
        capability_version=definition.version,
        arguments={"value": value},
    )
    invocation = CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id=CAPABILITY_ID,
        capability_version=definition.version,
        kind=definition.kind,
        execution_mode=definition.execution_mode,
        idempotency=definition.idempotency,
        request_fingerprint=fingerprint,
        owner_user_id=USER_ID,
        origin_client_id=CLIENT_ID,
        remote_outcome_state=outcome,
        implementation_id=f"{K1}:{CAPABILITY_ID}",
        driver_kind="REMOTE_CLIENT",
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        session_id=SESSION_ID,
        execution_id="exec-r7e-real",
        tool_call_id=f"call-{invocation_id}",
        connection_id=K1,
        attempt=1,
        max_attempts=1,
        arguments={"value": value},
    )
    await runtime.invocation_lifecycle.create(invocation)
    await runtime.invocation_lifecycle.store.save_attempt(
        CapabilityInvocationAttempt(
            attempt_id=f"att-{invocation_id}-1",
            invocation_id=invocation_id,
            attempt_number=1,
            implementation_id=f"{K1}:{CAPABILITY_ID}",
            driver_kind="REMOTE_CLIENT",
            connection_id=K1,
            state=CapabilityInvocationState.FAILED,
        )
    )
    return invocation, fingerprint


@pytest.mark.asyncio
async def test_r7_e_real_tcp_dispatch_not_dispatched_reuses_same_invocation(tmp_path):
    app, catalog, connections = _build_gateway_app()
    server, server_task, port = await _start_gateway(app)
    generation = None
    calls = []
    try:
        generation = await _connect_client(
            port=port,
            registry=_client_registry(
                lambda value, **kwargs: (
                    calls.append(value)
                    or {"source": "client", "value": value}
                ),
                idempotency=CapabilityIdempotency.UNKNOWN,
            ),
            ledger=ClientInvocationLedger(tmp_path / "dispatch.sqlite3"),
            connection_id="r7e-k2-dispatch",
        )
        runtime, store = _server_runtime(catalog, connections)
        invocation, fingerprint = await _seed_waiting(
            runtime,
            invocation_id="inv-r7e-dispatch",
            outcome=RemoteOutcomeState.NOT_DISPATCHED,
        )

        result = await asyncio.wait_for(
            runtime.continue_invocation(
                invocation.invocation_id,
                target_connection_id=generation.connection_id,
                mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
                expected_revision=invocation.revision,
                expected_request_fingerprint=fingerprint,
            ),
            timeout=5.0,
        )

        persisted = await store.get(invocation.invocation_id)
        attempts = await store.list_attempts(invocation.invocation_id)
        assert calls == ["expected"]
        assert result.invocation_id == invocation.invocation_id
        assert result.output == {"source": "client", "value": "expected"}
        assert len(store.items) == 1
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.COMPLETED
        assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert attempts[-1].connection_id == generation.connection_id
    finally:
        await _close_client(generation)
        await _stop_gateway(server, server_task)


@pytest.mark.asyncio
async def test_r7_e_real_tcp_replay_safe_idempotent_executes_attempt_two(tmp_path):
    app, catalog, connections = _build_gateway_app()
    server, server_task, port = await _start_gateway(app)
    generation = None
    calls = []
    try:
        generation = await _connect_client(
            port=port,
            registry=_client_registry(
                lambda value, **kwargs: (
                    calls.append(value)
                    or {"source": "replay", "value": value}
                ),
                idempotency=CapabilityIdempotency.IDEMPOTENT,
            ),
            ledger=ClientInvocationLedger(tmp_path / "idempotent.sqlite3"),
            connection_id="r7e-k2-idempotent",
        )
        runtime, store = _server_runtime(catalog, connections)
        invocation, fingerprint = await _seed_waiting(
            runtime,
            invocation_id="inv-r7e-idempotent",
            outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
        )

        result = await asyncio.wait_for(
            runtime.continue_invocation(
                invocation.invocation_id,
                target_connection_id=generation.connection_id,
                mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
                expected_revision=invocation.revision,
                expected_request_fingerprint=fingerprint,
            ),
            timeout=5.0,
        )

        attempts = await store.list_attempts(invocation.invocation_id)
        assert calls == ["expected"]
        assert result.invocation_id == invocation.invocation_id
        assert result.metadata["continuation_mode"] == "REPLAY_SAFE"
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert len(store.items) == 1
    finally:
        await _close_client(generation)
        await _stop_gateway(server, server_task)


@pytest.mark.asyncio
async def test_r7_e_real_tcp_deduplicated_terminal_ledger_prevents_second_effect(tmp_path):
    app, catalog, connections = _build_gateway_app()
    server, server_task, port = await _start_gateway(app)
    generation = None
    calls = []
    invocation_id = "inv-r7e-deduplicated"
    value = "expected"
    fingerprint = capability_request_fingerprint(
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        arguments={"value": value},
    )
    ledger = ClientInvocationLedger(tmp_path / "deduplicated.sqlite3")
    ledger.prepare(
        client_id=CLIENT_ID,
        principal_id=USER_ID,
        invocation_id=invocation_id,
        capability_id=CAPABILITY_ID,
        capability_version="1.0",
        request_fingerprint=fingerprint,
        idempotency=CapabilityIdempotency.DEDUPLICATED.value,
    )
    ledger.mark_running(
        client_id=CLIENT_ID,
        principal_id=USER_ID,
        invocation_id=invocation_id,
    )
    ledger.commit_terminal(
        client_id=CLIENT_ID,
        principal_id=USER_ID,
        invocation_id=invocation_id,
        terminal_type="result",
        terminal_payload={
            "output": {
                "source": "prior-k1",
                "value": value,
                "external_effect_count": 1,
            }
        },
    )

    try:
        generation = await _connect_client(
            port=port,
            registry=_client_registry(
                lambda value, **kwargs: (
                    calls.append(value)
                    or {
                        "source": "unexpected-second-effect",
                        "value": value,
                    }
                ),
                idempotency=CapabilityIdempotency.DEDUPLICATED,
            ),
            ledger=ledger,
            connection_id="r7e-k2-deduplicated",
        )
        runtime, store = _server_runtime(catalog, connections)
        invocation, seeded_fingerprint = await _seed_waiting(
            runtime,
            invocation_id=invocation_id,
            outcome=RemoteOutcomeState.OUTCOME_UNKNOWN,
            value=value,
        )
        assert seeded_fingerprint == fingerprint

        result = await asyncio.wait_for(
            runtime.continue_invocation(
                invocation.invocation_id,
                target_connection_id=generation.connection_id,
                mode=ExistingInvocationContinuationMode.REPLAY_SAFE,
                expected_revision=invocation.revision,
                expected_request_fingerprint=fingerprint,
            ),
            timeout=5.0,
        )

        persisted = await store.get(invocation_id)
        attempts = await store.list_attempts(invocation_id)
        assert calls == []
        assert result.output == {
            "source": "prior-k1",
            "value": value,
            "external_effect_count": 1,
        }
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.COMPLETED
        assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert len(store.items) == 1
    finally:
        await _close_client(generation)
        await _stop_gateway(server, server_task)
