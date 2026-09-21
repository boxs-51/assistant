from __future__ import annotations

import asyncio
import inspect
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
import uvicorn
from fastapi import FastAPI

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import (
    CapabilityRuntime as ClientCapabilityRuntime,
)
from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerState,
)
from cl.src.core.realtime_client import GatewayRealtimeClient

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.error import (
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_OUTCOME_UNKNOWN,
    REMOTE_RESULT_RECONCILIATION_REQUIRED,
)
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.reconciliation import (
    RemoteReconciliationStatus,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.registry import ConnectionStateError
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import (
    get_websocket_identity,
)
from se.src.transport.gateway.dependencies import get_container


CAPABILITY_ID = "desktop.r6e"
OWNER_ID = "r6e-user-1"
OTHER_OWNER_ID = "r6e-user-2"
CLIENT_ID = "r6e-client-1"
OTHER_CLIENT_ID = "r6e-client-2"
SESSION_ID = "r6e-session-1"


def _identity(user_id: str = OWNER_ID) -> Identity:
    return Identity(
        user_id=user_id,
        session_id=SESSION_ID,
        auth_type="jwt",
    )


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_until_async(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for R6-E condition.")


def _build_gateway_app(*, websocket_user_id: str = OWNER_ID):
    catalog = CapabilityCatalog()
    registration = ClientCapabilityRegistrationService(catalog, None)
    connection_runtime = ConnectionRuntime(registration)
    registration.connections = connection_runtime.registry

    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
        connection_runtime=connection_runtime,
    )
    app = FastAPI()
    app.include_router(events_router.router)
    container.require = lambda key: getattr(container, key)
    app.state.container = container
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_websocket_identity] = (
        lambda: _identity(websocket_user_id)
    )
    return app, catalog, connection_runtime


async def _start_uvicorn(app):
    port = _free_tcp_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        timeout_graceful_shutdown=2.0,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(
        server.serve(),
        name="r6-e-real-tcp-gateway",
    )
    await _wait_until_async(
        lambda: server.started or task.done(),
        timeout=10.0,
    )
    if task.done():
        await task
        raise AssertionError("R6-E gateway exited during startup.")
    return server, task, port


