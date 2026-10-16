"""SBX3 ING-03/04/07: true TCP/WebSocket Agent-to-Client ingress.

This deliberately tests a renamed, process-capable *real stdio MCP export*.
The provider is deterministic; the gateway, connection, catalog, remote
driver, client dispatcher and MCP process are real. No source remediation.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from cl.src.core.capability_runtime import CapabilityRuntime as ClientCapabilityRuntime
from cl.src.core.realtime_client import GatewayRealtimeClient, RealtimeHandshakeError
from cl.src.hitl.hitl_manager import HITLManager
from cl.src.mcp_client.mcp_adapter import MCPManager

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.capabilities import RegistryAgentCapabilityResolver
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage, InferenceResponse, InferenceUsage,
)
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.context.temporal import TemporalContextProvider

from se.tests.e2e import test_phase6_10_true_websocket_agent_client_loop as base


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "cl" / "tests" / "fixtures" / "mcp_stdio_sbx3_ingress_server.py"
)
BAD = "mcp__ingress__innocent_preview"
ECHO = "mcp__ingress__safe_echo"
REPLAY = "mcp__ingress__replay_probe"


class _TracingHITL(HITLManager):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.set_approval_callback(lambda _: True)

    def request_approval(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return super().request_approval(*args, **kwargs)


class _Model:
    """Deterministic inference ONLY; no fake transport or registry."""

    def __init__(self, capability_id):
        self.name = capability_id
        self.calls = 0
        self.requests = []

    async def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            message = InferenceMessage(
                role="assistant",
                content="",
                tool_calls=[{
                    "id": "ing-model-tool-01",
                    "name": self.name,
                    "arguments": {"value": "real-tcp"},
                }],
            )
            finish = "tool_calls"
        else:
            message = InferenceMessage(
                role="assistant", content="done", tool_calls=[]
            )
            finish = "stop"
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            finish_reason=finish,
            usage=InferenceUsage(),
            provider="sbx3-deterministic-not-external-llm",
            model=request.model or "sbx3-test",
            metadata={},
        )


def _real_mcp_tools(marker):
    manager = MCPManager(
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=10.0,
    )
    # stdout glyph is not encodable by Windows cp1252; capture only fixture status.
    with contextlib.redirect_stdout(io.StringIO()):
        tools = manager.load_mcp_servers({
            "ingress": {
                "enabled": True,
                "command": sys.executable,
                "args": ["-E", str(FIXTURE)],
                "base_risk": "LOW",
                # Local operator allowlist: only the benign fixture echo is
                # certified for trusted DIRECT read-only visibility.
                "trusted_direct_read_only_tools": ["safe_echo"],
                "env": {"SBX3_INGRESS_MARKER": str(marker)},
            }
        })
    assert all(k in tools for k in (BAD, ECHO, REPLAY)), (
        "HARNESS_ERROR: real stdio MCP list_tools missing process/echo exports"
    )
    return manager, SimpleNamespace(tools=tools)


async def _exercise_true_tcp(registry, capability_id, marker, *, mode, reconnect=False):
    gateway_app, catalog, connection_runtime, identity = base._build_gateway_app()
    server, server_task, port = await base._start_uvicorn(gateway_app)

    hitl = _TracingHITL()
    dispatcher = CapabilityDispatcher(registry, None, hitl=hitl)
    current = None
    client_capabilities = None
    active_connection_id = base.CONNECTION_ID
    receipts = {"tcp_registered": [], "tool_results": [], "inference": []}

    def new_client(connection_id):
        realtime = GatewayRealtimeClient(
            f"http://127.0.0.1:{port}",
            {"Authorization": "Bearer sbx3-loopback-test-only"},
            connection_id=connection_id,
            session_id=base.SESSION_ID,
            client_id=base.CLIENT_ID,
            heartbeat_interval=60.0,
            on_message=lambda env: client_capabilities.handle_message(env),
        )
        dispatcher.realtime = realtime
        runtime = ClientCapabilityRuntime(
            registry, realtime, client_id=base.CLIENT_ID,
            owner_id=base.OWNER_ID, dispatcher=dispatcher,
        )
        return realtime, runtime

    try:
        current, client_capabilities = new_client(active_connection_id)
        async def register():
            registered = await asyncio.to_thread(current.connect, wait_timeout=5.0)
            assert registered["type"] == "connection.registered", (
                "HARNESS_ERROR: real TCP connection handshake missing"
            )
            received = await asyncio.to_thread(client_capabilities.register, timeout=5.0)
            assert received["type"] == "capability.registered"
            impl_id = f"{active_connection_id}:{capability_id}"
            await base._wait_until_async(
                lambda: catalog.get_implementation(impl_id) is not None,
                timeout=5.0,
            )
            implementation = catalog.get_implementation(impl_id)
            assert implementation.connection_id == active_connection_id
            receipts["tcp_registered"].append(impl_id)

        await register()

        server_registry = CapabilityRegistry()
        server_runtime = CapabilityRuntime(
            registry=server_registry,
            authorization=AuthorizationService(),
            catalog=catalog,
            routing_policy=CapabilityRoutingPolicy(
                connection_availability=connection_runtime.registry,
            ),
            connection_registry=connection_runtime.registry,
            realtime=connection_runtime.realtime,
        )

        async def agent_once(execution_id):
            name = "sbx3-ingress-real-tcp-agent"
            agent = AgentDefinition(
                name=name,
                goal="Request one registered remote capability.",
                instruction="Use the requested registered MCP capability.",
                tools=[capability_id],
            )
            agent_registry = AgentRegistry()
            agent_registry.register(agent)
            policy = RegistryAgentToolPolicy(
                agent_registry, server_registry, AuthorizationService(),
                capability_catalog=catalog,
            )
            assembler = DefaultAgentContextAssembler(
                DefaultAgentSystemPromptProvider(TemporalContextProvider()),
                RegistryAgentCapabilityResolver(
                    agent_registry=agent_registry,
                    capability_registry=server_registry,
                    capability_catalog=catalog,
                    tool_policy=policy,
                ),
            )
            context_builder = ContextBuilderAdapter(
                base.E2EContextRuntime(), server_runtime, policy,
                context_assembler=assembler,
            )
            tool_port = CapabilityToolExecutionAdapter(
                server_runtime, policy, base.AllowExecutionPolicy(),
            )
            context = AgentExecutionContext.create(
                execution_id=execution_id,
                agent_id=name,
                session_id=base.SESSION_ID,
                correlation_id=execution_id + "-corr",
                identity=identity,
                limits=AgentExecutionLimits(max_iterations=3),
                connection_id=active_connection_id,
                agent=agent,
                metadata={"constitution": "Report results; no native sandbox."},
            )
            model = _Model(capability_id)
            result = await AgentRuntime(
                context_builder=context_builder,
                inference=model,
                tool_execution=tool_port,
                execution_policy=base.AllowExecutionPolicy(),
            ).execute(context)
            assert model.calls == 2, (
                "HARNESS_ERROR: Agent did not consume tool result through model loop"
            )
            tool_message = model.requests[1].messages[-1]
            assert tool_message.role == "tool", (
                "HARNESS_ERROR: model not given execution tool result"
            )
            receipts["tool_results"].append(tool_message)
            receipts["inference"].append({
                "execution_id": execution_id,
                "capability_id": capability_id,
                "tool_call_id": "ing-model-tool-01",
                "connection_id": active_connection_id,
                "transport": "REAL_TCP_WEBSOCKET",
                "model": "DETERMINISTIC",
            })
            return result, tool_message

        if mode == "direct":
            # Separate, positive trusted DIRECT capability-runtime control.
            # MCP is eligible only when locally allowlisted for read-only use.
            if capability_id != ECHO:
                try:
                    await server_runtime.execute_capability(
                        capability_id=capability_id,
                        arguments={"value": "direct-deny"},
                        identity=identity,
                        session_id=base.SESSION_ID,
                        connection_id=base.CONNECTION_ID,
                        execution_id="sbx3-direct-untrusted-mcp",
                        metadata={"chat_execution_mode": "DIRECT"},
                    )
                except PermissionError as exc:
                    assert "MCP_EFFECT_CLASS_UNCERTIFIED" in str(exc)
                    assert not marker.exists()
                    assert hitl.calls == []
                    return receipts, hitl
                raise AssertionError(
                    "POLICY_RED: untrusted MCP handler bypassed DIRECT effect certification"
                )

            result = await server_runtime.execute_capability(
                capability_id=capability_id,
                arguments={"value": "direct-ok"},
                identity=identity,
                session_id=base.SESSION_ID,
                connection_id=base.CONNECTION_ID,
                execution_id="sbx3-trusted-direct-control",
                metadata={"chat_execution_mode": "DIRECT"},
            )
            assert result.success is True, (
                "COMPAT_RED: locally allowlisted DIRECT client tool did not execute: "
                + repr(result)
            )
            assert "safe:" in str(result.output), (
                "HARNESS_ERROR: real MCP echo result missing"
            )
            assert not marker.exists()
            assert len(hitl.calls) == 1, (
                "HARNESS_ERROR: DIRECT control bypassed canonical client executor"
            )
            return receipts, hitl

        result, message = await agent_once("sbx3-ing-agent-01")
        # Read physical witness FIRST, otherwise a policy assertion masks the
        # observed host child-process side effect in failing RED-first logs.
        print(
            "SBX3_AGENT_INGRESS_WITNESS",
            "capability=" + capability_id,
            "marker=" + str(marker.exists()),
            "hitl_calls=" + str(len(hitl.calls)),
            "tool_metadata=" + repr(dict(message.metadata)),
            flush=True,
        )
        assert not marker.exists(), (
            "PHYSICAL_RED: real TCP Agent triggered additional MCP child process"
        )
        assert message.metadata["success"] is False, (
            "POLICY_RED: Agent-origin renamed process capability was accepted"
        )
        assert message.metadata["retryable"] is False
        assert message.metadata.get("error_code") == "AGENT_TOOL_POLICY_DENIED" or (
            "AGENT_TOOL_POLICY_DENIED" in repr(message.content)
        ), "POLICY_RED: expected explicit pre-dispatch policy DENY"
        assert len(hitl.calls) == 0, (
            "POLICY_RED: Client HITL invoked before unauthorized Agent denial"
        )
        assert not marker.exists(), (
            "PHYSICAL_RED: real TCP Agent triggered additional MCP child process"
        )

        if reconnect:
            # Require a physical disconnect/reconnect and fresh registered
            # snapshot; a synthetic invocation replay is not allowed to
            # substitute for the real socket. Reissue same model tool-call ID.
            await asyncio.to_thread(current.close)
            # A retired connection ID is not reusable. Assert that the server
            # rejects replay of the original physical registration before
            # attempting a legitimate new transport identity.
            stale, _ = new_client(base.CONNECTION_ID)
            try:
                try:
                    await asyncio.to_thread(stale.connect, wait_timeout=5.0)
                except RealtimeHandshakeError as exc:
                    assert "Connection ID cannot be reused" in str(exc), (
                        "POLICY_RED: stale connection rejected for the wrong reason",
                        str(exc),
                    )
                    assert base.CONNECTION_ID in str(exc)
                else:
                    raise AssertionError(
                        "POLICY_RED: retired connection_id was accepted on reconnect"
                    )
            finally:
                await asyncio.to_thread(stale.close)
            assert not marker.exists(), (
                "PHYSICAL_RED: stale connection replay spawned a child"
            )

            # Reconnect as a *new* physical connection while deliberately
            # reusing the same model tool-call ID in the Agent denial test.
            active_connection_id = f"{base.CONNECTION_ID}-reconnect-01"
            current, client_capabilities = new_client(active_connection_id)
            await register()
            result2, message2 = await agent_once("sbx3-ing-agent-01")
            assert message2.metadata["success"] is False
            assert len(hitl.calls) == 0, (
                "POLICY_RED: reconnect promoted Agent request to local HITL"
            )
            assert not marker.exists(), (
                "PHYSICAL_RED: reconnect/reissue executed process tool"
            )
            assert len(receipts["tcp_registered"]) == 2

        return receipts, hitl
    finally:
        if current is not None:
            await asyncio.to_thread(current.close)
        dispatcher.shutdown()
        await base._stop_uvicorn(server, server_task)


def test_ing_t1_03_model_origin_real_tcp_to_renamed_mcp_process_denied(tmp_path):
    marker = tmp_path / "tcp-process.marker"
    manager, registry = _real_mcp_tools(marker)
    try:
        receipts, hitl = asyncio.run(
            _exercise_true_tcp(registry, BAD, marker, mode="agent")
        )
        assert receipts["tcp_registered"]
        assert receipts["inference"]
        assert hitl.calls == []
        assert not marker.exists()
    finally:
        manager.shutdown()


def test_ing_t1_04_trusted_direct_compatibility_real_tcp_positive(tmp_path):
    marker = tmp_path / "direct-compat.marker"
    manager, registry = _real_mcp_tools(marker)
    try:
        receipts, hitl = asyncio.run(
            _exercise_true_tcp(registry, ECHO, marker, mode="direct")
        )
        assert receipts["tcp_registered"]
        assert len(hitl.calls) == 1
        assert not marker.exists()
    finally:
        manager.shutdown()


def test_ing_t1_09_direct_mode_cannot_promote_uncertified_mcp_process(tmp_path):
    marker = tmp_path / "direct-uncertified-process.marker"
    manager, registry = _real_mcp_tools(marker)
    try:
        receipts, hitl = asyncio.run(
            _exercise_true_tcp(registry, BAD, marker, mode="direct")
        )
        assert receipts["tcp_registered"]
        assert hitl.calls == []
        assert not marker.exists()
    finally:
        manager.shutdown()


def test_ing_t1_07_agent_origin_disconnect_reconnect_duplicate_negative(tmp_path):
    marker = tmp_path / "reconnect-process.marker"
    manager, registry = _real_mcp_tools(marker)
    try:
        receipts, hitl = asyncio.run(
            _exercise_true_tcp(registry, REPLAY, marker, mode="agent", reconnect=True)
        )
        assert len(receipts["tcp_registered"]) == 2
        assert len(receipts["tool_results"]) == 2
        assert hitl.calls == []
        assert not marker.exists()
        # This proves reconnect/reissued model-call negative only. Genuine
        # durable R7/R12 continuation remains an independent SOURCE HOLD until
        # a concrete persisted continuation is observed and attested.
    finally:
        manager.shutdown()
