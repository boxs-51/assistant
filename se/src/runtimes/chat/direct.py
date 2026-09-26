from __future__ import annotations

import asyncio
import uuid
from typing import Any, Mapping, Sequence

from ..agent.adapters.messages import gateway_message_to_inference, jsonable
from ..agent.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceToolDefinition,
)
from ..capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityKind,
)
from ..capability.policy import CapabilityAccessProfile
from ..context.temporal import TemporalContextProvider
from .contracts import DirectChatPolicy


class DirectChatRuntime:
    """Bounded non-agent inference/tool loop with fail-closed READ visibility."""

    def __init__(
        self,
        *,
        inference,
        capability_runtime,
        temporal_context_provider=None,
        policy: DirectChatPolicy | None = None,
    ) -> None:
        self._inference = inference
        self._capability_runtime = capability_runtime
        self._temporal = temporal_context_provider or TemporalContextProvider()
        self._policy = policy or DirectChatPolicy()

    async def execute(
        self,
        *,
        messages: Sequence[Any],
        identity,
        session_id: str,
        connection_id: str | None = None,
        model: str,
        timezone_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> InferenceResponse:
        transcript = [
            item if isinstance(item, InferenceMessage)
            else gateway_message_to_inference(item)
            for item in messages
        ]
        executable = await self._capability_runtime.get_available_capabilities(
            identity,
            CapabilityAccessProfile.DIRECT_READ_ONLY,
        )
        all_authorized = await self._capability_runtime.get_available_capabilities(
            identity,
            CapabilityAccessProfile.AGENT_POLICY,
        )
        context_skills = [
            item for item in all_authorized
            if item.kind is CapabilityKind.SKILL
            and item.execution_mode is CapabilityExecutionMode.CONTEXT_ONLY
        ]
        tools = tuple(
            InferenceToolDefinition(
                name=item.capability_id,
                description=item.description,
                parameters=dict(item.parameters),
            )
            for item in executable
        )
        allowed_capability_ids = {item.capability_id for item in executable}
        skill_text = "\n\n".join(
            str(item.metadata.get("instruction", "")).strip()
            for item in context_skills
            if str(item.metadata.get("instruction", "")).strip()
        )
        execution_id = f"direct_{uuid.uuid4().hex}"
        calls_used = 0

        for iteration in range(1, self._policy.max_tool_rounds + 2):
            temporal = self._temporal.current(timezone_name)
            system_content = temporal.as_system_text()
            if skill_text:
                system_content += f"\n\n[CONTEXT SKILLS]\n{skill_text}"
            request_messages = (
                InferenceMessage(role="system", content=system_content),
                *[item for item in transcript if item.role != "system"],
            )
            response = await self._inference.complete(InferenceRequest(
                request_id=f"inf_{uuid.uuid4().hex}",
                execution_id=execution_id,
                iteration=iteration,
                messages=request_messages,
                tools=tools,
                model=model,
                owner_user_id=identity.user_id,
                metadata={**dict(metadata or {}), "chat_execution_mode": "DIRECT"},
            ))
            transcript.append(response.message)
            calls = list(response.message.tool_calls)
            if not calls:
                return response
            if iteration > self._policy.max_tool_rounds:
                raise RuntimeError("Direct chat exceeded max_tool_rounds.")
            if calls_used + len(calls) > self._policy.max_tool_calls:
                raise RuntimeError("Direct chat exceeded max_tool_calls.")
            calls_used += len(calls)

            async def run(call):
                if call.name not in allowed_capability_ids:
                    raise PermissionError(
                        f"Capability '{call.name}' is not allowed in DIRECT mode."
                    )
                result = await self._capability_runtime.execute_capability(
                    capability_id=call.name,
                    arguments=dict(call.arguments),
                    identity=identity,
                    session_id=session_id,
                    connection_id=connection_id,
                    metadata={"chat_execution_mode": "DIRECT"},
                )
                return InferenceMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.id,
                    content=jsonable(result.output),
                )

            if self._policy.allow_parallel_tools:
                results = await asyncio.gather(*(run(call) for call in calls))
            else:
                results = [await run(call) for call in calls]
            transcript.extend(results)

        raise RuntimeError("Direct chat terminated without a final response.")