async def _stop_uvicorn(server, task, *, timeout: float = 5.0):
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    server.should_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return
    except asyncio.TimeoutError:
        server.force_exit = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _client_registry(
    tool,
    *,
    idempotency: CapabilityIdempotency,
):
    return SimpleNamespace(
        tools={
            CAPABILITY_ID: {
                "func": tool,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "description": "R6-E real TCP client capability",
                    "version": "1.0",
                    "idempotency": idempotency.value,
                    "base_risk": "LOW",
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


class _CloseBeforeResultRealtime(GatewayRealtimeClient):
    """Persisted terminal exists, then the real transport disappears."""

    def __init__(self, *args, result_close_event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._result_close_event = result_close_event or threading.Event()

    def send_result(self, invocation_id, result, **correlation):
        self.close()
        self._result_close_event.set()
        return super().send_result(
            invocation_id,
            result,
            **correlation,
        )


class _BlockingResultRealtime(GatewayRealtimeClient):
    """Hold a real K1 terminal frame after ledger commit until the test releases it."""

    def __init__(
        self,
        *args,
        result_ready_event=None,
        allow_send_event=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._result_ready_event = result_ready_event or threading.Event()
        self._allow_send_event = allow_send_event or threading.Event()

    def send_result(self, invocation_id, result, **correlation):
        self._result_ready_event.set()
        if not self._allow_send_event.wait(5.0):
            raise TimeoutError("R6-E late-result barrier timed out.")
        return super().send_result(
            invocation_id,
            result,
            **correlation,
        )


@dataclass
class _ClientGeneration:
    realtime: GatewayRealtimeClient
    dispatcher: CapabilityDispatcher
    capabilities: ClientCapabilityRuntime
    ledger: ClientInvocationLedger
    connection_id: str
    client_id: str
    principal_id: str


async def _connect_client(
    *,
    port: int,
    registry,
    ledger: ClientInvocationLedger,
    connection_id: str,
    client_id: str = CLIENT_ID,
    principal_id: str = OWNER_ID,
    dispatcher: CapabilityDispatcher | None = None,
    realtime_cls=GatewayRealtimeClient,
    realtime_kwargs: dict[str, Any] | None = None,
) -> _ClientGeneration:
    holder: dict[str, Any] = {}
    realtime = realtime_cls(
        f"http://127.0.0.1:{port}",
        {"Authorization": "Bearer r6-e"},
        connection_id=connection_id,
        session_id=SESSION_ID,
        client_id=client_id,
        heartbeat_interval=60.0,
        on_message=lambda envelope: holder["capabilities"].handle_message(
            envelope
        ),
        **dict(realtime_kwargs or {}),
    )

    if dispatcher is None:
        dispatcher = CapabilityDispatcher(
            registry,
            realtime,
            invocation_ledger=ledger,
            client_id=client_id,
            principal_id=principal_id,
        )
    else:
        dispatcher.set_realtime(realtime)
        dispatcher.set_identity(client_id, principal_id)

    capabilities = ClientCapabilityRuntime(
        registry,
        realtime,
        client_id=client_id,
        owner_id=principal_id,
        dispatcher=dispatcher,
    )
    holder["capabilities"] = capabilities

    registered = await asyncio.to_thread(
        realtime.connect,
        wait_timeout=5.0,
    )
    assert registered["type"] == "connection.registered"
    registration = await asyncio.to_thread(
        capabilities.register,
        timeout=5.0,
    )
    assert registration["type"] == "capability.registered"

    return _ClientGeneration(
        realtime=realtime,
        dispatcher=dispatcher,
        capabilities=capabilities,
        ledger=ledger,
        connection_id=connection_id,
        client_id=client_id,
        principal_id=principal_id,
    )


async def _close_generation(
    generation: _ClientGeneration | None,
    *,
    shutdown_dispatcher: bool,
) -> None:
    if generation is None:
        return
    await asyncio.to_thread(generation.realtime.close)
    if shutdown_dispatcher:
        generation.dispatcher.shutdown()


def _server_runtime(catalog, connection_runtime):
    store = InMemoryCapabilityInvocationStore()
    lifecycle = CapabilityInvocationLifecycle(store)
    runtime = CapabilityRuntime(
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=connection_runtime.registry,
        ),
        connection_registry=connection_runtime.registry,
        realtime=connection_runtime.realtime,
        invocation_lifecycle=lifecycle,
    )
    return runtime, store


class _ServerDriver(BaseCapabilityDriver):
    def __init__(self, definition, handler):
        super().__init__(definition)
        self._handler = handler

    async def execute(self, context, arguments):
        value = self._handler(context, arguments)
        if inspect.isawaitable(value):
            value = await value
        return value


def _add_server_implementation(
    runtime: CapabilityRuntime,
    catalog: CapabilityCatalog,
    handler,
):
    definition = catalog.get_definition(CAPABILITY_ID)
    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id=f"server:{CAPABILITY_ID}",
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="PYTHON",
    )
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )
    runtime.driver_registry.bind(
        implementation.implementation_id,
        _ServerDriver(definition, handler),
    )
    return implementation


async def _execute(
    runtime: CapabilityRuntime,
    *,
    invocation_id: str,
    connection_id: str,
    max_attempts: int = 1,
    timeout_seconds: float | None = None,
):
    return await runtime.execute_capability(
        CAPABILITY_ID,
        {"value": invocation_id},
        _identity(),
        invocation_id=invocation_id,
        session_id=SESSION_ID,
        connection_id=connection_id,
        timeout_seconds=timeout_seconds,
        metadata={"max_attempts": max_attempts},
    )


async def _seed_waiting_invocation(
    runtime: CapabilityRuntime,
    *,
    invocation_id: str,
    owner_user_id: str = OWNER_ID,
    origin_client_id: str = CLIENT_ID,
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
        owner_user_id=owner_user_id,
        origin_client_id=origin_client_id,
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
        session_id=SESSION_ID,
        connection_id="old-connection",
        arguments={"value": value},
    )
    await runtime.invocation_lifecycle.create(invocation)
    return invocation


def _ledger_record(
    ledger: ClientInvocationLedger,
    invocation_id: str,
    *,
    client_id: str = CLIENT_ID,
    principal_id: str = OWNER_ID,
):
    return ledger.get(
        client_id=client_id,
        principal_id=principal_id,
        invocation_id=invocation_id,
    )


def test_r6_e1_disconnect_before_dispatch_preserves_not_dispatched(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        client_calls = []
        server_calls = []
        original_require = connections.registry.require_active_socket

        try:
            registry = _client_registry(
                lambda value, **kwargs: client_calls.append(value) or value,
                idempotency=CapabilityIdempotency.UNKNOWN,
            )
            generation = await _connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(tmp_path / "e1.sqlite3"),
                connection_id="r6e-e1-k1",
            )
            runtime, store = _server_runtime(catalog, connections)
            _add_server_implementation(
                runtime,
                catalog,
                lambda context, arguments: (
                    server_calls.append(context.invocation_id)
                    or {
                        "source": "server",
                        "invocation_id": context.invocation_id,
                    }
                ),
            )

            injected = False

            def reject_before_send(connection_id):
                nonlocal injected
                if connection_id == generation.connection_id and not injected:
                    injected = True
                    raise ConnectionStateError(
                        "R6-E1 fault: connection lost before send boundary"
                    )
                return original_require(connection_id)

            connections.registry.require_active_socket = reject_before_send
            result = await _execute(
                runtime,
                invocation_id="r6e-e1",
                connection_id=generation.connection_id,
                max_attempts=2,
            )

            persisted = store.items["r6e-e1"]
            attempts = await store.list_attempts("r6e-e1")
            assert injected is True
            assert client_calls == []
            assert server_calls == ["r6e-e1"]
            assert result.invocation_id == "r6e-e1"
            assert persisted.remote_outcome_state is RemoteOutcomeState.NOT_DISPATCHED
            assert len(attempts) == 2
            assert attempts[0].connection_id == generation.connection_id
            assert attempts[1].implementation_id == f"server:{CAPABILITY_ID}"
        finally:
            connections.registry.require_active_socket = original_require
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        started = threading.Event()
        release = threading.Event()
        client_effects = []
        server_calls = []

        def tool(value, **kwargs):
            client_effects.append(value)
            started.set()
            release.wait(5.0)
            return {"source": "client", "value": value}

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    tool,
                    idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
                ),
                ledger=ClientInvocationLedger(tmp_path / "e2.sqlite3"),
                connection_id="r6e-e2-k1",
            )
            runtime, store = _server_runtime(catalog, connections)
            _add_server_implementation(
                runtime,
                catalog,
                lambda context, arguments: (
                    server_calls.append(context.invocation_id)
                    or {"source": "server"}
                ),
            )

            task = asyncio.create_task(
                _execute(
                    runtime,
                    invocation_id="r6e-e2",
                    connection_id=generation.connection_id,
                    max_attempts=2,
                )
            )
            assert await asyncio.to_thread(started.wait, 5.0)
            await asyncio.to_thread(generation.realtime.close)

            with pytest.raises(CapabilityError) as raised:
                await task
            assert raised.value.code == REMOTE_OUTCOME_UNKNOWN

            persisted = store.items["r6e-e2"]
            assert persisted.state is CapabilityInvocationState.WAITING
            assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
            assert persisted.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN
            assert client_effects == ["r6e-e2"]
            assert server_calls == []
            assert len(await store.list_attempts("r6e-e2")) == 1
        finally:
            release.set()
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e3_same_process_reconnect_recovers_lost_terminal_once(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        first = None
        second = None
        calls = []
        close_seen = threading.Event()
        ledger = ClientInvocationLedger(tmp_path / "e3.sqlite3")

        def tool(value, **kwargs):
            calls.append(value)
            return {"source": "client", "value": value, "ordinal": len(calls)}

        try:
            registry = _client_registry(
                tool,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            )
            first = await _connect_client(
                port=port,
                registry=registry,
                ledger=ledger,
                connection_id="r6e-e3-k1",
                realtime_cls=_CloseBeforeResultRealtime,
                realtime_kwargs={"result_close_event": close_seen},
            )
            runtime, store = _server_runtime(catalog, connections)

            with pytest.raises(CapabilityError) as raised:
                await _execute(
                    runtime,
                    invocation_id="r6e-e3",
                    connection_id=first.connection_id,
                )
            assert raised.value.code == REMOTE_OUTCOME_UNKNOWN
            assert await asyncio.to_thread(close_seen.wait, 5.0)

            record = _ledger_record(ledger, "r6e-e3")
            assert record is not None
            assert record.state is ClientInvocationLedgerState.TERMINAL

            second = await _connect_client(
                port=port,
                registry=registry,
                ledger=ledger,
                connection_id="r6e-e3-k2",
                dispatcher=first.dispatcher,
            )
            reconciled = await runtime.reconcile_remote_invocation(
                "r6e-e3",
                second.connection_id,
                timeout=5.0,
            )

            assert reconciled.status is RemoteReconciliationStatus.TERMINAL
            assert reconciled.terminal_payload == {
                "output": {
                    "source": "client",
                    "value": "r6e-e3",
                    "ordinal": 1,
                }
            }
            persisted = store.items["r6e-e3"]
            assert persisted.state is CapabilityInvocationState.COMPLETED
            assert persisted.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
            assert calls == ["r6e-e3"]
        finally:
            if second is not None:
                await _close_generation(second, shutdown_dispatcher=True)
            elif first is not None:
                await _close_generation(first, shutdown_dispatcher=True)
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        first = None
        second = None
        calls = []
        path = tmp_path / "e4.sqlite3"

        def tool(value, **kwargs):
            calls.append(value)
            return {"source": "client", "value": value}

        try:
            registry = _client_registry(
                tool,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            )
            first = await _connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(path),
                connection_id="r6e-e4-k1",
                realtime_cls=_CloseBeforeResultRealtime,
            )
            runtime, store = _server_runtime(catalog, connections)

            with pytest.raises(CapabilityError) as raised:
                await _execute(
                    runtime,
                    invocation_id="r6e-e4",
                    connection_id=first.connection_id,
                )
            assert raised.value.code == REMOTE_OUTCOME_UNKNOWN

            await _close_generation(first, shutdown_dispatcher=True)
            first = None

            restarted_ledger = ClientInvocationLedger(path)
            second = await _connect_client(
                port=port,
                registry=registry,
                ledger=restarted_ledger,
                connection_id="r6e-e4-k2",
            )
            reconciled = await runtime.reconcile_remote_invocation(
                "r6e-e4",
                second.connection_id,
                timeout=5.0,
            )

            assert reconciled.status is RemoteReconciliationStatus.TERMINAL
            assert store.items["r6e-e4"].state is CapabilityInvocationState.COMPLETED
            assert calls == ["r6e-e4"]
        finally:
            await _close_generation(
                second or first,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e5_running_after_client_restart_reconciles_unknown_and_never_reexecutes(
    tmp_path,
):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        first = None
        second = None
        entered = []
        started = threading.Event()
        path = tmp_path / "e5.sqlite3"

        def tool(value, cancel_event=None, **kwargs):
            entered.append(value)
            started.set()
            while cancel_event is None or not cancel_event.is_set():
                time.sleep(0.01)
            return {"value": value}

        try:
            registry = _client_registry(
                tool,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            )
            first = await _connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(path),
                connection_id="r6e-e5-k1",
            )
            runtime, store = _server_runtime(catalog, connections)
            task = asyncio.create_task(
                _execute(
                    runtime,
                    invocation_id="r6e-e5",
                    connection_id=first.connection_id,
                )
            )
            assert await asyncio.to_thread(started.wait, 5.0)
            running = _ledger_record(first.ledger, "r6e-e5")
            assert running is not None
            assert running.state is ClientInvocationLedgerState.RUNNING

            await asyncio.to_thread(first.realtime.close)
            with pytest.raises(CapabilityError) as raised:
                await task
            assert raised.value.code == REMOTE_OUTCOME_UNKNOWN

            first.dispatcher.shutdown()
            first = None

            restarted_ledger = ClientInvocationLedger(path)
            await _wait_until_async(
                lambda: (
                    _ledger_record(restarted_ledger, "r6e-e5").state
                    is ClientInvocationLedgerState.RUNNING
                )
            )
            second = await _connect_client(
                port=port,
                registry=registry,
                ledger=restarted_ledger,
                connection_id="r6e-e5-k2",
            )
            reconciled = await runtime.reconcile_remote_invocation(
                "r6e-e5",
                second.connection_id,
                timeout=5.0,
            )

            assert reconciled.status is RemoteReconciliationStatus.UNKNOWN
            assert reconciled.ledger_state == "RUNNING"
            assert entered == ["r6e-e5"]
            persisted = store.items["r6e-e5"]
            assert persisted.state is CapabilityInvocationState.WAITING
            assert persisted.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN
        finally:
            await _close_generation(
                second or first,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e6_idempotent_unknown_outcome_uses_same_invocation_for_controlled_replay(
    tmp_path,
):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        client_calls = []
        server_calls = []
        realtime_holder = {}

        def tool(value, **kwargs):
            client_calls.append(value)
            realtime_holder["realtime"].close()
            return {"source": "client"}

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    tool,
                    idempotency=CapabilityIdempotency.IDEMPOTENT,
                ),
                ledger=ClientInvocationLedger(tmp_path / "e6.sqlite3"),
                connection_id="r6e-e6-k1",
            )
            realtime_holder["realtime"] = generation.realtime
            runtime, store = _server_runtime(catalog, connections)
            _add_server_implementation(
                runtime,
                catalog,
                lambda context, arguments: (
                    server_calls.append(context.invocation_id)
                    or {
                        "source": "server",
                        "invocation_id": context.invocation_id,
                    }
                ),
            )

            result = await _execute(
                runtime,
                invocation_id="r6e-e6",
                connection_id=generation.connection_id,
                max_attempts=2,
            )
            attempts = await store.list_attempts("r6e-e6")

            assert client_calls == ["r6e-e6"]
            assert server_calls == ["r6e-e6"]
            assert result.invocation_id == "r6e-e6"
            assert result.output["invocation_id"] == "r6e-e6"
            assert len(attempts) == 2
            assert {item.invocation_id for item in attempts} == {"r6e-e6"}
            assert store.items["r6e-e6"].remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN
        finally:
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e7_deduplicated_replay_preserves_key_and_external_effect_once(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        external_effects = set()
        server_seen = []
        realtime_holder = {}

        def client_tool(value, invocation_id=None, **kwargs):
            external_effects.add(invocation_id)
            realtime_holder["realtime"].close()
            return {"source": "client", "invocation_id": invocation_id}

        def server_tool(context, arguments):
            invocation_id = context.invocation_id
            server_seen.append(invocation_id)
            duplicate = invocation_id in external_effects
            if not duplicate:
                external_effects.add(invocation_id)
            return {
                "source": "server",
                "invocation_id": invocation_id,
                "deduplicated": duplicate,
            }

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    client_tool,
                    idempotency=CapabilityIdempotency.DEDUPLICATED,
                ),
                ledger=ClientInvocationLedger(tmp_path / "e7.sqlite3"),
                connection_id="r6e-e7-k1",
            )
            realtime_holder["realtime"] = generation.realtime
            runtime, store = _server_runtime(catalog, connections)
            _add_server_implementation(runtime, catalog, server_tool)

            result = await _execute(
                runtime,
                invocation_id="r6e-e7",
                connection_id=generation.connection_id,
                max_attempts=2,
            )

            assert result.output == {
                "source": "server",
                "invocation_id": "r6e-e7",
                "deduplicated": True,
            }
            assert external_effects == {"r6e-e7"}
            assert server_seen == ["r6e-e7"]
            attempts = await store.list_attempts("r6e-e7")
            assert len(attempts) == 2
            assert {item.invocation_id for item in attempts} == {"r6e-e7"}
        finally:
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e8_real_reconciliation_rejects_semantic_fingerprint_conflict(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        tool_calls = []
        ledger = ClientInvocationLedger(tmp_path / "e8.sqlite3")

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    lambda value, **kwargs: tool_calls.append(value) or value,
                    idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
                ),
                ledger=ledger,
                connection_id="r6e-e8-k2",
            )
            runtime, store = _server_runtime(catalog, connections)
            invocation = await _seed_waiting_invocation(
                runtime,
                invocation_id="r6e-e8",
            )
            conflicting = capability_request_fingerprint(
                capability_id=CAPABILITY_ID,
                capability_version="1.0",
                arguments={"value": "different"},
            )
            ledger.prepare(
                client_id=CLIENT_ID,
                principal_id=OWNER_ID,
                invocation_id="r6e-e8",
                capability_id=CAPABILITY_ID,
                capability_version="1.0",
                request_fingerprint=conflicting,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT.value,
            )

            with pytest.raises(CapabilityError) as raised:
                await runtime.reconcile_remote_invocation(
                    "r6e-e8",
                    generation.connection_id,
                    timeout=5.0,
                )

            assert raised.value.code == REMOTE_INVOCATION_CONFLICT
            assert tool_calls == []
            assert store.items["r6e-e8"].state is CapabilityInvocationState.WAITING
            assert invocation.request_fingerprint != conflicting
        finally:
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("websocket_user_id", "client_id"),
    [
        (OTHER_OWNER_ID, CLIENT_ID),
        (OWNER_ID, OTHER_CLIENT_ID),
    ],
)
def test_r6_e9_foreign_principal_or_client_cannot_reconcile(
    tmp_path,
    websocket_user_id,
    client_id,
):
    async def scenario():
        app, catalog, connections = _build_gateway_app(
            websocket_user_id=websocket_user_id,
        )
        server, server_task, port = await _start_uvicorn(app)
        generation = None

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    lambda value, **kwargs: value,
                    idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
                ),
                ledger=ClientInvocationLedger(
                    tmp_path
                    / f"e9-{websocket_user_id}-{client_id}.sqlite3"
                ),
                connection_id=f"r6e-e9-{websocket_user_id}-{client_id}",
                client_id=client_id,
                principal_id=websocket_user_id,
            )
            runtime, store = _server_runtime(catalog, connections)
            await _seed_waiting_invocation(
                runtime,
                invocation_id="r6e-e9",
                owner_user_id=OWNER_ID,
                origin_client_id=CLIENT_ID,
            )

            with pytest.raises(CapabilityError) as raised:
                await runtime.reconcile_remote_invocation(
                    "r6e-e9",
                    generation.connection_id,
                    timeout=5.0,
                )
            assert raised.value.code == "CAPABILITY_UNAUTHORIZED"
            assert store.items["r6e-e9"].state is CapabilityInvocationState.WAITING
        finally:
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e10_late_k1_terminal_cannot_mutate_k2_committed_reconciliation(tmp_path):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        first = None
        second = None
        ready = threading.Event()
        allow_send = threading.Event()
        calls = []
        path = tmp_path / "e10.sqlite3"

        def tool(value, **kwargs):
            calls.append(value)
            return {"value": value, "ordinal": len(calls)}

        try:
            registry = _client_registry(
                tool,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            )
            first = await _connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(path),
                connection_id="r6e-e10-k1",
                realtime_cls=_BlockingResultRealtime,
                realtime_kwargs={
                    "result_ready_event": ready,
                    "allow_send_event": allow_send,
                },
            )
            runtime, store = _server_runtime(catalog, connections)
            task = asyncio.create_task(
                _execute(
                    runtime,
                    invocation_id="r6e-e10",
                    connection_id=first.connection_id,
                )
            )
            assert await asyncio.to_thread(ready.wait, 5.0)
            record = _ledger_record(first.ledger, "r6e-e10")
            assert record is not None
            assert record.state is ClientInvocationLedgerState.TERMINAL

            # Fault only the server-side correlation owner.  K1 remains a real
            # open WebSocket so its already-produced result can arrive late.
            assert await connections.realtime.disconnect(first.connection_id) == 1
            with pytest.raises(CapabilityError) as raised:
                await task
            assert raised.value.code == REMOTE_OUTCOME_UNKNOWN
            assert store.items["r6e-e10"].state is CapabilityInvocationState.WAITING

            second = await _connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(path),
                connection_id="r6e-e10-k2",
            )
            reconciled = await runtime.reconcile_remote_invocation(
                "r6e-e10",
                second.connection_id,
                timeout=5.0,
            )
            assert reconciled.status is RemoteReconciliationStatus.TERMINAL

            committed = await store.get("r6e-e10")
            assert committed is not None
            assert committed.state is CapabilityInvocationState.COMPLETED
            committed_revision = committed.revision
            committed_output = committed.output

            allow_send.set()
            await asyncio.sleep(0.2)

            after_late = await store.get("r6e-e10")
            assert after_late is not None
            assert after_late.state is CapabilityInvocationState.COMPLETED
            assert after_late.revision == committed_revision
            assert after_late.output == committed_output
            assert calls == ["r6e-e10"]
        finally:
            allow_send.set()
            if second is not None:
                await _close_generation(second, shutdown_dispatcher=True)
            if first is not None:
                await _close_generation(first, shutdown_dispatcher=True)
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_r6_e11_timeout_is_not_rollback_proof_and_late_terminal_cannot_resurrect(
    tmp_path,
):
    async def scenario():
        app, catalog, connections = _build_gateway_app()
        server, server_task, port = await _start_uvicorn(app)
        generation = None
        effects = []
        ledger = ClientInvocationLedger(tmp_path / "e11.sqlite3")

        def tool(value, cancel_event=None, **kwargs):
            # Deliberately ignore cancellation until after an external effect.
            time.sleep(0.10)
            effects.append(value)
            return {"value": value}

        try:
            generation = await _connect_client(
                port=port,
                registry=_client_registry(
                    tool,
                    idempotency=CapabilityIdempotency.UNKNOWN,
                ),
                ledger=ledger,
                connection_id="r6e-e11-k1",
            )
            runtime, store = _server_runtime(catalog, connections)

            with pytest.raises(CapabilityError) as raised:
                await _execute(
                    runtime,
                    invocation_id="r6e-e11",
                    connection_id=generation.connection_id,
                    timeout_seconds=0.02,
                )
            assert raised.value.code == "CAPABILITY_TIMEOUT"

            await _wait_until_async(
                lambda: (
                    (_ledger_record(ledger, "r6e-e11") is not None)
                    and (
                        _ledger_record(ledger, "r6e-e11").state
                        is ClientInvocationLedgerState.TERMINAL
                    )
                ),
                timeout=5.0,
            )
            assert effects == ["r6e-e11"]

            before = await store.get("r6e-e11")
            assert before is not None
            assert before.state is CapabilityInvocationState.TIMED_OUT
            assert before.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN

            with pytest.raises(CapabilityError) as reconciliation_error:
                await runtime.reconcile_remote_invocation(
                    "r6e-e11",
                    generation.connection_id,
                    timeout=5.0,
                )
            assert (
                reconciliation_error.value.code
                == REMOTE_RESULT_RECONCILIATION_REQUIRED
            )

            after = await store.get("r6e-e11")
            assert after is not None
            assert after.state is CapabilityInvocationState.TIMED_OUT
            assert after.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN
            assert after.revision == before.revision
            assert effects == ["r6e-e11"]
        finally:
            await _close_generation(
                generation,
                shutdown_dispatcher=True,
            )
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())
