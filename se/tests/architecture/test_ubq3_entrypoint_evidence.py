from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.application.user_tool_quota import ToolQuotaAdmission
from se.src.domain.schemas.capability import CapabilityExecutionRequest
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.workflow import WorkflowDefinition, WorkflowStep
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
)
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.tool import ToolExecutionRequest
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.runtimes.capability.composition import DeclarativeWorkflowDriver
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.chat.direct import DirectChatRuntime
from se.src.transport.gateway.api.v1.capability_router import execute_capability


class _QuotaProbe:
    enabled = True

    def __init__(self) -> None:
        self.reserve_calls: list[dict] = []
        self.settle_calls: list[ToolQuotaAdmission] = []
        self.release_calls: list[ToolQuotaAdmission] = []

    async def reserve_tool_call(self, **kwargs):
        self.reserve_calls.append(dict(kwargs))
        invocation_id = str(kwargs["invocation_id"])
        return ToolQuotaAdmission(
            owner_user_id=str(kwargs["identity"].user_id),
            invocation_id=invocation_id,
            capability_id=str(kwargs["capability_id"]),
            request_fingerprint=str(kwargs["request_fingerprint"]),
            idempotency_key=f"quota:{invocation_id}",
            reservation_id=f"reservation:{invocation_id}",
            window_epoch=1,
            reservation_state="RESERVED",
        )

    async def find_tool_call_authority(self, **kwargs):
        return None

    async def recover_tool_call(self, **kwargs):
        raise AssertionError("entrypoint evidence must not use R7 continuation")

    async def settle_tool_call(self, admission):
        self.settle_calls.append(admission)
        return ToolQuotaAdmission(
            owner_user_id=admission.owner_user_id,
            invocation_id=admission.invocation_id,
            capability_id=admission.capability_id,
            request_fingerprint=admission.request_fingerprint,
            idempotency_key=admission.idempotency_key,
            reservation_id=admission.reservation_id,
            window_epoch=admission.window_epoch,
            reservation_state="SETTLED",
        )

    async def release_tool_call(self, admission):
        self.release_calls.append(admission)
        return admission


class _EchoDriver(BaseCapabilityDriver):
    def __init__(
        self,
        capability_id: str,
        *,
        kind: CapabilityKind = CapabilityKind.TOOL,
        effects: set[CapabilityEffect] | None = None,
    ) -> None:
        super().__init__(
            CapabilityDefinition(
                id=capability_id,
                name=capability_id,
                description="UBQ-3 entrypoint evidence",
                kind=kind,
                execution_mode=CapabilityExecutionMode.ONE_SHOT,
                effects=set(effects or set()),
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        )
        self.calls = 0

    async def execute(self, context, arguments):
        self.calls += 1
        return {"value": arguments["value"]}


def _identity() -> Identity:
    return Identity(user_id="user-ubq3-entry", auth_type="jwt", scopes={"*"})


def _runtime(
    capability_id: str,
    *,
    kind: CapabilityKind = CapabilityKind.TOOL,
    effects: set[CapabilityEffect] | None = None,
    authorization=None,
):
    quota = _QuotaProbe()
    runtime = CapabilityRuntime(
        authorization=authorization or AuthorizationService(),
        tool_quota_service=quota,
    )
    driver = _EchoDriver(capability_id, kind=kind, effects=effects)
    runtime.register_capability(driver)
    return runtime, driver, quota


class _AllowToolPolicy:
    def is_visible(self, *, agent_id: str, capability_id: str) -> bool:
        return True

    def authorize(
        self,
        *,
        identity: Identity,
        agent_id: str,
        capability_id: str,
    ) -> PolicyDecision:
        return PolicyDecision.ALLOW


class _AllowExecutionPolicy:
    def check_tool_call(self, context, request) -> PolicyDecision:
        return PolicyDecision.ALLOW


class _DirectInference:
    def __init__(self, capability_id: str) -> None:
        self.capability_id = capability_id
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        message = (
            InferenceMessage(
                role="assistant",
                tool_calls=(
                    InferenceToolCall(
                        id="direct-call-1",
                        name=self.capability_id,
                        arguments={"value": "direct"},
                    ),
                ),
            )
            if self.calls == 1
            else InferenceMessage(role="assistant", content="done")
        )
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            provider="mock",
            model="mock",
        )


