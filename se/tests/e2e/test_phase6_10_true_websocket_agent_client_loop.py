from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI

from se.src.application.policy.authorization import AuthorizationService
from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from se.src.runtimes.agent.adapters.tool import (
    CapabilityToolExecutionAdapter,
)
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.capabilities import RegistryAgentCapabilityResolver
from se.src.runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import (
    get_websocket_identity
)
from se.src.transport.gateway.dependencies import get_container

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import (
    CapabilityRuntime as ClientCapabilityRuntime,
)
from cl.src.core.realtime_client import GatewayRealtimeClient


CONNECTION_ID = "e2e-client-01"
SESSION_ID = "e2e-session-01"
CLIENT_ID = "e2e-desktop-01"
OWNER_ID = "e2e-user-01"
CAPABILITY_ID = "desktop.echo"


class AllowExecutionPolicy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW

    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


class DeterministicInference:
    """
    Deterministic model double.

    The inference implementation is intentionally fake because this test is
    about the REAL Gateway <-> Client WebSocket/tool execution path.
    """

    def __init__(self):
        self.calls = 0
        self.requests = []

    async def complete(self, request):
        self.calls += 1
        self.requests.append(request)

        if self.calls == 1:
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=InferenceMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "call-e2e-1",
                            "name": CAPABILITY_ID,
                            "arguments": {
                                "value": "hello-from-agent",
                            },
                        }
                    ],
                ),
                finish_reason="tool_calls",
                usage=InferenceUsage(),
                provider="e2e-test",
                model=request.model or "e2e-test",
                metadata={},
            )

        assert self.calls == 2

        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                content="done",
                tool_calls=[],
            ),
            finish_reason="stop",
            usage=InferenceUsage(),
            provider="e2e-test",
            model=request.model or "e2e-test",
            metadata={},
        )


class E2EContextRuntime:
    async def load_context(self, session_id, identity):
        session = type(
            "Session",
            (),
            {"messages": [InferenceMessage(role="user", content="Run echo")]},
        )()
        return type("Loaded", (), {"session": session})()


def _free_tcp_port() -> int:
    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)

    raise AssertionError(
        "Timed out waiting for E2E condition."
    )


def _build_gateway_app():
    catalog = CapabilityCatalog()

    connection_runtime = ConnectionRuntime(
        ClientCapabilityRegistrationService(
            catalog,
            None,
        ),
    )

    connection_runtime.registration_service.connections = (
        connection_runtime.registry
    )

    container = SimpleNamespace(
        eventing_manager=SimpleNamespace(
            ws_manager=WebSocketConnectionManager(),
        ),
        connection_runtime=connection_runtime,
    )


    identity = Identity(
        user_id=OWNER_ID,
        session_id=SESSION_ID,
        auth_type="jwt",
    )

    app = FastAPI()
    app.include_router(events_router.router)

    container.require = lambda key: getattr(container, key)
    app.state.container = container
    app.dependency_overrides[get_container] = (
        lambda: container
    )
    app.dependency_overrides[get_websocket_identity] = lambda: identity
    
    return (
        app,
        catalog,
        connection_runtime,
        identity,
    )


def _build_client_registry():
    """
    Real client registry shape consumed by CapabilityDispatcher.

    No fake transport is used. The function below is an actual Python tool
    executed by the client's ThreadPoolExecutor.
    """

    executed = []

    def desktop_echo(value: str, **kwargs):
        executed.append(
            {
                "value": value,
                "connection_id": kwargs.get(
                    "connection_id"
                ),
                "session_id": kwargs.get(
                    "session_id"
                ),
            }
        )

        return {
            "echo": value,
            "executed_on": "client",
            "connection_id": kwargs.get(
                "connection_id"
            ),
        }

    registry = SimpleNamespace(
        tools={
            CAPABILITY_ID: {
                "func": desktop_echo,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "description": (
                        "Real client-side E2E echo tool"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                            },
                        },
                        "required": ["value"],
                    },
                },
            }
        }
    )

    return registry, executed


def _start_uvicorn(app):
    port = _free_tcp_port()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )

    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run,
        name="phase6-10-e2e-gateway",
        daemon=True,
    )
    thread.start()

    _wait_until(
        lambda: server.started,
        timeout=10.0,
    )

    return server, thread, port


