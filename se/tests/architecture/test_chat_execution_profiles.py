from datetime import datetime, timedelta, timezone

import pytest

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.request import GatewayChatRequest
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.chat.direct import DirectChatRuntime
from se.src.runtimes.context.temporal import TemporalContext, TemporalContextProvider


class _Inference:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            message = InferenceMessage(
                role="assistant",
                tool_calls=(InferenceToolCall(id="call-1", name="repo.read", arguments={}),),
            )
        else:
            message = InferenceMessage(role="assistant", content="done")
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=message,
            provider="mock",
            model="mock-chat",
        )


class _ForbiddenInference:
    async def complete(self, request):
        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role="assistant",
                tool_calls=(InferenceToolCall(id="bad", name="repo.write", arguments={}),),
            ),
            provider="mock",
            model="mock-chat",
        )


class _Temporal:
    def __init__(self):
        self.calls = 0

    def current(self, timezone_name=None):
        self.calls += 1
        now = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=self.calls)
        return TemporalContext(
            current_time=now,
            current_date=now.date().isoformat(),
            timezone="UTC",
            utc_offset="+00:00",
        )


def _definition(name, *, effects, kind=CapabilityKind.TOOL, mode=CapabilityExecutionMode.ONE_SHOT, metadata=None):
    return CapabilityDefinition(
        id=name,
        name=name,
        description=name,
        input_schema={"type": "object"},
        effects=set(effects),
        kind=kind,
        execution_mode=mode,
        metadata=metadata or {},
    )


def test_transport_normalizes_agent_enabled_to_canonical_mode():
    direct = GatewayChatRequest(model="mock", messages=[{"role": "user", "content": "hi"}])
    agent = GatewayChatRequest(model="mock", messages=[{"role": "user", "content": "hi"}], agent_enabled=True)
    assert direct.execution_mode.value == "DIRECT"
    assert agent.execution_mode.value == "AGENT"


@pytest.mark.asyncio
async def test_direct_runtime_exposes_only_read_effects_and_refreshes_time_each_inference():
    capabilities = CapabilityRuntime()
    capabilities.register_capability(PythonCapabilityDriver(
        _definition("repo.read", effects={CapabilityEffect.READ}), lambda: {"ok": True}
    ))
    capabilities.register_capability(PythonCapabilityDriver(
        _definition("repo.write", effects={CapabilityEffect.WRITE}), lambda: None
    ))
    capabilities.register_capability(PythonCapabilityDriver(
        _definition(
            "guidelines",
            effects=set(),
            kind=CapabilityKind.SKILL,
            mode=CapabilityExecutionMode.CONTEXT_ONLY,
            metadata={"instruction": "Follow repository guidelines."},
        ),
        lambda: None,
    ))
    inference = _Inference()
    temporal = _Temporal()
    runtime = DirectChatRuntime(
        inference=inference,
        capability_runtime=capabilities,
        temporal_context_provider=temporal,
    )

    response = await runtime.execute(
        messages=[{"role": "user", "content": "inspect"}],
        identity=Identity(user_id="u1", auth_type="api_key"),
        session_id="s1",
        model="mock-chat",
    )

    assert response.message.content == "done"
    assert [[tool.name for tool in request.tools] for request in inference.requests] == [
        ["repo.read"], ["repo.read"]
    ]
    assert temporal.calls == 2
    first_system = inference.requests[0].messages[0].content
    second_system = inference.requests[1].messages[0].content
    assert "Follow repository guidelines." in first_system
    assert first_system != second_system
    assert inference.requests[1].messages[-1].role == "tool"


def test_temporal_context_falls_back_to_utc_for_unknown_timezone():
    temporal = TemporalContextProvider().current("Invalid/Timezone")
    assert temporal.timezone == "UTC"
    assert temporal.utc_offset == "+00:00"


@pytest.mark.asyncio
async def test_direct_runtime_rejects_hidden_write_call_even_if_model_emits_it():
    called = False

    def write():
        nonlocal called
        called = True

    capabilities = CapabilityRuntime()
    capabilities.register_capability(PythonCapabilityDriver(
        _definition("repo.write", effects={CapabilityEffect.WRITE}), write
    ))
    runtime = DirectChatRuntime(
        inference=_ForbiddenInference(),
        capability_runtime=capabilities,
    )

    with pytest.raises(PermissionError, match="not allowed in DIRECT"):
        await runtime.execute(
            messages=[{"role": "user", "content": "write"}],
            identity=Identity(user_id="u1", auth_type="api_key"),
            session_id="s1",
            model="mock-chat",
        )
    assert called is False