@pytest.mark.asyncio
async def test_ubq3_agent_tool_entrypoint_charges_exactly_once() -> None:
    runtime, driver, quota = _runtime("tool.agent.entry")
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        _AllowToolPolicy(),
        _AllowExecutionPolicy(),
    )
    context = AgentExecutionContext.create(
        execution_id="exec-ubq3-agent-entry",
        agent_id="agent-ubq3",
        session_id="session-ubq3-agent",
        correlation_id="corr-ubq3-agent",
        identity=_identity(),
        limits=AgentExecutionLimits(),
        task_id=None,
    )
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id="inv-ubq3-agent-entry",
        tool_call_id="call-ubq3-agent-entry",
        capability_id=driver.name,
        arguments={"value": "agent"},
    )

    result = await adapter.execute(context, request)

    assert result.success is True
    assert driver.calls == 1
    assert len(quota.reserve_calls) == 1
    assert len(quota.settle_calls) == 1
    assert quota.release_calls == []


@pytest.mark.asyncio
async def test_ubq3_direct_capability_api_charges_exactly_once() -> None:
    runtime, driver, quota = _runtime("tool.api.entry")
    result = await execute_capability(
        capability_id=driver.name,
        body=CapabilityExecutionRequest(
            arguments={"value": "api"},
            invocation_id="inv-ubq3-api-entry",
        ),
        identity=_identity(),
        container=SimpleNamespace(capability_runtime=runtime),
    )

    assert result.output["value"] == "api"
    assert driver.calls == 1
    assert len(quota.reserve_calls) == 1
    assert len(quota.settle_calls) == 1


@pytest.mark.asyncio
async def test_ubq3_direct_chat_tool_charges_exactly_once() -> None:
    runtime, driver, quota = _runtime(
        "tool.direct.entry",
        effects={CapabilityEffect.READ},
    )
    direct = DirectChatRuntime(
        inference=_DirectInference(driver.name),
        capability_runtime=runtime,
    )

    response = await direct.execute(
        messages=[{"role": "user", "content": "read"}],
        identity=_identity(),
        session_id="session-ubq3-direct",
        model="mock",
    )

    assert response.message.content == "done"
    assert driver.calls == 1
    assert len(quota.reserve_calls) == 1
    assert len(quota.settle_calls) == 1


@pytest.mark.asyncio
async def test_ubq3_declarative_workflow_charges_each_nested_tool_once() -> None:
    quota = _QuotaProbe()
    runtime = CapabilityRuntime(tool_quota_service=quota)
    first = _EchoDriver("tool.workflow.first")
    second = _EchoDriver("tool.workflow.second")
    runtime.register_capability(first)
    runtime.register_capability(second)

    workflow = WorkflowDefinition(
        steps=[
            WorkflowStep(
                step_id="one",
                tool_name=first.name,
                arguments={"value": "{{initial_input.value}}"},
            ),
            WorkflowStep(
                step_id="two",
                tool_name=second.name,
                arguments={"value": "{{steps.one.value}}"},
            ),
        ]
    )
    driver = DeclarativeWorkflowDriver(
        CapabilityDefinition(
            id="workflow.ubq3",
            name="workflow.ubq3",
            description="UBQ-3 workflow evidence",
            input_schema={"type": "object"},
        ),
        workflow,
        runtime,
    )

    result = await driver.execute(
        CapabilityExecutionContext.create(
            identity=_identity(),
            execution_id="exec-ubq3-workflow",
            invocation_id="inv-ubq3-workflow-root",
            workflow_id="workflow.ubq3",
        ),
        {"value": "workflow"},
    )

    assert result == {"value": "workflow"}
    assert first.calls == 1
    assert second.calls == 1
    assert len(quota.reserve_calls) == 2
    assert len(quota.settle_calls) == 2
    assert len({call["invocation_id"] for call in quota.reserve_calls}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CapabilityKind.SKILL, CapabilityKind.AGENT])
async def test_ubq3_non_tool_kinds_do_not_consume_tool_quota(kind) -> None:
    runtime, driver, quota = _runtime(
        f"capability.{kind.value.lower()}.entry",
        kind=kind,
    )

    result = await runtime.execute_capability(
        driver.name,
        {"value": kind.value},
        _identity(),
        invocation_id=f"inv-{kind.value.lower()}-no-tool-quota",
    )

    assert result.output["value"] == kind.value
    assert driver.calls == 1
    assert quota.reserve_calls == []
    assert quota.settle_calls == []
    assert quota.release_calls == []


@pytest.mark.asyncio
async def test_ubq3_unauthorized_tool_is_rejected_before_quota_mutation() -> None:
    class _DenyAuthorization:
        def is_allowed(self, identity, capability) -> bool:
            return False

    runtime, driver, quota = _runtime(
        "tool.unauthorized.entry",
        authorization=_DenyAuthorization(),
    )

    with pytest.raises(PermissionError):
        await runtime.execute_capability(
            driver.name,
            {"value": "denied"},
            _identity(),
            invocation_id="inv-ubq3-unauthorized",
        )

    assert driver.calls == 0
    assert quota.reserve_calls == []
    assert quota.settle_calls == []
    assert quota.release_calls == []
