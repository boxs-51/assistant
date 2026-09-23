from __future__ import annotations

from typing import Any, Mapping

from ....domain.schemas.tool import GatewayToolResult
from ..contracts.context import AgentExecutionContext
from ..contracts.context_builder import (
    AgentContextHistoryMode,
    AgentContextRequest,
    AgentContextSnapshot,
    ContextBuilderPort,
)
from ..contracts.inference import InferenceMessage, InferenceToolDefinition
from ..contracts.policy import AgentToolPolicy, PolicyDecision
from .messages import gateway_message_to_inference, jsonable


class ContextBuilderAdapter(ContextBuilderPort):
    """Build one immutable Agent context snapshot from ContextRuntime."""

    def __init__(
        self,
        context_runtime: Any,
        capability_runtime: Any,
        tool_policy: AgentToolPolicy,
        context_assembler=None,
    ) -> None:
        self._context_runtime = context_runtime
        self._capability_runtime = capability_runtime
        self._tool_policy = tool_policy
        self._context_assembler = context_assembler

    async def build(
        self,
        context: AgentExecutionContext,
        request: AgentContextRequest,
    ) -> AgentContextSnapshot:
        if request.execution_id != context.execution_id:
            raise ValueError(
                "Context request execution_id does not match execution context."
            )
        if request.iteration < 1:
            raise ValueError("Context iteration must be >= 1.")
        context.ensure_active()

        history: list[InferenceMessage]
        explicit_history = (
            request.history_mode is AgentContextHistoryMode.EXPLICIT
        )
        if explicit_history or request.prior_messages:
            history = [
                InferenceMessage.model_validate(jsonable(message))
                for message in request.prior_messages
            ]
        else:
            runtime_loader = getattr(self._context_runtime, "load_context", None)
            if callable(runtime_loader):
                loaded = await runtime_loader(
                    context.session_id,
                    context.identity,
                )
            else:
                engine = getattr(self._context_runtime, "context_engine", None)
                if engine is None:
                    raise RuntimeError("ContextRuntime is not initialized.")
                loaded = await engine.load_context(
                    context.session_id,
                    context.identity,
                )
            history = [
                gateway_message_to_inference(message)
                for message in (loaded.session.messages if loaded.session else [])
            ]

        if self._context_assembler is None:
            instruction = (context.agent.instruction if context.agent else "").strip()
            if instruction and not any(
                message.role == "system" for message in history
            ):
                history.insert(0, InferenceMessage(role="system", content=instruction))

        input_payload = dict(request.input or context.input or {})
        prompt = input_payload.get("prompt", input_payload.get("content"))
        if not explicit_history and prompt is not None:
            prompt_value = jsonable(prompt)
            if not any(
                message.role == "user" and message.content == prompt_value
                for message in history
            ):
                history.append(
                    InferenceMessage(role="user", content=prompt_value)
                )

        for result in request.tool_results:
            history.append(
                InferenceMessage(
                    role="tool",
                    name=result.capability_id,
                    tool_call_id=result.tool_call_id,
                    content=(
                        jsonable(result.output)
                        if result.success
                        else result.error_message or result.error_code
                    ),
                    metadata={
                        "is_error": not result.success,
                        "error_code": result.error_code,
                    },
                )
            )

        tools: list[InferenceToolDefinition] = []
        assembly = None
        if self._context_assembler is not None:
            assembly = await self._context_assembler.assemble(
                context=context,
                prior_messages=[item.model_dump(mode="json") for item in history],
            )
            history = list(assembly.messages)
            tools = list(assembly.tools)
        else:
            registry = getattr(self._capability_runtime, "registry", None)
        if self._context_assembler is None and registry is not None and context.agent is not None:
            for capability_id in context.agent.tools or []:
                if self._tool_policy.is_visible(
                    agent_id=context.agent_id,
                    capability_id=capability_id,
                ) is not True:
                    continue
                if self._tool_policy.authorize(
                    identity=context.identity,
                    agent_id=context.agent_id,
                    capability_id=capability_id,
                ) is not PolicyDecision.ALLOW:
                    continue
                record = registry.get(capability_id)
                if record is None or not record.executable:
                    continue
                definition = record.definition
                tools.append(
                    InferenceToolDefinition(
                        name=definition.name,
                        description=definition.description,
                        parameters=dict(definition.parameters or {}),
                    )
                )

        metadata = {
            **context.metadata,
            **dict(request.metadata),
            "agent_id": context.agent_id,
            "session_id": context.session_id,
            "trace_id": context.trace_id,
        }
        if assembly is not None:
            metadata.update(
                capability_ids=[
                    item.capability_id for item in assembly.capabilities
                ],
                skill_ids=[item.skill_id for item in assembly.skills],
                system_prompt_source=assembly.system_prompt.source,
                system_prompt_version=assembly.system_prompt.version,
                constraints=dict(assembly.constraints),
            )
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=tuple(history),
            tools=tuple(tools),
            token_estimate=max(0, sum(len(str(message.content or "")) for message in history) // 4),
            metadata=metadata,
        )
