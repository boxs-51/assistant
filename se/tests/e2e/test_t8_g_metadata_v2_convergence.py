from __future__ import annotations

import asyncio
import json
import shutil
import socket
import textwrap
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import (
    CapabilityRuntime as ClientCapabilityRuntime,
)
from cl.src.core.realtime_client import (
    GatewayRealtimeClient,
    RealtimeHandshakeError,
)
from cl.src.loader.local_tools import LocalToolManager
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementationState,
)
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.tool.registry import ToolRegistry
from se.src.transport.gateway.api.v1 import capability_router, events_router
from se.src.transport.gateway.authentication.dependency import (
    get_current_identity,
    get_websocket_identity,
)
from se.src.transport.gateway.dependencies import get_container


REPO_ROOT = Path(__file__).resolve().parents[3]
OWNER_ID = "t8-g-user"
SESSION_ID = "t8-g-session"
CLIENT_ID = "t8-g-client"
LOGICAL_ID = "logical.echo"
WEB_IDS = {"web.search", "web.read", "web.read_many"}


def _identity() -> Identity:
    return Identity(
        user_id=OWNER_ID,
        session_id=SESSION_ID,
        auth_type="jwt",
    )


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_until_async(predicate, timeout=10.0, interval=0.01) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for T8-G E2E condition.")


def _build_gateway_app():
    catalog = CapabilityCatalog()
    registration_service = ClientCapabilityRegistrationService(
        catalog,
        None,
    )
    connection_runtime = ConnectionRuntime(registration_service)
    registration_service.connections = connection_runtime.registry

    identity = _identity()
    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
        connection_runtime=connection_runtime,
        capability_runtime=None,
    )
    container.require = lambda key: getattr(container, key)

    app = FastAPI()
    app.include_router(events_router.router)
    app.include_router(capability_router.router)
    app.state.container = container
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_websocket_identity] = lambda: identity
    app.dependency_overrides[get_current_identity] = lambda: identity

    return app, catalog, connection_runtime, identity, container


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
        name="t8-g-e2e-gateway",
    )
    await _wait_until_async(
        lambda: server.started or task.done(),
        timeout=10.0,
    )
    if task.done():
        await task
        raise AssertionError("T8-G gateway exited before startup completed.")
    return server, task, port


async def _stop_uvicorn(server, task, timeout=5.0) -> None:
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