def test_phase6_10_1_true_websocket_remote_agent_tool_loop():
    """
    Phase 6.10.1 P0 E2E invariant:

        AgentRuntime
          -> ToolExecutionPort
          -> CapabilityRuntime
          -> RemoteClientDriver
          -> REAL TCP WebSocket
          -> REAL client receiver thread
          -> CapabilityRuntime
          -> REAL CapabilityDispatcher
          -> REAL Python tool
          -> capability.result
          -> REAL TCP WebSocket
          -> RealtimeMultiplexer
          -> AgentRuntime
          -> SECOND inference
          -> COMPLETED

    Explicitly forbidden:
        - FakeSocket
        - realtime.handle_inbound() from the test
        - manually injected capability.result
        - directly invoking CapabilityDispatcher from the test
    """

    async def scenario():
        (
            gateway_app,
            catalog,
            connection_runtime,
            identity,
        ) = _build_gateway_app()

        server, server_thread, port = _start_uvicorn(
            gateway_app
        )

        client_registry, executed_tools = (
            _build_client_registry()
        )

        client_realtime = None
        client_dispatcher = None
        client_capabilities = None

        try:
            client_dispatcher = CapabilityDispatcher(
                client_registry,
                None,
            )

            client_realtime = GatewayRealtimeClient(
                f"http://127.0.0.1:{port}",
                {
                    "Authorization": "Bearer phase6-e2e",
                },
                connection_id=CONNECTION_ID,
                session_id=SESSION_ID,
                client_id=CLIENT_ID,
                heartbeat_interval=60.0,
                on_message=lambda envelope: (
                    client_capabilities.handle_message(envelope)
                ),
            )

            # The dispatcher must use the SAME real realtime transport.
            client_dispatcher.realtime = (
                client_realtime
            )

            client_capabilities = ClientCapabilityRuntime(
                client_registry,
                client_realtime,
                client_id=CLIENT_ID,
                owner_id=OWNER_ID,
                dispatcher=client_dispatcher,
            )

            # ----------------------------------------------------------
            # REAL CLIENT -> REAL GATEWAY connection.register
            # ----------------------------------------------------------
            registered = client_realtime.connect(
                wait_timeout=5.0,
            )

            assert registered["type"] == (
                "connection.registered"
            )
            assert registered["connection_id"] == (
                CONNECTION_ID
            )
            assert registered["payload"]["state"] == (
                "ACTIVE"
            )

            # ----------------------------------------------------------
            # REAL CLIENT -> REAL GATEWAY capability.register
            # ----------------------------------------------------------
            registration = client_capabilities.register(
                timeout=5.0,
            )

            assert registration["type"] == (
                "capability.registered"
            )
            assert registration["connection_id"] == (
                CONNECTION_ID
            )
            assert registration["payload"]["capabilities"]

            # The gateway must now contain the implementation created
            # from the REAL client registration message.
            implementation_id = (
                f"{CONNECTION_ID}:{CAPABILITY_ID}"
            )

            _wait_until(
                lambda: (
                    catalog.get_implementation(
                        implementation_id
                    )
                    is not None
                ),
                timeout=5.0,
            )

            implementation = (
                catalog.get_implementation(
                    implementation_id
                )
            )

            assert implementation is not None
            assert implementation.connection_id == (
                CONNECTION_ID
            )
            assert implementation.state.value == (
                "ENABLED"
            )

            # ----------------------------------------------------------
            # Gateway-side AgentRuntime setup
            # ----------------------------------------------------------
            #
            # Deliberately keep the server registry empty. The registered
            # remote catalog implementation is independently executable.
            gateway_capability_registry = (
                CapabilityRegistry()
            )

            gateway_realtime = (
                connection_runtime.realtime
            )

            gateway_capability_runtime = CapabilityRuntime(
                registry=gateway_capability_registry,
                authorization=AuthorizationService(),
                catalog=catalog,
                routing_policy=CapabilityRoutingPolicy(
                    connection_availability=(
                        connection_runtime.registry
                    ),
                ),
                connection_registry=(
                    connection_runtime.registry
                ),
                realtime=gateway_realtime,
            )

            inference = DeterministicInference()

            agent = AgentDefinition(
                name="phase6-10-e2e-agent",
                goal="Execute a client-side tool.",
                instruction=(
                    "Use desktop.echo when required."
                ),
                tools=[CAPABILITY_ID],
            )
            agent_registry = AgentRegistry()
            agent_registry.register(agent)
            tool_policy = RegistryAgentToolPolicy(
                agent_registry,
                gateway_capability_registry,
                AuthorizationService(),
                capability_catalog=catalog,
            )
            context_assembler = DefaultAgentContextAssembler(
                DefaultAgentSystemPromptProvider(),
                RegistryAgentCapabilityResolver(
                    agent_registry=agent_registry,
                    capability_registry=gateway_capability_registry,
                    capability_catalog=catalog,
                    tool_policy=tool_policy,
                ),
            )
            context_builder = ContextBuilderAdapter(
                E2EContextRuntime(),
                gateway_capability_runtime,
                tool_policy,
                context_assembler=context_assembler,
            )
            tool_port = CapabilityToolExecutionAdapter(
                gateway_capability_runtime,
                tool_policy,
                AllowExecutionPolicy(),
            )

            context = AgentExecutionContext.create(
                execution_id="e2e-execution-01",
                agent_id="phase6-10-e2e-agent",
                session_id=SESSION_ID,
                correlation_id="e2e-correlation-01",
                identity=identity,
                limits=AgentExecutionLimits(
                    max_iterations=3,
                ),
                connection_id=CONNECTION_ID,
                agent=agent,
                metadata={"constitution": "Report remote results honestly."},
            )

            runtime = AgentRuntime(
                context_builder=context_builder,
                inference=inference,
                tool_execution=tool_port,
                execution_policy=(
                    AllowExecutionPolicy()
                ),
            )

            # ----------------------------------------------------------
            # REAL E2E AGENT EXECUTION
            # ----------------------------------------------------------
            #
            # No fake socket.
            # No handle_inbound().
            # No manually injected result.
            #
            # The call below must block until the REAL client receives
            # capability.invoke and sends capability.result over TCP.
            execution = await runtime.execute(
                context
            )

            # ----------------------------------------------------------
            # Assertions: REAL client tool execution
            # ----------------------------------------------------------
            assert executed_tools == [
                {
                    "value": "hello-from-agent",
                    "connection_id": CONNECTION_ID,
                    "session_id": SESSION_ID,
                }
            ]

            # The guard driver must never execute.
            assert catalog.get_implementation(
                implementation_id
            ).connection_id == CONNECTION_ID

            # ----------------------------------------------------------
            # Assertions: REAL Agent second inference
            # ----------------------------------------------------------
            assert inference.calls == 2
            assert len(inference.requests) == 2

            assert execution.output == "done"
            assert execution.state.value == "COMPLETED"
            assert context.iteration == 2

            first_request = inference.requests[0]
            assert first_request.messages[0].role == "system"
            assert sum(
                item.role == "system" for item in first_request.messages
            ) == 1
            system_content = str(first_request.messages[0].content)
            assert "AGENT CONSTITUTION" in system_content
            assert "phase6-10-e2e-agent" in system_content
            assert "Execute a client-side tool" in system_content
            assert "Use desktop.echo when required" in system_content
            assert [item.name for item in first_request.tools] == [CAPABILITY_ID]

            # The second inference must contain the tool result in the
            # reconstructed conversation.
            second_request = inference.requests[1]
            assert sum(
                item.role == "system" for item in second_request.messages
            ) == 1
            # Temporal context is intentionally rebuilt for every inference;
            # the stable agent instructions remain while current time advances.
            assert second_request.messages[0].content != first_request.messages[0].content
            assert "AGENT CONSTITUTION" in str(second_request.messages[0].content)
            assert "Use desktop.echo when required" in str(second_request.messages[0].content)
            assert second_request.messages[-1].role == "tool"
            serialized = repr(
                second_request.messages
            )

            assert "hello-from-agent" in serialized
            assert CAPABILITY_ID in serialized

        finally:
            if client_realtime is not None:
                client_realtime.close()

            if client_dispatcher is not None:
                client_dispatcher.shutdown()

            server.should_exit = True
            server_thread.join(timeout=5.0)

            assert not server_thread.is_alive(), (
                "Gateway E2E server did not shut down."
            )

    asyncio.run(scenario())


