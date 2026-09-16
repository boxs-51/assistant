import asyncio
import pytest
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceUsage,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.runtimes.connection.realtime import RealtimeMultiplexer


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


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


class FakeInference:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            message = InferenceMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "desktop.echo",
                        "arguments": {"value": "hello"},
                    }
                ],
            )
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=message,
                finish_reason="tool_calls",
                usage=InferenceUsage(),
                provider="fake",
                model=request.model or "fake",
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
            provider="fake",
            model=request.model or "fake",
            metadata={},
        )


class FakeContextBuilder:
    async def build(self, context, request):
        class Snapshot:
            messages = list(request.prior_messages)
            tools = [
                {
                    "name": "desktop.echo",
                    "description": "Remote echo",
                    "parameters": {"type": "object"},
                }
            ]
            metadata = {"connection_id": context.connection_id}
        return Snapshot()


def test_agent_tool_loop_executes_on_client_and_returns_to_inference():
    async def scenario():
        catalog = CapabilityCatalog()
        definition = CapabilityDefinition(
            id="desktop.echo",
            name="desktop.echo",
            description="Remote echo",
            input_schema={"type": "object"},
        )
        catalog.register_definition(definition)
        implementation = CapabilityImplementation.from_definition(
            definition,
            implementation_id="desktop-01:desktop.echo",
            location=CapabilityExecutionLocation.CLIENT,
            driver_kind="REMOTE_CLIENT",
            owner_type=CapabilityOwnerType.CLIENT,
            owner_id="user-1",
            connection_id="conn-1",
        )
        catalog.register_implementation(implementation)
        catalog.transition_implementation(
            implementation.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

        connections = ConnectionRegistry()
        socket = FakeSocket()
        connections.register(
            "sess-1",
            "user-1",
            socket,
            connection_id="conn-1",
        )
        connections.activate("conn-1")
        realtime = RealtimeMultiplexer(connections)
        capability_runtime = CapabilityRuntime(
            authorization=AuthorizationService(),
            catalog=catalog,
            routing_policy=CapabilityRoutingPolicy(
                connection_availability=connections,
            ),
            connection_registry=connections,
            realtime=realtime,
        )
        tool_port = CapabilityToolExecutionAdapter(
            capability_runtime,
            AllowToolPolicy(),
            AllowExecutionPolicy(),
        )
        runtime = AgentRuntime(
            context_builder=FakeContextBuilder(),
            inference=FakeInference(),
            tool_execution=tool_port,
            execution_policy=AllowExecutionPolicy(),
        )
        agent = AgentDefinition(
            name="test-agent",
            goal="test",
            instruction="Use tools when necessary.",
            tools=["desktop.echo"],
        )
        context = AgentExecutionContext.create(
            execution_id="exec-1",
            agent_id="test-agent",
            session_id="sess-1",
            correlation_id="corr-1",
            identity=Identity(
                user_id="user-1",
                session_id="sess-1",
                auth_type="jwt",
            ),
            limits=AgentExecutionLimits(max_iterations=3),
            connection_id="conn-1",
            agent=agent,
        )

        execution = asyncio.create_task(runtime.execute(context))
        while not socket.messages:
            await asyncio.sleep(0)

        invoke = socket.messages[0]
        assert invoke["type"] == "capability.invoke"
        assert invoke["connection_id"] == "conn-1"
        assert invoke["execution_id"] == "exec-1"

        from se.src.runtimes.connection.protocol import RealtimeEnvelope

        invoke = socket.messages[0]
        assert invoke["type"] == "capability.invoke"
        assert invoke["connection_id"] == "conn-1"
        assert invoke["execution_id"] == "exec-1"

        # 2. Lấy invocation_id linh hoạt (tùy thuộc vào cấu trúc payload hoặc root)
        invocation_id = invoke.get("invocation_id") or invoke.get("payload", {}).get("invocation_id")

        await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="result-1",
                session_id="sess-1",
                connection_id="conn-1",
                execution_id="exec-1",
                invocation_id=invocation_id,
                payload={"value": "hello"},
            ),
        )

        try:
            result = await asyncio.wait_for(execution, timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("Test bị timeout do socket không nhận được message hoặc Future bị treo.")
        assert result.output == "done"
        assert result.state.value == "COMPLETED"
        assert context.iteration == 2

    asyncio.run(scenario())