def _write_v2_package(
    root: Path,
    *,
    package_name: str,
    capability_id: str,
    bound_source: str,
    logical_description: str = "Synthetic canonical V2 echo",
) -> Path:
    package = root / package_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from tools.v1._shared.contracts import success_result, tool_result_schema

            TOOL_METADATA = {{
                "manifest_version": "2.0",
                "name": "{package_name}",
                "version": "9.0.0",
                "description": "Synthetic physical package for T8-G",
                "expose_root": False,
                "exports": [
                    {{
                        "id": "{capability_id}",
                        "version": "3.0",
                        "name": "{capability_id}",
                        "description": "{logical_description}",
                        "bind": {{"bound_source": "{bound_source}"}},
                        "input_schema": {{
                            "type": "object",
                            "properties": {{
                                "value": {{"type": "string"}},
                            }},
                            "required": ["value"],
                            "additionalProperties": False,
                        }},
                        "output_schema": tool_result_schema({{}}),
                        "kind": "TOOL",
                        "execution_mode": "ONE_SHOT",
                        "idempotency": "IDEMPOTENT",
                        "effects": ["READ"],
                        "base_risk": "LOW",
                        "required_scopes": [],
                        "required_permissions": [],
                        "danger_patterns": [],
                    }}
                ],
            }}

            def run(bound_source, value, **kwargs):
                return success_result(
                    tool=TOOL_METADATA["name"],
                    action="echo",
                    version=TOOL_METADATA["version"],
                    data={{
                        "source": bound_source,
                        "value": value,
                        "connection_id": kwargs.get("connection_id"),
                    }},
                )
            """
        ),
        encoding="utf-8",
    )
    return package / "__init__.py"


def _load_client_tools(root: Path, *capability_ids: str):
    manager = LocalToolManager(root, set())
    loaded = manager.load_tools(
        {
            "allowed_local_tools": ["*"],
            "blocked_local_tools": [],
            "enabled_v2_capabilities": list(capability_ids),
        }
    )
    assert set(loaded) == set(capability_ids)
    return loaded


def _build_server_runtime(catalog, connection_runtime) -> CapabilityRuntime:
    return CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(
            connection_availability=connection_runtime.registry,
        ),
        connection_registry=connection_runtime.registry,
        realtime=connection_runtime.realtime,
    )


async def _connect_client(
    base_url: str,
    tools,
    *,
    connection_id: str,
):
    registry = SimpleNamespace(tools=tools)
    dispatcher = CapabilityDispatcher(registry, None)
    client_runtime = None
    realtime = GatewayRealtimeClient(
        base_url,
        {"Authorization": "Bearer t8-g-e2e"},
        connection_id=connection_id,
        session_id=SESSION_ID,
        client_id=CLIENT_ID,
        heartbeat_interval=60.0,
        on_message=lambda envelope: client_runtime.handle_message(envelope),
    )
    dispatcher.set_realtime(realtime)
    client_runtime = ClientCapabilityRuntime(
        registry,
        realtime,
        client_id=CLIENT_ID,
        owner_id=OWNER_ID,
        dispatcher=dispatcher,
    )
    registered = await asyncio.to_thread(
        realtime.connect,
        wait_timeout=5.0,
    )
    assert registered["type"] == "connection.registered"
    assert registered["payload"]["state"] == "ACTIVE"
    return realtime, dispatcher, client_runtime


async def _register_client(client_runtime):
    registered = await asyncio.to_thread(
        client_runtime.register,
        timeout=5.0,
    )
    assert registered["type"] == "capability.registered"
    assert registered["payload"]["capabilities"]
    assert client_runtime.realtime.capabilities_registered is True
    return registered


@pytest.mark.asyncio
async def test_g1_g2_real_web_is_server_only_and_preserves_physical_toolresult(
    tmp_path,
):
    # G1: isolate the real Web package so unrelated V1 optional dependencies
    # cannot make this convergence gate environment-dependent.
    server_tools = tmp_path / "server_tools"
    server_tools.mkdir()
    shutil.copytree(
        REPO_ROOT / "tools" / "v1" / "web_tool",
        server_tools / "web_tool",
    )

    catalog = CapabilityCatalog()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(),
    )
    tool_registry = ToolRegistry(runtime.registry)

    registered = register_local_tools(runtime, tool_registry, server_tools)
    assert {
        capability_id
        for capability_id, state in registered.items()
        if state == "registered"
    } == WEB_IDS

    for capability_id in WEB_IDS:
        implementations = catalog.list_implementations(capability_id)
        assert [item.implementation_id for item in implementations] == [
            f"server:{capability_id}"
        ]
        assert implementations[0].location is CapabilityExecutionLocation.SERVER
        assert implementations[0].metadata["physical_tool"] == "web_tool"

    # A whitespace-only query passes the public JSON schema minLength but is
    # rejected by the T6 handler before DNS/HTTP. This proves the complete
    # logical -> SERVER driver -> physical ToolResult path without networking.
    invocation_id = "t8-g-web-search"
    result = await runtime.execute_capability(
        "web.search",
        {"query": "   "},
        _identity(),
        invocation_id=invocation_id,
        session_id=SESSION_ID,
    )
    assert result.capability_id == "web.search"
    assert result.output["ok"] is False
    assert result.output["tool"] == "web_tool"
    assert result.output["action"] == "search"
    assert result.output["error"]["code"] == "INVALID_ARGUMENT"

    stored = await runtime.invocation_lifecycle.store.get(invocation_id)
    assert stored is not None
    assert stored.capability_id == "web.search"
    assert stored.implementation_id == "server:web.search"

    # G2: the repository default policy must keep the *real* Web V2 package
    # out of the CLIENT registry and therefore out of capability.register.
    settings = json.loads(
        (REPO_ROOT / "cl" / "config" / "setting.json").read_text(
            encoding="utf-8"
        )
    )
    tools_config = settings["tools_config"]
    enabled_v2 = tools_config["enabled_v2_capabilities"]
    assert "*" not in enabled_v2
    assert not any(
        capability_id.startswith("web.")
        for capability_id in enabled_v2
    )

    manager = LocalToolManager(REPO_ROOT / "tools" / "v1", set())
    web_entry = REPO_ROOT / "tools" / "v1" / "web_tool" / "__init__.py"
    module = manager._load_entrypoint(web_entry, is_package=True)
    assert module is not None
    enabled = manager._enabled_v2_capabilities(tools_config)
    client_web_entries = manager._canonical_v2_entries(
        metadata=module.TOOL_METADATA,
        handler=module.run,
        enabled=enabled,
        fpath=web_entry,
    )
    assert client_web_entries == {}

    registration_runtime = ClientCapabilityRuntime(
        SimpleNamespace(tools=client_web_entries),
        SimpleNamespace(connection_id="t8-g-default-web"),
        client_id=CLIENT_ID,
        owner_id=OWNER_ID,
        dispatcher=SimpleNamespace(),
    )
    payload = registration_runtime.build_registration()
    advertised = {
        item["definition"]["id"] for item in payload["capabilities"]
    }
    assert WEB_IDS.isdisjoint(advertised)


@pytest.mark.parametrize("order", ["server-first", "client-first"])
def test_g3_g4_g8_v2_hybrid_converges_in_both_orders_and_routes_without_rewrite(
    tmp_path,
    order,
):
    async def scenario():
        app, catalog, connection_runtime, identity, container = (
            _build_gateway_app()
        )
        server, server_task, port = await _start_uvicorn(app)

        client_root = tmp_path / f"{order}-client"
        server_root = tmp_path / f"{order}-server"
        _write_v2_package(
            client_root,
            package_name="client_bundle",
            capability_id=LOGICAL_ID,
            bound_source="client",
        )
        _write_v2_package(
            server_root,
            package_name="server_bundle",
            capability_id=LOGICAL_ID,
            bound_source="server",
        )
        client_tools = _load_client_tools(client_root, LOGICAL_ID)

        gateway_runtime = _build_server_runtime(
            catalog,
            connection_runtime,
        )
        container.capability_runtime = gateway_runtime
        server_tool_registry = ToolRegistry(gateway_runtime.registry)

        realtime = None
        dispatcher = None
        try:
            if order == "server-first":
                server_result = register_local_tools(
                    gateway_runtime,
                    server_tool_registry,
                    server_root,
                )
                assert server_result == {LOGICAL_ID: "registered"}
                canonical_before = catalog.get_definition(
                    LOGICAL_ID
                ).model_dump(mode="json")

            realtime, dispatcher, client_runtime = await _connect_client(
                f"http://127.0.0.1:{port}",
                client_tools,
                connection_id=f"t8-g-{order}",
            )
            await _register_client(client_runtime)

            if order == "client-first":
                canonical_before = catalog.get_definition(
                    LOGICAL_ID
                ).model_dump(mode="json")
                server_result = register_local_tools(
                    gateway_runtime,
                    server_tool_registry,
                    server_root,
                )
                assert server_result == {LOGICAL_ID: "registered"}

            canonical_after = catalog.get_definition(
                LOGICAL_ID
            ).model_dump(mode="json")
            assert canonical_after == canonical_before
            assert canonical_after["metadata"] == {
                "base_risk": "LOW",
                "required_permissions": [],
                "danger_patterns": [],
            }

            implementations = catalog.list_implementations(LOGICAL_ID)
            assert {
                item.location for item in implementations
            } == {
                CapabilityExecutionLocation.SERVER,
                CapabilityExecutionLocation.CLIENT,
            }
            assert {
                item.implementation_id for item in implementations
            } == {
                f"server:{LOGICAL_ID}",
                f"t8-g-{order}:{LOGICAL_ID}",
            }

            # G4 routing fence: same-connection CLIENT remains first priority.
            client_result = await gateway_runtime.execute_capability(
                LOGICAL_ID,
                {"value": order},
                identity,
                invocation_id=f"inv-{order}-client",
                session_id=SESSION_ID,
                connection_id=f"t8-g-{order}",
            )
            assert client_result.output["tool"] == "client_bundle"
            assert client_result.output["data"] == {
                "source": "client",
                "value": order,
                "connection_id": f"t8-g-{order}",
            }

            # Without a connection binding the CLIENT implementation is not
            # authorized, so the existing SERVER sibling remains routable.
            server_result = await gateway_runtime.execute_capability(
                LOGICAL_ID,
                {"value": order},
                identity,
                invocation_id=f"inv-{order}-server",
                session_id=SESSION_ID,
            )
            assert server_result.output["tool"] == "server_bundle"
            assert server_result.output["data"]["source"] == "server"
            assert server_result.output["data"]["value"] == order

            # G8: exercise the existing HTTP list/get control plane. The
            # logical definition stays canonical while both implementations
            # are introspectable.
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=5.0,
            ) as http:
                detail_response = await http.get(
                    f"/v1/capabilities/{LOGICAL_ID}",
                    headers={"Authorization": "Bearer t8-g-e2e"},
                )
                assert detail_response.status_code == 200
                detail = detail_response.json()
                assert detail["capability_id"] == LOGICAL_ID
                assert detail["definition"]["id"] == LOGICAL_ID
                assert detail["definition"]["version"] == "3.0"
                assert detail["definition"]["metadata"] == {
                    "base_risk": "LOW",
                    "required_permissions": [],
                    "danger_patterns": [],
                }
                assert {
                    item["implementation_id"]
                    for item in detail["implementations"]
                } == {
                    f"server:{LOGICAL_ID}",
                    f"t8-g-{order}:{LOGICAL_ID}",
                }

                list_response = await http.get(
                    "/v1/capabilities/",
                    headers={"Authorization": "Bearer t8-g-e2e"},
                )
                assert list_response.status_code == 200
                entries = {
                    item["capability_id"]: item
                    for item in list_response.json()
                }
                assert LOGICAL_ID in entries
                assert entries[LOGICAL_ID]["definition"] == detail["definition"]
        finally:
            if realtime is not None:
                await asyncio.to_thread(realtime.close)
            if dispatcher is not None:
                dispatcher.shutdown()
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_g5_realtime_divergent_batch_rolls_back_atomically(tmp_path):
    async def scenario():
        app, catalog, connection_runtime, _identity_value, container = (
            _build_gateway_app()
        )
        gateway_runtime = _build_server_runtime(
            catalog,
            connection_runtime,
        )
        container.capability_runtime = gateway_runtime

        server_root = tmp_path / "g5-server"
        _write_v2_package(
            server_root,
            package_name="server_bundle",
            capability_id=LOGICAL_ID,
            bound_source="server",
        )
        server_result = register_local_tools(
            gateway_runtime,
            ToolRegistry(gateway_runtime.registry),
            server_root,
        )
        assert server_result == {LOGICAL_ID: "registered"}
        canonical_before = catalog.get_definition(
            LOGICAL_ID
        ).model_dump(mode="json")

        server, server_task, port = await _start_uvicorn(app)

        client_root = tmp_path / "g5-client"
        _write_v2_package(
            client_root,
            package_name="aaa_new",
            capability_id="logical.new",
            bound_source="client-new",
            logical_description="Synthetic new canonical V2 capability",
        )
        _write_v2_package(
            client_root,
            package_name="bbb_shared",
            capability_id=LOGICAL_ID,
            bound_source="client-shared",
        )
        client_tools = _load_client_tools(
            client_root,
            "logical.new",
            LOGICAL_ID,
        )

        realtime = None
        dispatcher = None
        try:
            realtime, dispatcher, client_runtime = await _connect_client(
                f"http://127.0.0.1:{port}",
                client_tools,
                connection_id="t8-g-g5",
            )
            payload = client_runtime.build_registration()
            assert [
                item["definition"]["id"]
                for item in payload["capabilities"]
            ] == ["logical.new", LOGICAL_ID]

            # First entry is valid and absent. Second conflicts with the
            # existing canonical definition. The whole batch must remain
            # mutation-free when driven through the real WebSocket boundary.
            payload["capabilities"][1]["definition"]["description"] = (
                "DIVERGENT T8-G CONTRACT"
            )
            realtime.send("capability.register", payload)
            with pytest.raises(
                RealtimeHandshakeError,
                match="different contract",
            ):
                await asyncio.to_thread(
                    realtime.wait_capabilities_registered,
                    5.0,
                )

            assert not catalog.contains_definition("logical.new")
            assert not catalog.contains_implementation(
                "t8-g-g5:logical.new"
            )
            assert not catalog.contains_implementation(
                f"t8-g-g5:{LOGICAL_ID}"
            )
            assert catalog.get_definition(
                LOGICAL_ID
            ).model_dump(mode="json") == canonical_before
            assert {
                item.implementation_id
                for item in catalog.list_implementations(LOGICAL_ID)
            } == {f"server:{LOGICAL_ID}"}
        finally:
            if realtime is not None:
                await asyncio.to_thread(realtime.close)
            if dispatcher is not None:
                dispatcher.shutdown()
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())


def test_g6_v2_reconnect_reuses_definition_and_rebinds_implementation(tmp_path):
    async def scenario():
        app, catalog, connection_runtime, _identity_value, _container = (
            _build_gateway_app()
        )
        server, server_task, port = await _start_uvicorn(app)

        client_root = tmp_path / "g6-client"
        _write_v2_package(
            client_root,
            package_name="client_bundle",
            capability_id=LOGICAL_ID,
            bound_source="client",
        )
        client_tools = _load_client_tools(client_root, LOGICAL_ID)

        first_realtime = None
        first_dispatcher = None
        second_realtime = None
        second_dispatcher = None
        try:
            first_realtime, first_dispatcher, first_runtime = (
                await _connect_client(
                    f"http://127.0.0.1:{port}",
                    client_tools,
                    connection_id="t8-g-reconnect-1",
                )
            )
            first_ack = await _register_client(first_runtime)
            assert first_ack["connection_id"] == "t8-g-reconnect-1"
            canonical_before = catalog.get_definition(
                LOGICAL_ID
            ).model_dump(mode="json")

            old_implementation_id = (
                f"t8-g-reconnect-1:{LOGICAL_ID}"
            )
            assert catalog.get_implementation(
                old_implementation_id
            ).state is CapabilityImplementationState.ENABLED

            await asyncio.to_thread(first_realtime.close)
            assert first_realtime.capabilities_registered is False
            await _wait_until_async(
                lambda: (
                    catalog.get_implementation(
                        old_implementation_id
                    ).state
                    is CapabilityImplementationState.REMOVED
                ),
                timeout=5.0,
            )

            second_realtime, second_dispatcher, second_runtime = (
                await _connect_client(
                    f"http://127.0.0.1:{port}",
                    client_tools,
                    connection_id="t8-g-reconnect-2",
                )
            )
            second_ack = await _register_client(second_runtime)
            assert second_ack["connection_id"] == "t8-g-reconnect-2"
            assert second_realtime.capabilities_registered is True

            assert catalog.get_definition(
                LOGICAL_ID
            ).model_dump(mode="json") == canonical_before
            assert catalog.get_implementation(
                old_implementation_id
            ).state is CapabilityImplementationState.REMOVED
            new_implementation = catalog.get_implementation(
                f"t8-g-reconnect-2:{LOGICAL_ID}"
            )
            assert new_implementation.state is CapabilityImplementationState.ENABLED
            assert new_implementation.connection_id == "t8-g-reconnect-2"
        finally:
            if second_realtime is not None:
                await asyncio.to_thread(second_realtime.close)
            if second_dispatcher is not None:
                second_dispatcher.shutdown()
            if first_realtime is not None:
                await asyncio.to_thread(first_realtime.close)
            if first_dispatcher is not None:
                first_dispatcher.shutdown()
            await _stop_uvicorn(server, server_task)

    asyncio.run(scenario())
