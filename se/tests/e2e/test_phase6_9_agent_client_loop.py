import asyncio
import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
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
from se.src.runtimes.capability.runtime import CapabilityRuntime

# Import trực tiếp các module có sẵn từ codebase
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.realtime import RealtimeMultiplexer
from se.src.runtimes.connection.registry import ConnectionRegistry


class FakeSocket:
    """Socket giả lập nhận message JSON từ RealtimeMultiplexer."""
    def __init__(self):
        self.messages = []

    async def send_json(self, payload: dict):
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
                    InferenceToolCall(
                        id="call-1",
                        name="desktop.echo",
                        arguments={"value": "hello"},
                    )
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


@pytest.mark.asyncio
async def test_agent_tool_loop_executes_on_client_and_returns_to_inference():
    # 1. Khởi tạo catalog và Capability
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

    # 2. Đăng ký connection sử dụng hàm sẵn có trong ConnectionRegistry
    connections = ConnectionRegistry()
    socket = FakeSocket()
    connections.register(
        session_id="sess-1",
        user_id="user-1",
        socket=socket,
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

    # 3. Chạy Agent Loop
    execution = asyncio.create_task(runtime.execute(context))

    # Chờ socket nhận tin nhắn từ RealtimeMultiplexer
    timeout_counter = 0
    while not socket.messages:
        if execution.done():
            res = execution.result()
            pytest.fail(
                f"Execution dừng sớm! State: {res.state}, Error Code: {res.error_code}, Error Msg: {res.error_message}"
            )
        await asyncio.sleep(0.01)
        timeout_counter += 1
        if timeout_counter > 200:
            pytest.fail("Timeout: Socket không nhận được message trong thời gian quy định.")

    # 4. Kiểm tra tin nhắn capability.invoke gửi tới socket
    invoke_data = socket.messages[0]
    assert invoke_data["type"] == "capability.invoke"
    assert invoke_data["connection_id"] == "conn-1"
    assert invoke_data["execution_id"] == "exec-1"

    invocation_id = invoke_data["invocation_id"]

    # 5. Phản hồi kết quả lại cho RealtimeMultiplexer bằng contract RealtimeEnvelope chuẩn
    inbound_envelope = RealtimeEnvelope(
        type="capability.result",
        message_id="result-1",
        session_id="sess-1",
        connection_id="conn-1",
        execution_id="exec-1",
        invocation_id=invocation_id,
        payload={"value": "hello"},
    )
    handled = await realtime.handle_inbound("conn-1", inbound_envelope)
    assert handled is True

    # 6. Kiểm tra kết quả hoàn tất Agent Runtime Loop
    result = await asyncio.wait_for(execution, timeout=2.0)
    assert result.output == "done"
    assert result.state.value == "COMPLETED"