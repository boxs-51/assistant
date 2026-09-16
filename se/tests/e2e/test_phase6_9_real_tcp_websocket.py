from __future__ import annotations

import socket
import threading
import time
import asyncio
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.runtime import ConnectionRuntime
from se.src.transport.gateway.api.v1 import events_router
from se.src.transport.gateway.authentication.dependency import (
    get_websocket_identity,
)
from se.src.transport.gateway.dependencies import get_container
from se.src.runtimes.agent.adapters import CapabilityToolExecutionAdapter
from se.src.domain.schemas import AgentExecutionLimits

from cl.src.core.client_runtime import ClientRuntime


CONNECTION_ID = "e2e-connection-01"
SESSION_ID = "e2e-session-01"
CLIENT_ID = "e2e-client-01"
OWNER_ID = "e2e-owner-01"
CAPABILITY_ID = "desktop.echo"


class AllowToolPolicy:
    def is_visible(self, *, agent_id, capability_id):
        return True

    def authorize(self, *, identity, agent_id, capability_id):
        return PolicyDecision.ALLOW


class AllowExecutionPolicy:
    def check_start(self, context):
        return PolicyDecision.ALLOW

    def check_iteration(self, context, iteration):
        return PolicyDecision.ALLOW

    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


class DeterministicInference:
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
                            "id": "tool-call-1",
                            "name": CAPABILITY_ID,
                            "arguments": {
                                "value": "hello",
                            },
                        }
                    ],
                ),
                finish_reason="tool_calls",
                usage=InferenceUsage(),
                provider="e2e",
                model="e2e",
                metadata={},
            )

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
            provider="e2e",
            model="e2e",
            metadata={},
        )


class DeterministicContextBuilder:
    async def build(self, context, request):
        class Snapshot:
            messages = list(request.prior_messages)

            tools = [
                {
                    "name": CAPABILITY_ID,
                    "description": "E2E client echo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                            }
                        },
                        "required": ["value"],
                    },
                }
            ]

            metadata = {
                "connection_id": context.connection_id,
            }

        return Snapshot()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)

    raise AssertionError("E2E condition timed out.")


def _build_registry(executed):
    def desktop_echo(value, **kwargs):
        executed.append(
            {
                "value": value,
                "connection_id": kwargs.get("connection_id"),
                "session_id": kwargs.get("session_id"),
            }
        )

        return {
            "echo": value,
            "connection_id": kwargs.get("connection_id"),
        }

    return SimpleNamespace(
        tools={
            CAPABILITY_ID: {
                "func": desktop_echo,
                "metadata": {
                    "name": CAPABILITY_ID,
                    "description": "E2E client echo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                            }
                        },
                        "required": ["value"],
                    },
                },
            }
        }
    )


def _build_gateway():
    catalog = CapabilityCatalog()

    connection_runtime = ConnectionRuntime(
        ClientCapabilityRegistrationService(
            catalog,
            None,
        )
    )

    connection_runtime.registration_service.connections = (
        connection_runtime.registry
    )

    identity = Identity(
        user_id=OWNER_ID,
        session_id=SESSION_ID,
        auth_type="jwt",
    )

    container = SimpleNamespace(
        connection_runtime=connection_runtime,
        eventing_manager=SimpleNamespace(
            ws_manager=SimpleNamespace(),
        ),
    )

    app = FastAPI()
    app.include_router(events_router.router)

    app.dependency_overrides[get_container] = (
        lambda: container
    )

    app.dependency_overrides[get_websocket_identity] = (
        lambda: identity
    )

    return app, catalog, connection_runtime, identity


def _start_gateway(app):
    port = _free_port()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )

    thread = threading.Thread(
        target=server.run,
        daemon=True,
    )
    thread.start()

    _wait_until(lambda: server.started)

    return server, thread, port


@pytest.mark.e2e
def test_phase6_9_real_tcp_websocket_agent_client_loop():
    """
    P0-1A:

        AgentRuntime
          -> CapabilityRuntime
          -> RemoteClientDriver
          -> REAL TCP WebSocket
          -> ClientRuntime receiver
          -> CapabilityDispatcher
          -> real Python capability
          -> REAL TCP WebSocket
          -> RealtimeMultiplexer
          -> AgentRuntime
          -> second inference

    Forbidden:
        FakeSocket
        realtime.handle_inbound()
        dispatcher.dispatch()
        manual capability.result
    """

    app, catalog, connection_runtime, identity = _build_gateway()
    server, thread, port = _start_gateway(app)

    executed = []
    registry = _build_registry(executed)

    client = ClientRuntime(
        f"http://127.0.0.1:{port}",
        registry,
        api_key="phase6-e2e",
        client_id=CLIENT_ID,
        owner_id=OWNER_ID,
    )

    try:
        client.start()

        _wait_until(
            lambda: catalog.contains_implementation(
                f"{CONNECTION_ID}:{CAPABILITY_ID}"
            )
        )

        implementation = catalog.get_implementation(
            f"{CONNECTION_ID}:{CAPABILITY_ID}"
        )

        assert implementation.connection_id == client.connection_id

        gateway_registry = CapabilityRegistry()

        definition = CapabilityDefinition(
            id=CAPABILITY_ID,
            name=CAPABILITY_ID,
            description="E2E client echo",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
        )

        gateway_registry.register_definition(definition)

        class RegistryGateDriver:
            async def execute(self, context, arguments):
                raise AssertionError(
                    "SERVER driver executed instead of client driver."
                )

        gateway_registry.register_capability(
            RegistryGateDriver()
        )

        capability_runtime = CapabilityRuntime(
            registry=gateway_registry,
            catalog=catalog,
            routing_policy=CapabilityRoutingPolicy(
                connection_availability=(
                    connection_runtime.registry
                )
            ),
            connection_registry=connection_runtime.registry,
            realtime=connection_runtime.realtime,
        )

        agent = AgentRuntime(
            context_builder=DeterministicContextBuilder(),
            inference=DeterministicInference(),
            tool_execution=CapabilityToolExecutionAdapter(
                capability_runtime,
                AllowToolPolicy(),
                AllowExecutionPolicy(),
            ),
            execution_policy=AllowExecutionPolicy(),
        )

        context = AgentExecutionContext.create(
            execution_id="exec-e2e-1",
            agent_id="agent-e2e",
            session_id=SESSION_ID,
            correlation_id="corr-e2e-1",
            identity=identity,
            limits=AgentExecutionLimits(
                max_iterations=4,
                max_tool_calls=4,
            ),
            connection_id=client.connection_id,
        )

        result = asyncio.run(agent.execute(context))

        assert result.state.value == "COMPLETED"
        assert result.output == "done"

        assert executed == [
            {
                "value": "hello",
                "connection_id": client.connection_id,
                "session_id": SESSION_ID,
            }
        ]

    finally:
        client.stop()

        server.should_exit = True
        thread.join(timeout=5)