def test_real_websocket_disconnect_falls_back_to_server_same_invocation():
    """A real socket loss creates attempt 2 without changing invocation ID."""
    from se.src.runtimes.capability.contracts.implementation import (
        CapabilityExecutionLocation,
        CapabilityImplementation,
        CapabilityImplementationState,
    )
    from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
    from se.src.runtimes.capability.invocation import (
        CapabilityInvocationLifecycle,
        InMemoryCapabilityInvocationStore,
    )

    async def scenario():
        gateway_app, catalog, connection_runtime, identity = _build_gateway_app()
        server, server_thread, port = _start_uvicorn(gateway_app)
        started = threading.Event()
        release = threading.Event()

        def slow_client_tool(value: str, **kwargs):
            started.set()
            release.wait(timeout=5.0)
            return {"source": "client", "value": value}

        client_registry = SimpleNamespace(
            tools={
                CAPABILITY_ID: {
                    "func": slow_client_tool,
                    "metadata": {
                        "name": CAPABILITY_ID,
                        "description": "Disconnecting client tool",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    },
                }
            }
        )
        dispatcher = CapabilityDispatcher(client_registry, None)
        client_realtime = None
        try:
            client_capabilities = None
            client_realtime = GatewayRealtimeClient(
                f"http://127.0.0.1:{port}",
                {"Authorization": "Bearer phase7-fallback"},
                connection_id=CONNECTION_ID,
                session_id=SESSION_ID,
                client_id=CLIENT_ID,
                heartbeat_interval=60.0,
                on_message=lambda envelope: client_capabilities.handle_message(envelope),
            )
            dispatcher.realtime = client_realtime
            client_capabilities = ClientCapabilityRuntime(
                client_registry,
                client_realtime,
                client_id=CLIENT_ID,
                owner_id=OWNER_ID,
                dispatcher=dispatcher,
            )
            client_realtime.connect(wait_timeout=5.0)
            client_capabilities.register(timeout=5.0)

            definition = catalog.get_definition(CAPABILITY_ID)
            server_implementation = CapabilityImplementation.from_definition(
                definition,
                implementation_id=f"server:{CAPABILITY_ID}",
                location=CapabilityExecutionLocation.SERVER,
                driver_kind="PYTHON",
            )
            catalog.register_implementation(server_implementation)
            catalog.transition_implementation(
                server_implementation.implementation_id,
                CapabilityImplementationState.ENABLED,
            )

            class ServerFallbackDriver(BaseCapabilityDriver):
                async def execute(self, context, arguments):
                    return {
                        "source": "server",
                        "value": arguments["value"],
                        "invocation_id": context.invocation_id,
                    }

            invocation_store = InMemoryCapabilityInvocationStore()
            runtime = CapabilityRuntime(
                catalog=catalog,
                routing_policy=CapabilityRoutingPolicy(
                    connection_availability=connection_runtime.registry
                ),
                connection_registry=connection_runtime.registry,
                realtime=connection_runtime.realtime,
                invocation_lifecycle=CapabilityInvocationLifecycle(
                    invocation_store
                ),
            )
            runtime.driver_registry.bind(
                server_implementation.implementation_id,
                ServerFallbackDriver(definition),
            )

            task = asyncio.create_task(
                runtime.execute_capability(
                    CAPABILITY_ID,
                    {"value": "fallback"},
                    identity,
                    invocation_id="inv-real-fallback",
                    session_id=SESSION_ID,
                    connection_id=CONNECTION_ID,
                    metadata={"max_attempts": 2},
                )
            )
            assert await asyncio.to_thread(started.wait, 5.0)
            await asyncio.to_thread(client_realtime.close)
            release.set()
            result = await asyncio.wait_for(task, timeout=10.0)

            assert result.invocation_id == "inv-real-fallback"
            assert result.output == {
                "source": "server",
                "value": "fallback",
                "invocation_id": "inv-real-fallback",
            }
            attempts = sorted(
                invocation_store.attempts.values(),
                key=lambda item: item.attempt_number,
            )
            assert [item.implementation_id for item in attempts] == [
                f"{CONNECTION_ID}:{CAPABILITY_ID}",
                f"server:{CAPABILITY_ID}",
            ]
            assert invocation_store.items["inv-real-fallback"].state.value == "COMPLETED"
        finally:
            release.set()
            if client_realtime is not None:
                client_realtime.close()
            dispatcher.shutdown()
            server.should_exit = True
            server_thread.join(timeout=10.0)
            assert not server_thread.is_alive()

    asyncio.run(scenario())
