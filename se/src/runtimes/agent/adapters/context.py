from __future__ import annotations

import base64
import binascii
import hashlib
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

_F7T_SCREENSHOT_CAPABILITY_ID = "desktop.screenshot"
_F7T_INLINE_CONTRACT = "F7T_INLINE_BASE64_V1"
_F7T_SCREENSHOT_FILENAME = "desktop-screenshot.png"
_F7T_SCREENSHOT_MAX_BYTES = 8_388_608


def _project_f7t_screenshot_output(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, Mapping) or set(output) != {
        "ok", "tool", "action", "data", "error", "meta"
    }:
        return None
    if output.get("ok") is not True or output.get("error") is not None:
        return None
    if output.get("tool") != "desktop_automation" or output.get("action") != "screenshot":
        return None

    meta = output.get("meta")
    if not isinstance(meta, Mapping) or set(meta) != {"version", "truncated", "warnings"}:
        return None
    if meta.get("version") != "2.1.0" or meta.get("truncated") is not False:
        return None
    if meta.get("warnings") != []:
        return None

    data = output.get("data")
    if not isinstance(data, Mapping) or set(data) != {"$f7t_media"}:
        return None
    media = data.get("$f7t_media")
    if not isinstance(media, Mapping) or set(media) != {"contract", "items"}:
        return None
    if media.get("contract") != _F7T_INLINE_CONTRACT:
        return None
    items = media.get("items")
    if not isinstance(items, list) or len(items) != 1:
        return None
    item = items[0]
    expected_keys = {
        "ordinal", "media_kind", "mime_type", "filename", "encoding",
        "size_bytes", "sha256", "data_base64",
    }
    if not isinstance(item, Mapping) or set(item) != expected_keys:
        return None
    if (
        item.get("ordinal") != 0
        or item.get("media_kind") != "image"
        or item.get("mime_type") != "image/png"
        or item.get("filename") != _F7T_SCREENSHOT_FILENAME
        or item.get("encoding") != "base64"
    ):
        return None
    size_bytes = item.get("size_bytes")
    if type(size_bytes) is not int or not (1 <= size_bytes <= _F7T_SCREENSHOT_MAX_BYTES):
        return None
    sha256 = item.get("sha256")
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in sha256)
    ):
        return None
    data_base64 = item.get("data_base64")
    if (
        not isinstance(data_base64, str)
        or not data_base64
        or any(ch.isspace() for ch in data_base64)
    ):
        return None
    try:
        decoded = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) != size_bytes:
        return None
    if hashlib.sha256(decoded).hexdigest() != sha256:
        return None
    if base64.b64encode(decoded).decode("ascii") != data_base64:
        return None

    return {
        "$f7t_media_projection": {
            "source_contract": _F7T_INLINE_CONTRACT,
            "binary_omitted": True,
            "items": [
                {
                    "ordinal": 0,
                    "media_kind": "image",
                    "mime_type": "image/png",
                    "filename": _F7T_SCREENSHOT_FILENAME,
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                }
            ],
        }
    }


def _model_facing_success_output(capability_id: str, output: Any) -> Any:
    if capability_id != _F7T_SCREENSHOT_CAPABILITY_ID:
        return jsonable(output)
    projected = _project_f7t_screenshot_output(output)
    if projected is not None:
        return projected
    # A malformed/near-match successful screenshot result remains durable for
    # audit/recovery, but binary content must never fall through into model
    # context. Do not synthesize an alternate media projection contract.
    return "desktop.screenshot output omitted: invalid strict F7T_INLINE_BASE64_V1 envelope"


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
                        _model_facing_success_output(
                            result.capability_id,
                            result.output,
                        )
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

        if context.task_id is None and context.metadata.get("agent_time_budget_enabled"):
            task_deadline_set = context.metadata.get("task_deadline_at") is not None
            properties = {
                "iteration_seconds": {"type": "number", "exclusiveMinimum": 0},
                "inference_seconds": {"type": "number", "exclusiveMinimum": 0},
                "tool_seconds": {"type": "number", "exclusiveMinimum": 0},
            }
            if not task_deadline_set:
                properties["task_seconds"] = {"type": "number", "exclusiveMinimum": 0}
            deadline_instruction = (
                "The task deadline is already set. "
                if task_deadline_set
                else "The first call requires task_seconds. "
            )
            tools.append(InferenceToolDefinition(
                name="agent.budget.configure",
                description=(
                    deadline_instruction
                    + "Allocate time for each Agent iteration, model call, and tool call. "
                    + "Later calls may change operation budgets but cannot reset the deadline. "
                    "Use enough time for commands such as sleep 300 seconds."
                ),
                parameters={
                    "type": "object",
                    "properties": properties,
                    "required": ["task_seconds"] if not task_deadline_set else [],
                    "additionalProperties": False,
                },
            ))

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
